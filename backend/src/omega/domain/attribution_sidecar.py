"""Deterministic attribution sidecar and logical export-package contracts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from omega.domain.attribution_delivery import (
    AttributionDeliveryChannel,
    AttributionObligation,
    canonicalize_attribution_obligations,
)

ATTRIBUTION_SIDECAR_SCHEMA_VERSION = 1
ATTRIBUTION_EXPORT_PACKAGE_SCHEMA_VERSION = 1
_SIDECAR_FORMAT = "OMEGA_ATTRIBUTION_SIDECAR"
_PACKAGE_FORMAT = "OMEGA_ATTRIBUTION_EXPORT_PACKAGE"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _validate_sha256(value: str, field_name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be lowercase SHA-256")


class AttributionSidecar(_FrozenContract):
    """Canonical machine-readable attribution document for one artifact."""

    format: Literal["OMEGA_ATTRIBUTION_SIDECAR"] = _SIDECAR_FORMAT
    schema_version: Literal[1] = ATTRIBUTION_SIDECAR_SCHEMA_VERSION
    artifact_id: UUID
    artifact_sha256: str
    obligations: tuple[AttributionObligation, ...]

    @model_validator(mode="after")
    def validate_sidecar(self) -> AttributionSidecar:
        _validate_sha256(self.artifact_sha256, "artifact_sha256")
        if not self.obligations:
            raise ValueError("Attribution sidecar must cover at least one obligation")
        if self.obligations != canonicalize_attribution_obligations(self.obligations):
            raise ValueError("Attribution sidecar obligations are not canonically ordered")
        for obligation in self.obligations:
            if (
                obligation.artifact_id != self.artifact_id
                or obligation.artifact_sha256 != self.artifact_sha256
            ):
                raise ValueError("Attribution sidecar crossed artifact scope")
            if AttributionDeliveryChannel.EXPORT_SIDECAR not in obligation.allowed_channels:
                raise ValueError("EXPORT_SIDECAR is not allowed for every obligation")
        return self

    @property
    def obligation_ids(self) -> tuple[str, ...]:
        return tuple(sorted(item.obligation_id for item in self.obligations))

    @property
    def filename(self) -> str:
        return "attribution.json"

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class AttributionExportPackageManifest(_FrozenContract):
    """Deterministic identity for the logical media-plus-sidecar package."""

    format: Literal["OMEGA_ATTRIBUTION_EXPORT_PACKAGE"] = _PACKAGE_FORMAT
    schema_version: Literal[1] = ATTRIBUTION_EXPORT_PACKAGE_SCHEMA_VERSION
    artifact_id: UUID
    artifact_sha256: str
    media_filename: str
    sidecar_filename: str
    sidecar_sha256: str
    obligation_ids: tuple[str, ...]
    package_checksum: str

    def checksum_payload(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "artifact_id": str(self.artifact_id).lower(),
            "artifact_sha256": self.artifact_sha256,
            "media_filename": self.media_filename,
            "sidecar_filename": self.sidecar_filename,
            "sidecar_sha256": self.sidecar_sha256,
            "obligation_ids": list(self.obligation_ids),
        }

    @model_validator(mode="after")
    def validate_manifest(self) -> AttributionExportPackageManifest:
        _validate_sha256(self.artifact_sha256, "artifact_sha256")
        _validate_sha256(self.sidecar_sha256, "sidecar_sha256")
        _validate_sha256(self.package_checksum, "package_checksum")
        if self.media_filename != "media.mp4":
            raise ValueError("Export media filename is not canonical")
        if self.sidecar_filename != "attribution.json":
            raise ValueError("Export sidecar filename is not canonical")
        if not self.obligation_ids:
            raise ValueError("Export package must cover at least one obligation")
        if self.obligation_ids != tuple(sorted(set(self.obligation_ids))):
            raise ValueError("obligation_ids must be unique and canonically ordered")
        expected = hashlib.sha256(_canonical_json_bytes(self.checksum_payload())).hexdigest()
        if self.package_checksum != expected:
            raise ValueError("package_checksum does not match canonical package facts")
        return self


def create_attribution_sidecar(
    *,
    artifact_id: UUID,
    artifact_sha256: str,
    obligations: list[AttributionObligation] | tuple[AttributionObligation, ...],
) -> AttributionSidecar:
    """Build one canonical, artifact-scoped EXPORT_SIDECAR document."""
    return AttributionSidecar(
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
        obligations=canonicalize_attribution_obligations(obligations),
    )


def parse_attribution_sidecar(data: bytes) -> AttributionSidecar:
    """Parse a sidecar and reject any non-canonical or tampered byte representation."""
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Attribution sidecar is not valid UTF-8 JSON") from exc
    sidecar = AttributionSidecar.model_validate(raw)
    if data != sidecar.canonical_bytes():
        raise ValueError("Attribution sidecar bytes are not canonical")
    return sidecar


def create_export_package_manifest(
    sidecar: AttributionSidecar,
) -> AttributionExportPackageManifest:
    """Bind media, sidecar, and exact obligation coverage into one package checksum."""
    artifact_name = str(sidecar.artifact_id).lower()
    values: dict[str, Any] = {
        "format": _PACKAGE_FORMAT,
        "schema_version": ATTRIBUTION_EXPORT_PACKAGE_SCHEMA_VERSION,
        "artifact_id": sidecar.artifact_id,
        "artifact_sha256": sidecar.artifact_sha256,
        "media_filename": "media.mp4",
        "sidecar_filename": sidecar.filename,
        "sidecar_sha256": sidecar.sha256,
        "obligation_ids": sidecar.obligation_ids,
    }
    checksum_payload = {
        **values,
        "artifact_id": artifact_name,
        "obligation_ids": list(sidecar.obligation_ids),
    }
    return AttributionExportPackageManifest(
        package_checksum=hashlib.sha256(
            _canonical_json_bytes(checksum_payload)
        ).hexdigest(),
        **values,
    )


__all__ = [
    "ATTRIBUTION_EXPORT_PACKAGE_SCHEMA_VERSION",
    "ATTRIBUTION_SIDECAR_SCHEMA_VERSION",
    "AttributionExportPackageManifest",
    "AttributionSidecar",
    "create_attribution_sidecar",
    "create_export_package_manifest",
    "parse_attribution_sidecar",
]
