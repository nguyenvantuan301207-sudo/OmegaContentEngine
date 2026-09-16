from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import omega.api.production as production_api
from omega.api.production import export_artifact_attribution_sidecar
from omega.application import attribution_sidecar_export as export_module
from omega.application.attribution_sidecar_export import (
    AttributionSidecarExportError,
    export_attribution_sidecar,
    validate_export_package,
)
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.production_runtime_truth import (
    RuntimeVisualTruth,
    _build_attribution_obligations,
)
from omega.domain.attribution_delivery import (
    AttributionDeliveryChannel,
    AttributionDeliveryState,
    AttributionEvidenceVerificationState,
    create_attribution_obligation,
    derive_attribution_delivery_state,
    validate_evidence_coverage,
)
from omega.domain.attribution_sidecar import (
    ATTRIBUTION_SIDECAR_SCHEMA_VERSION,
    AttributionSidecar,
    create_attribution_sidecar,
    create_export_package_manifest,
    parse_attribution_sidecar,
)

MEDIA_BYTES = b"deterministic rendered media"
MEDIA_HASH = hashlib.sha256(MEDIA_BYTES).hexdigest()
VISUAL_HASH = "b" * 64
RECORDED_AT = datetime(2026, 9, 16, tzinfo=UTC)


def _runtime_visual(
    *,
    scene_index: int = 1,
    license_status: str = "ATTRIBUTION_REQUIRED",
    attribution: str | None = "Photo by Alice",
    allowed_channels: tuple[AttributionDeliveryChannel, ...] = (),
) -> RuntimeVisualTruth:
    return RuntimeVisualTruth(
        scene_index=scene_index,
        origin="PROVIDER",
        visual_mode="PEXELS",
        kind="IMAGE",
        provider="provider-a",
        provider_asset_id=f"asset-{scene_index}",
        license_status=license_status,
        source_page_url=f"https://example.test/assets/{scene_index}",
        license_url="https://example.test/license",
        attribution=attribution,
        allowed_attribution_channels=allowed_channels,
        content_sha256=chr(ord("a") + scene_index) * 64,
    )


def test_runtime_obligation_builder_propagates_explicit_sidecar_authority():
    artifact_id = uuid4()
    attributed = _runtime_visual(
        allowed_channels=(AttributionDeliveryChannel.EXPORT_SIDECAR,),
    )
    licensed = _runtime_visual(
        scene_index=2,
        license_status="LICENSED",
        attribution=None,
    )

    obligations = _build_attribution_obligations(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        visuals=(attributed, licensed),
    )

    assert len(obligations) == 1
    obligation = obligations[0]
    assert obligation.artifact_id == artifact_id
    assert obligation.artifact_sha256 == MEDIA_HASH
    assert obligation.scene_index == attributed.scene_index
    assert obligation.provider_asset_id == attributed.provider_asset_id
    assert obligation.visual_content_sha256 == attributed.content_sha256
    assert obligation.attribution_text == attributed.attribution
    assert obligation.allowed_channels == (
        AttributionDeliveryChannel.EXPORT_SIDECAR,
    )


def test_runtime_obligation_builder_preserves_unresolved_channel_authority():
    artifact_id = uuid4()
    obligations = _build_attribution_obligations(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        visuals=(_runtime_visual(),),
    )

    assert len(obligations) == 1
    assert obligations[0].allowed_channels == ()


def test_unresolved_runtime_obligation_is_rejected_by_sidecar():
    artifact_id = uuid4()
    obligation = _build_attribution_obligations(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        visuals=(_runtime_visual(),),
    )[0]

    with pytest.raises(ValidationError, match="EXPORT_SIDECAR is not allowed"):
        create_attribution_sidecar(
            artifact_id=artifact_id,
            artifact_sha256=MEDIA_HASH,
            obligations=(obligation,),
        )


