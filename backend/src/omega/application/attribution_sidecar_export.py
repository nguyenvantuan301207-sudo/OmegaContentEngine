"""Atomic deterministic attribution sidecar export and evidence persistence."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.media_storage import compute_sha256
from omega.domain.attribution_delivery import (
    AttributionDeliveryChannel,
    AttributionDeliveryEvidence,
    AttributionEvidenceVerificationState,
    AttributionObligation,
    create_attribution_delivery_evidence,
    deduplicate_attribution_display,
    validate_evidence_coverage,
)
from omega.domain.attribution_sidecar import (
    AttributionExportPackageManifest,
    AttributionSidecar,
    create_attribution_sidecar,
    create_export_package_manifest,
    parse_attribution_sidecar,
)
from omega.infrastructure.models import (
    AttributionDeliveryEvidence as AttributionDeliveryEvidenceRecord,
)


class AttributionSidecarExportError(ValueError):
    """Raised when an export cannot prove a complete, valid package."""


@dataclass(frozen=True)
class AttributionSidecarExportResult:
    """Result returned only after the package and durable evidence are valid."""

    package_directory: Path
    media_path: Path
    sidecar_path: Path
    manifest: AttributionExportPackageManifest
    evidence: AttributionDeliveryEvidence
    package_created: bool
    evidence_created: bool


def _record_to_domain(record: AttributionDeliveryEvidenceRecord) -> AttributionDeliveryEvidence:
    return AttributionDeliveryEvidence(
        schema_version=record.schema_version,
        evidence_id=record.id,
        artifact_id=record.artifact_id,
        artifact_sha256=record.artifact_sha256,
        obligation_ids=tuple(record.obligation_ids),
        delivery_channel=record.delivery_channel,
        target_context_id=record.target_context_id,
        target_platform=record.target_platform,
        target_account_id=record.target_account_id,
        delivered_text=record.delivered_text,
        delivered_text_sha256=record.delivered_text_sha256,
        verification_state=record.verification_state,
        evidence_reference=record.evidence_reference,
        evidence_checksum=record.evidence_checksum,
        supersedes_evidence_id=record.supersedes_evidence_id,
        recorded_at=record.recorded_at,
    )


def validate_export_package(
    package_directory: Path,
    manifest: AttributionExportPackageManifest,
    required_obligations: list[AttributionObligation]
    | tuple[AttributionObligation, ...],
) -> AttributionSidecar:
    """Fail closed unless a directory is exactly the bound media-plus-sidecar package."""
    package_directory = package_directory.resolve()
    if not package_directory.is_dir():
        raise AttributionSidecarExportError("Attribution export package is missing")
    media_path = package_directory / manifest.media_filename
    sidecar_path = package_directory / manifest.sidecar_filename
    expected_names = {manifest.media_filename, manifest.sidecar_filename}
    entries = tuple(package_directory.iterdir())
    if {item.name for item in entries} != expected_names:
        raise AttributionSidecarExportError(
            "Attribution export package must contain exactly media and sidecar"
        )
    if any(item.is_symlink() for item in entries):
        raise AttributionSidecarExportError("Attribution export package cannot contain links")
    if not media_path.is_file() or not sidecar_path.is_file():
        raise AttributionSidecarExportError("Attribution export package is incomplete")
    if compute_sha256(media_path) != manifest.artifact_sha256:
        raise AttributionSidecarExportError("Exported media hash does not match artifact")
    sidecar_bytes = sidecar_path.read_bytes()
    if hashlib.sha256(sidecar_bytes).hexdigest() != manifest.sidecar_sha256:
        raise AttributionSidecarExportError("Exported sidecar hash does not match package")
    try:
        sidecar = parse_attribution_sidecar(sidecar_bytes)
    except ValueError as exc:
        raise AttributionSidecarExportError(str(exc)) from exc
    if (
        sidecar.artifact_id != manifest.artifact_id
        or sidecar.artifact_sha256 != manifest.artifact_sha256
        or sidecar.obligation_ids != manifest.obligation_ids
    ):
        raise AttributionSidecarExportError("Exported sidecar does not match package manifest")
    try:
        expected_sidecar = create_attribution_sidecar(
            artifact_id=manifest.artifact_id,
            artifact_sha256=manifest.artifact_sha256,
            obligations=required_obligations,
        )
    except ValueError as exc:
        raise AttributionSidecarExportError(str(exc)) from exc
    if sidecar != expected_sidecar:
        raise AttributionSidecarExportError(
            "Exported sidecar does not exactly cover required obligations"
        )
    if create_export_package_manifest(sidecar) != manifest:
        raise AttributionSidecarExportError("Export package checksum is invalid")
    return sidecar


def _write_package_atomically(
    *,
    source_media_path: Path,
    export_root: Path,
    sidecar: AttributionSidecar,
    manifest: AttributionExportPackageManifest,
) -> tuple[Path, bool]:
    export_root.mkdir(parents=True, exist_ok=True)
    export_root = export_root.resolve()
    package_directory = export_root / str(sidecar.artifact_id).lower()
    if package_directory.exists():
        if package_directory.is_symlink() or package_directory.resolve().parent != export_root:
            raise AttributionSidecarExportError("Attribution export package escaped export root")
        validate_export_package(package_directory, manifest, sidecar.obligations)
        return package_directory, False

    staging = Path(
        tempfile.mkdtemp(
            prefix=".tmp-",
            suffix=".tmp",
            dir=export_root,
        )
    )
    try:
        staged_media = staging / manifest.media_filename
        staged_sidecar = staging / manifest.sidecar_filename
        with source_media_path.open("rb") as source, staged_media.open("xb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        with staged_sidecar.open("xb") as handle:
            handle.write(sidecar.canonical_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        validate_export_package(staging, manifest, sidecar.obligations)
        try:
            staging.replace(package_directory)
        except FileExistsError:
            shutil.rmtree(staging, ignore_errors=True)
            validate_export_package(package_directory, manifest, sidecar.obligations)
            return package_directory, False
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    validate_export_package(package_directory, manifest, sidecar.obligations)
    return package_directory, True


async def export_attribution_sidecar(
    *,
    session: AsyncSession,
    artifact_id: UUID,
    artifact_sha256: str,
    source_media_path: Path,
    export_root: Path,
    obligations: list[AttributionObligation] | tuple[AttributionObligation, ...],
    recorded_at: datetime | None = None,
) -> AttributionSidecarExportResult:
    """Create and validate an export package before appending verified evidence."""
    source_media_path = source_media_path.resolve()
    if not source_media_path.is_file():
        raise AttributionSidecarExportError("Source media artifact is missing")
    if compute_sha256(source_media_path) != artifact_sha256:
        raise AttributionSidecarExportError("Source media hash does not match artifact")

    try:
        sidecar = create_attribution_sidecar(
            artifact_id=artifact_id,
            artifact_sha256=artifact_sha256,
            obligations=obligations,
        )
        manifest = create_export_package_manifest(sidecar)
    except ValueError as exc:
        raise AttributionSidecarExportError(str(exc)) from exc

    package_directory, package_created = _write_package_atomically(
        source_media_path=source_media_path,
        export_root=export_root,
        sidecar=sidecar,
        manifest=manifest,
    )
    validated_sidecar = validate_export_package(
        package_directory,
        manifest,
        obligations,
    )
    delivered_text = " | ".join(
        item.attribution_text
        for item in deduplicate_attribution_display(validated_sidecar.obligations)
    )
    evidence = create_attribution_delivery_evidence(
        evidence_id=uuid4(),
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
        obligation_ids=manifest.obligation_ids,
        delivery_channel=AttributionDeliveryChannel.EXPORT_SIDECAR,
        target_context_id=f"export-package:{manifest.package_checksum}",
        delivered_text=delivered_text,
        verification_state=AttributionEvidenceVerificationState.VERIFIED,
        evidence_reference=(
            f"sidecar-sha256:{manifest.sidecar_sha256};"
            f"package-sha256:{manifest.package_checksum}"
        ),
        recorded_at=recorded_at or datetime.now(UTC),
    )
    validate_evidence_coverage(evidence, validated_sidecar.obligations)

    existing = (
        await session.execute(
            select(AttributionDeliveryEvidenceRecord).where(
                AttributionDeliveryEvidenceRecord.evidence_checksum
                == evidence.evidence_checksum
            )
        )
    ).scalar_one_or_none()
    evidence_created = existing is None
    if existing is not None:
        persisted = _record_to_domain(existing)
        validate_evidence_coverage(persisted, validated_sidecar.obligations)
    else:
        record = AttributionDeliveryEvidenceRecord(
            id=evidence.evidence_id,
            artifact_id=evidence.artifact_id,
            artifact_sha256=evidence.artifact_sha256,
            obligation_ids=list(evidence.obligation_ids),
            delivery_channel=evidence.delivery_channel.value,
            target_context_id=evidence.target_context_id,
            target_platform=evidence.target_platform,
            target_account_id=evidence.target_account_id,
            delivered_text=evidence.delivered_text,
            delivered_text_sha256=evidence.delivered_text_sha256,
            verification_state=evidence.verification_state.value,
            evidence_reference=evidence.evidence_reference,
            evidence_checksum=evidence.evidence_checksum,
            supersedes_evidence_id=evidence.supersedes_evidence_id,
            schema_version=evidence.schema_version,
            recorded_at=evidence.recorded_at,
        )
        session.add(record)
        await session.commit()
        persisted = evidence

    return AttributionSidecarExportResult(
        package_directory=package_directory,
        media_path=package_directory / manifest.media_filename,
        sidecar_path=package_directory / manifest.sidecar_filename,
        manifest=manifest,
        evidence=persisted,
        package_created=package_created,
        evidence_created=evidence_created,
    )


__all__ = [
    "AttributionSidecarExportError",
    "AttributionSidecarExportResult",
    "export_attribution_sidecar",
    "validate_export_package",
]