def _obligation(
    artifact_id: UUID,
    *,
    scene_index: int = 1,
    asset_id: str = "asset-1",
    text: str = "Photo by Alice",
    artifact_sha256: str = MEDIA_HASH,
):
    return create_attribution_obligation(
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
        scene_index=scene_index,
        provider="provider-a",
        provider_asset_id=asset_id,
        visual_content_sha256=VISUAL_HASH,
        source_reference="https://example.test/license",
        attribution_text=text,
        allowed_channels=(AttributionDeliveryChannel.EXPORT_SIDECAR,),
    )


def _obligations(artifact_id: UUID):
    return (
        _obligation(artifact_id, scene_index=1, asset_id="asset-1"),
        _obligation(
            artifact_id,
            scene_index=2,
            asset_id="asset-2",
            text="Image courtesy of Bob",
        ),
    )


def _write_package(tmp_path: Path, artifact_id: UUID, obligations=None):
    obligations = obligations or _obligations(artifact_id)
    sidecar = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=obligations,
    )
    manifest = create_export_package_manifest(sidecar)
    package = tmp_path / "package"
    package.mkdir()
    (package / manifest.media_filename).write_bytes(MEDIA_BYTES)
    (package / manifest.sidecar_filename).write_bytes(sidecar.canonical_bytes())
    return package, sidecar, manifest


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self):
        self.records = []
        self.commit_count = 0

    async def execute(self, _statement):
        return _Result(self.records[0] if self.records else None)

    def add(self, record):
        self.records.append(record)

    async def commit(self):
        self.commit_count += 1


def test_same_artifact_and_obligations_produce_byte_identical_sidecar():
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)

    first = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=obligations,
    )
    second = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=obligations,
    )

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.schema_version == ATTRIBUTION_SIDECAR_SCHEMA_VERSION == 1


def test_obligation_input_order_does_not_change_sidecar_or_hash():
    artifact_id = uuid4()
    one, two = _obligations(artifact_id)
    forward = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=(one, two),
    )
    reverse = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=(two, one),
    )

    assert forward.canonical_bytes() == reverse.canonical_bytes()
    assert forward.sha256 == reverse.sha256


def test_sidecar_contains_all_ids_canonical_text_and_artifact_hash():
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)
    sidecar = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=obligations,
    )

    assert sidecar.artifact_sha256 == MEDIA_HASH
    assert sidecar.obligation_ids == tuple(sorted(item.obligation_id for item in obligations))
    assert [item.attribution_text for item in sidecar.obligations] == [
        "Photo by Alice",
        "Image courtesy of Bob",
    ]


def test_sidecar_rejects_noncanonical_serialization():
    artifact_id = uuid4()
    sidecar = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=_obligations(artifact_id),
    )

    pretty = json.dumps(sidecar.model_dump(mode="json"), indent=2).encode()

    with pytest.raises(ValueError, match="not canonical"):
        parse_attribution_sidecar(pretty)


def test_package_checksum_is_deterministic_and_binds_required_facts():
    artifact_id = uuid4()
    sidecar = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=_obligations(artifact_id),
    )

    first = create_export_package_manifest(sidecar)
    second = create_export_package_manifest(sidecar)

    assert first == second
    assert first.artifact_sha256 == MEDIA_HASH
    assert first.sidecar_sha256 == sidecar.sha256
    assert first.obligation_ids == sidecar.obligation_ids


def test_media_and_sidecar_form_a_valid_complete_package(tmp_path):
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)
    package, sidecar, manifest = _write_package(tmp_path, artifact_id, obligations)

    assert validate_export_package(package, manifest, obligations) == sidecar


@pytest.mark.parametrize("missing", ["media", "sidecar"])
def test_partial_package_is_incomplete(tmp_path, missing):
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)
    package, _, manifest = _write_package(tmp_path, artifact_id, obligations)
    target = manifest.media_filename if missing == "media" else manifest.sidecar_filename
    (package / target).unlink()

    with pytest.raises(AttributionSidecarExportError, match="exactly media and sidecar"):
        validate_export_package(package, manifest, obligations)


def test_missing_required_obligation_fails_closed(tmp_path):
    artifact_id = uuid4()
    required = _obligations(artifact_id)
    package, _, manifest = _write_package(tmp_path, artifact_id, required[:1])

    with pytest.raises(AttributionSidecarExportError, match="exactly cover"):
        validate_export_package(package, manifest, required)


def test_unrelated_obligation_fails_closed(tmp_path):
    artifact_id = uuid4()
    required = _obligations(artifact_id)
    unrelated = _obligation(artifact_id, asset_id="unrelated")
    package, _, manifest = _write_package(tmp_path, artifact_id, (required[0], unrelated))

    with pytest.raises(AttributionSidecarExportError, match="exactly cover"):
        validate_export_package(package, manifest, required)


def test_wrong_artifact_hash_in_sidecar_fails_closed():
    artifact_id = uuid4()
    sidecar = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=_obligations(artifact_id),
    )
    malformed = sidecar.model_copy(update={"artifact_sha256": "0" * 64})

    with pytest.raises(ValidationError, match="artifact scope"):
        parse_attribution_sidecar(malformed.canonical_bytes())


@pytest.mark.parametrize("field", ["obligation_id", "attribution_text"])
def test_tampered_obligation_identity_or_text_fails_closed(field):
    artifact_id = uuid4()
    sidecar = create_attribution_sidecar(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        obligations=_obligations(artifact_id),
    )
    raw = json.loads(sidecar.canonical_bytes())
    raw["obligations"][0][field] = "c" * 64 if field == "obligation_id" else "Changed"
    tampered = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()

    with pytest.raises(ValidationError):
        parse_attribution_sidecar(tampered)


def test_tampered_sidecar_bytes_fail_closed(tmp_path):
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)
    package, _, manifest = _write_package(tmp_path, artifact_id, obligations)
    sidecar_path = package / manifest.sidecar_filename
    sidecar_path.write_bytes(sidecar_path.read_bytes() + b" ")

    with pytest.raises(AttributionSidecarExportError, match="sidecar hash"):
        validate_export_package(package, manifest, obligations)


def test_sidecar_hash_mismatch_fails_closed(tmp_path):
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)
    package, _, manifest = _write_package(tmp_path, artifact_id, obligations)
    malformed = manifest.model_copy(update={"sidecar_sha256": "0" * 64})

    with pytest.raises(AttributionSidecarExportError, match="sidecar hash"):
        validate_export_package(package, malformed, obligations)


def test_media_hash_mismatch_fails_closed(tmp_path):
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)
    package, _, manifest = _write_package(tmp_path, artifact_id, obligations)
    (package / manifest.media_filename).write_bytes(b"changed media")

    with pytest.raises(AttributionSidecarExportError, match="media hash"):
        validate_export_package(package, manifest, obligations)


def test_package_checksum_mismatch_fails_closed(tmp_path):
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)
    package, _, manifest = _write_package(tmp_path, artifact_id, obligations)
    malformed = manifest.model_copy(update={"package_checksum": "0" * 64})

    with pytest.raises(AttributionSidecarExportError, match="checksum"):
        validate_export_package(package, malformed, obligations)


@pytest.mark.asyncio
async def test_successful_export_persists_verified_sidecar_evidence(tmp_path):
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(MEDIA_BYTES)
    session = _Session()

    result = await export_attribution_sidecar(
        session=session,  # type: ignore[arg-type]
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        source_media_path=media_path,
        export_root=tmp_path / "exports",
        obligations=obligations,
        recorded_at=RECORDED_AT,
    )

    assert result.package_created is True
    assert result.evidence_created is True
    assert result.evidence.delivery_channel == AttributionDeliveryChannel.EXPORT_SIDECAR
    assert result.evidence.verification_state == AttributionEvidenceVerificationState.VERIFIED
    assert result.evidence.obligation_ids == result.manifest.obligation_ids
    assert result.manifest.sidecar_sha256 in result.evidence.evidence_reference
    assert result.manifest.package_checksum in result.evidence.evidence_reference
    assert session.commit_count == 1
    assert len(session.records) == 1


@pytest.mark.asyncio
async def test_source_media_hash_mismatch_creates_no_package_or_evidence(tmp_path):
    artifact_id = uuid4()
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(b"wrong")
    session = _Session()

    with pytest.raises(AttributionSidecarExportError, match="Source media hash"):
        await export_attribution_sidecar(
            session=session,  # type: ignore[arg-type]
            artifact_id=artifact_id,
            artifact_sha256=MEDIA_HASH,
            source_media_path=media_path,
            export_root=tmp_path / "exports",
            obligations=_obligations(artifact_id),
        )

    assert session.records == []
    assert not (tmp_path / "exports").exists()


@pytest.mark.asyncio
async def test_failed_write_creates_no_evidence(tmp_path, monkeypatch):
    artifact_id = uuid4()
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(MEDIA_BYTES)
    session = _Session()

    def fail_write(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(export_module, "_write_package_atomically", fail_write)
    with pytest.raises(OSError, match="disk full"):
        await export_attribution_sidecar(
            session=session,  # type: ignore[arg-type]
            artifact_id=artifact_id,
            artifact_sha256=MEDIA_HASH,
            source_media_path=media_path,
            export_root=tmp_path / "exports",
            obligations=_obligations(artifact_id),
        )

    assert session.records == []
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_failure_while_writing_sidecar_removes_partial_package_and_evidence(
    tmp_path,
    monkeypatch,
):
    artifact_id = uuid4()
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(MEDIA_BYTES)
    session = _Session()
    original = AttributionSidecar.canonical_bytes
    calls = 0

    def fail_during_sidecar_write(self):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("sidecar write failed")
        return original(self)

    monkeypatch.setattr(AttributionSidecar, "canonical_bytes", fail_during_sidecar_write)
    with pytest.raises(OSError, match="sidecar write"):
        await export_attribution_sidecar(
            session=session,  # type: ignore[arg-type]
            artifact_id=artifact_id,
            artifact_sha256=MEDIA_HASH,
            source_media_path=media_path,
            export_root=tmp_path / "exports",
            obligations=_obligations(artifact_id),
        )

    assert session.records == []
    assert session.commit_count == 0
    assert list((tmp_path / "exports").iterdir()) == []


@pytest.mark.asyncio
async def test_failed_post_write_validation_creates_no_evidence(tmp_path, monkeypatch):
    artifact_id = uuid4()
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(MEDIA_BYTES)
    session = _Session()
    original = export_module.validate_export_package
    calls = 0

    def fail_final_validation(package, manifest, obligations):
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise AttributionSidecarExportError("post-write validation failed")
        return original(package, manifest, obligations)

    monkeypatch.setattr(export_module, "validate_export_package", fail_final_validation)
    with pytest.raises(AttributionSidecarExportError, match="post-write"):
        await export_attribution_sidecar(
            session=session,  # type: ignore[arg-type]
            artifact_id=artifact_id,
            artifact_sha256=MEDIA_HASH,
            source_media_path=media_path,
            export_root=tmp_path / "exports",
            obligations=_obligations(artifact_id),
        )

    assert session.records == []
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_identical_repeat_is_idempotent_and_prior_evidence_unchanged(tmp_path):
    artifact_id = uuid4()
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(MEDIA_BYTES)
    session = _Session()
    arguments = {
        "session": session,
        "artifact_id": artifact_id,
        "artifact_sha256": MEDIA_HASH,
        "source_media_path": media_path,
        "export_root": tmp_path / "exports",
        "obligations": _obligations(artifact_id),
        "recorded_at": RECORDED_AT,
    }

    first = await export_attribution_sidecar(**arguments)  # type: ignore[arg-type]
    prior = first.evidence.model_dump()
    second = await export_attribution_sidecar(**arguments)  # type: ignore[arg-type]

    assert len(session.records) == 1
    assert session.commit_count == 1
    assert second.package_created is False
    assert second.evidence_created is False
    assert second.evidence == first.evidence
    assert first.evidence.model_dump() == prior


@pytest.mark.asyncio
async def test_evidence_for_artifact_a_cannot_satisfy_artifact_b(tmp_path):
    artifact_a = uuid4()
    artifact_b = uuid4()
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(MEDIA_BYTES)
    result = await export_attribution_sidecar(
        session=_Session(),  # type: ignore[arg-type]
        artifact_id=artifact_a,
        artifact_sha256=MEDIA_HASH,
        source_media_path=media_path,
        export_root=tmp_path / "exports",
        obligations=_obligations(artifact_a),
        recorded_at=RECORDED_AT,
    )

    with pytest.raises(ValueError, match="unknown attribution obligation"):
        validate_evidence_coverage(result.evidence, _obligations(artifact_b))


def test_legacy_empty_evidence_remains_unknown_not_verified():
    assert derive_attribution_delivery_state([]) == AttributionDeliveryState.UNKNOWN


@pytest.mark.asyncio
async def test_legacy_api_export_fails_closed_without_writing(tmp_path):
    artifact_id = uuid4()
    artifact = SimpleNamespace(
        id=artifact_id,
        content_hash=MEDIA_HASH,
        storage_uri="artifacts/source.mp4",
    )

    class LegacySession(_Session):
        async def execute(self, _statement):
            return SimpleNamespace(one_or_none=lambda: (artifact, None))

    storage = LocalMediaStorageProvider(str(tmp_path))
    with pytest.raises(HTTPException) as error:
        await export_artifact_attribution_sidecar(
            uuid4(), uuid4(), artifact_id, LegacySession(), storage
        )

    assert error.value.status_code == 409
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_api_success_returns_storage_scoped_package_receipt(tmp_path, monkeypatch):
    channel_id = uuid4()
    request_id = uuid4()
    artifact_id = uuid4()
    obligations = _obligations(artifact_id)
    storage = LocalMediaStorageProvider(str(tmp_path))
    artifacts_dir = storage.get_artifacts_dir(channel_id, request_id)
    media_path = artifacts_dir / "source.mp4"
    media_path.write_bytes(MEDIA_BYTES)
    artifact = SimpleNamespace(
        id=artifact_id,
        content_hash=MEDIA_HASH,
        storage_uri=storage.to_relative_uri(channel_id, request_id, media_path),
    )
    runtime_truth = SimpleNamespace(payload={})

    class ApiSession(_Session):
        def __init__(self):
            super().__init__()
            self.execute_count = 0

        async def execute(self, _statement):
            self.execute_count += 1
            if self.execute_count == 1:
                return SimpleNamespace(one_or_none=lambda: (artifact, runtime_truth))
            return _Result(self.records[0] if self.records else None)

    monkeypatch.setattr(
        production_api,
        "read_attribution_foundation",
        lambda _payload: (obligations, AttributionDeliveryState.UNKNOWN),
    )
    session = ApiSession()

    response = await export_artifact_attribution_sidecar(
        channel_id,
        request_id,
        artifact_id,
        session,  # type: ignore[arg-type]
        storage,
    )

    assert response.package_uri.startswith("exports/attribution/")
    assert response.evidence.delivery_channel == AttributionDeliveryChannel.EXPORT_SIDECAR
    assert response.package_checksum in response.evidence.evidence_reference
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_export_creates_no_physical_or_publish_evidence(tmp_path):
    artifact_id = uuid4()
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(MEDIA_BYTES)
    result = await export_attribution_sidecar(
        session=_Session(),  # type: ignore[arg-type]
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        source_media_path=media_path,
        export_root=tmp_path / "exports",
        obligations=_obligations(artifact_id),
        recorded_at=RECORDED_AT,
    )

    assert result.evidence.delivery_channel != AttributionDeliveryChannel.PHYSICAL_RENDER
    assert result.evidence.delivery_channel != AttributionDeliveryChannel.PUBLISH_METADATA
    assert result.evidence.target_platform is None
    assert result.evidence.target_account_id is None


def test_sidecar_contract_does_not_accept_unresolved_or_other_channels():
    artifact_id = uuid4()
    unresolved = create_attribution_obligation(
        artifact_id=artifact_id,
        artifact_sha256=MEDIA_HASH,
        scene_index=1,
        attribution_text="Photo by Alice",
    )

    with pytest.raises(ValidationError, match="not allowed"):
        AttributionSidecar(
            artifact_id=artifact_id,
            artifact_sha256=MEDIA_HASH,
            obligations=(unresolved,),
        )
