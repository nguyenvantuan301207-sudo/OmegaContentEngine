from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from omega.api.production import get_artifact_attribution_delivery_evidence
from omega.application.production_runtime_truth import read_attribution_foundation
from omega.domain.attribution_delivery import (
    AttributionDeliveryChannel,
    AttributionDeliveryEvidence,
    AttributionDeliveryState,
    AttributionEvidenceVerificationState,
    canonicalize_attribution_obligations,
    create_attribution_delivery_evidence,
    create_attribution_obligation,
    deduplicate_attribution_display,
    derive_attribution_delivery_state,
    normalize_attribution_text,
    validate_evidence_coverage,
)

ARTIFACT_HASH = "a" * 64
VISUAL_HASH = "b" * 64


def _obligation(
    *,
    artifact_id=None,
    artifact_sha256: str = ARTIFACT_HASH,
    scene_index: int = 1,
    provider_asset_id: str = "asset-1",
    attribution_text: str = "Photo by Alice",
    allowed_channels: tuple[AttributionDeliveryChannel, ...] = tuple(
        AttributionDeliveryChannel
    ),
):
    return create_attribution_obligation(
        artifact_id=artifact_id or uuid4(),
        artifact_sha256=artifact_sha256,
        scene_index=scene_index,
        provider="provider-a",
        provider_asset_id=provider_asset_id,
        visual_content_sha256=VISUAL_HASH,
        source_reference="https://example.test/license",
        attribution_text=attribution_text,
        allowed_channels=allowed_channels,
    )


def _evidence(
    obligation,
    *,
    artifact_id=None,
    artifact_sha256: str | None = None,
    channel: AttributionDeliveryChannel = AttributionDeliveryChannel.PUBLISH_METADATA,
    target_account_id: str = "account-a",
):
    publish = channel == AttributionDeliveryChannel.PUBLISH_METADATA
    return create_attribution_delivery_evidence(
        evidence_id=uuid4(),
        artifact_id=artifact_id or obligation.artifact_id,
        artifact_sha256=artifact_sha256 or obligation.artifact_sha256,
        obligation_ids=(obligation.obligation_id,),
        delivery_channel=channel,
        target_context_id="publish-intent-1" if publish else "artifact-package-1",
        target_platform="YOUTUBE" if publish else None,
        target_account_id=target_account_id if publish else None,
        delivered_text=obligation.attribution_text,
        verification_state=AttributionEvidenceVerificationState.UNVERIFIED,
        evidence_reference="receipt-pending-1",
        recorded_at=datetime(2026, 9, 16, tzinfo=UTC),
    )


def test_normalization_uses_nfc_and_preserves_non_whitespace_characters():
    decomposed = "Cafe\u0301 — © Alice"
    composed = "Café — © Alice"

    assert normalize_attribution_text(decomposed) == composed
    assert normalize_attribution_text(decomposed) == normalize_attribution_text(composed)


def test_normalization_trims_and_collapses_internal_whitespace():
    assert normalize_attribution_text("  Photo \t\n by   Alice  ") == "Photo by Alice"


def test_obligation_id_is_deterministic_for_equivalent_canonical_facts():
    artifact_id = uuid4()
    first = _obligation(
        artifact_id=artifact_id,
        attribution_text="  Cafe\u0301   by Alice ",
    )
    second = _obligation(
        artifact_id=artifact_id,
        attribution_text="Café by Alice",
    )

    assert first.obligation_id == second.obligation_id
    assert first.model_dump() == second.model_dump()


def test_canonical_order_does_not_depend_on_input_order():
    artifact_id = uuid4()
    one = _obligation(artifact_id=artifact_id, scene_index=1, provider_asset_id="z")
    two = _obligation(artifact_id=artifact_id, scene_index=2, provider_asset_id="a")

    assert canonicalize_attribution_obligations([two, one]) == (one, two)
    assert canonicalize_attribution_obligations([one, two]) == (one, two)


def test_display_dedupe_preserves_all_underlying_obligation_ids():
    artifact_id = uuid4()
    one = _obligation(artifact_id=artifact_id, scene_index=1, provider_asset_id="one")
    two = _obligation(artifact_id=artifact_id, scene_index=2, provider_asset_id="two")

    display = deduplicate_attribution_display([two, one])

    assert len(display) == 1
    assert display[0].attribution_text == "Photo by Alice"
    assert display[0].obligation_ids == (one.obligation_id, two.obligation_id)


def test_distinct_assets_with_same_text_remain_traceable():
    artifact_id = uuid4()
    one = _obligation(artifact_id=artifact_id, provider_asset_id="one")
    two = _obligation(artifact_id=artifact_id, provider_asset_id="two")

    assert one.obligation_id != two.obligation_id
    assert one.source_asset_identity != two.source_asset_identity


def test_obligation_is_bound_to_existing_artifact_identity_and_hash():
    artifact_id = uuid4()
    obligation = _obligation(artifact_id=artifact_id)

    assert obligation.artifact_id == artifact_id
    assert obligation.artifact_sha256 == ARTIFACT_HASH


def test_evidence_for_artifact_a_cannot_satisfy_artifact_b():
    obligation = _obligation()
    evidence = _evidence(obligation, artifact_id=uuid4())

    with pytest.raises(ValueError, match="artifact binding"):
        validate_evidence_coverage(evidence, [obligation])


def test_unresolved_obligation_channels_cannot_be_treated_as_delivery_success():
    obligation = create_attribution_obligation(
        artifact_id=uuid4(),
        artifact_sha256=ARTIFACT_HASH,
        scene_index=1,
        provider="provider-a",
        provider_asset_id="asset-1",
        attribution_text="Photo by Alice",
    )
    evidence = _evidence(obligation)

    with pytest.raises(ValueError, match="channels are unresolved"):
        validate_evidence_coverage(evidence, [obligation])


def test_only_three_approved_delivery_channels_are_representable():
    assert {channel.value for channel in AttributionDeliveryChannel} == {
        "PHYSICAL_RENDER",
        "PUBLISH_METADATA",
        "EXPORT_SIDECAR",
    }
    with pytest.raises(ValueError):
        AttributionDeliveryChannel("RUNTIME_TRUTH")


@pytest.mark.parametrize(
    "channel",
    [
        AttributionDeliveryChannel.PHYSICAL_RENDER,
        AttributionDeliveryChannel.PUBLISH_METADATA,
        AttributionDeliveryChannel.EXPORT_SIDECAR,
    ],
)
def test_each_approved_channel_is_supported_by_evidence_contract(channel):
    obligation = _obligation()

    assert _evidence(obligation, channel=channel).delivery_channel == channel


def test_legacy_runtime_truth_has_unknown_not_verified_delivery_state():
    obligations, state = read_attribution_foundation(
        {"schema_version": 2, "visuals": [{"attribution": "Photo by Alice"}]}
    )

    assert obligations == ()
    assert state == AttributionDeliveryState.UNKNOWN
    assert state != AttributionDeliveryState.VERIFIED
    assert derive_attribution_delivery_state([]) == AttributionDeliveryState.UNKNOWN


def test_malformed_new_runtime_foundation_fails_closed():
    with pytest.raises(ValueError, match="obligations are missing"):
        read_attribution_foundation(
            {
                "schema_version": 3,
                "attribution_delivery_state": "UNKNOWN",
            }
        )


def test_malformed_evidence_fails_closed():
    obligation = _obligation()
    evidence = _evidence(obligation)
    malformed = evidence.model_dump(mode="python")
    malformed["evidence_checksum"] = "0" * 64

    with pytest.raises(ValidationError, match="evidence_checksum"):
        AttributionDeliveryEvidence.model_validate(malformed)


def test_evidence_is_immutable_and_supersession_creates_a_new_record():
    obligation = _obligation()
    first = _evidence(obligation)
    second = create_attribution_delivery_evidence(
        evidence_id=uuid4(),
        artifact_id=first.artifact_id,
        artifact_sha256=first.artifact_sha256,
        obligation_ids=first.obligation_ids,
        delivery_channel=first.delivery_channel,
        target_context_id=first.target_context_id,
        target_platform=first.target_platform,
        target_account_id=first.target_account_id,
        delivered_text=first.delivered_text,
        verification_state=AttributionEvidenceVerificationState.REJECTED,
        evidence_reference="receipt-rejected-2",
        supersedes_evidence_id=first.evidence_id,
        recorded_at=datetime(2026, 9, 16, 0, 1, tzinfo=UTC),
    )

    with pytest.raises(ValidationError):
        first.evidence_reference = "overwritten"
    assert second.evidence_id != first.evidence_id
    assert second.supersedes_evidence_id == first.evidence_id


def test_publisher_targets_are_isolated_by_account_identity():
    obligation = _obligation()
    account_a = _evidence(obligation, target_account_id="account-a")
    account_b = _evidence(obligation, target_account_id="account-b")

    assert account_a.target_account_id != account_b.target_account_id
    assert account_a.evidence_checksum != account_b.evidence_checksum


def test_publish_metadata_evidence_requires_platform_and_account_target():
    obligation = _obligation()
    evidence = _evidence(obligation)
    malformed = evidence.model_dump(mode="python")
    malformed["target_account_id"] = None
    malformed["evidence_checksum"] = "0" * 64

    with pytest.raises(ValidationError, match="requires platform and account"):
        AttributionDeliveryEvidence.model_validate(malformed)


def test_obligations_survive_runtime_foundation_persistence_read_round_trip():
    obligation = _obligation(allowed_channels=())
    payload = {
        "schema_version": 3,
        "lineage": {"media_artifact_id": str(obligation.artifact_id)},
        "render_target": {"content_sha256": obligation.artifact_sha256},
        "visuals": [
            {
                "scene_index": obligation.scene_index,
                "origin": "PROVIDER",
                "visual_mode": "TEST",
                "provider": obligation.provider,
                "provider_asset_id": obligation.provider_asset_id,
                "license_status": "ATTRIBUTION_REQUIRED",
                "license_url": obligation.source_reference,
                "attribution": obligation.attribution_text,
                "content_sha256": obligation.visual_content_sha256,
            }
        ],
        "attribution_obligations": [obligation.model_dump(mode="json")],
        "attribution_delivery_state": "UNKNOWN",
    }

    reloaded, state = read_attribution_foundation(json.loads(json.dumps(payload)))

    assert reloaded == (obligation,)
    assert state == AttributionDeliveryState.UNKNOWN


def test_equivalent_canonical_data_produces_identical_fingerprint():
    artifact_id = uuid4()
    one = _obligation(
        artifact_id=artifact_id,
        attribution_text="Photo\u00a0by\tAlice",
    )
    two = _obligation(
        artifact_id=artifact_id,
        attribution_text="Photo by Alice",
    )

    assert one.obligation_id == two.obligation_id


@pytest.mark.asyncio
async def test_evidence_read_api_is_artifact_scoped_and_legacy_unknown():
    artifact_id = uuid4()
    artifact = SimpleNamespace(id=artifact_id, content_hash=ARTIFACT_HASH)
    artifact_result = SimpleNamespace(one_or_none=lambda: (artifact, None))
    evidence_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [])
    )
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[artifact_result, evidence_result])
    )

    response = await get_artifact_attribution_delivery_evidence(
        uuid4(), uuid4(), artifact_id, session
    )

    assert response.artifact_id == artifact_id
    assert response.artifact_sha256 == ARTIFACT_HASH
    assert response.required_obligation_ids == ()
    assert response.delivery_state == AttributionDeliveryState.UNKNOWN
    assert response.evidence == ()
    assert session.execute.await_count == 2


def test_migration_018_persists_evidence_and_rejects_identity_overwrite(
    tmp_path,
    monkeypatch,
):
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "018_create_attribution_delivery_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("omega_migration_018", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    artifact_id = uuid4()
    evidence_id = uuid4()
    obligation_id = "c" * 64
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("CREATE TABLE media_artifacts (id UUID PRIMARY KEY)"))
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        assert "attribution_delivery_evidence" in inspect(connection).get_table_names()
        connection.execute(
            text("INSERT INTO media_artifacts (id) VALUES (:id)"),
            {"id": str(artifact_id)},
        )
        values = {
            "id": str(evidence_id),
            "artifact_id": str(artifact_id),
            "artifact_sha256": ARTIFACT_HASH,
            "obligation_ids": json.dumps([obligation_id]),
            "delivery_channel": "EXPORT_SIDECAR",
            "target_context_id": "package-1",
            "delivered_text": "Photo by Alice",
            "delivered_text_sha256": "d" * 64,
            "verification_state": "UNVERIFIED",
            "evidence_reference": "pending-package-1",
            "evidence_checksum": "e" * 64,
            "schema_version": 1,
        }
        connection.execute(
            text(
                "INSERT INTO attribution_delivery_evidence "
                "(id, artifact_id, artifact_sha256, obligation_ids, "
                "delivery_channel, target_context_id, delivered_text, "
                "delivered_text_sha256, verification_state, evidence_reference, "
                "evidence_checksum, schema_version, recorded_at) VALUES "
                "(:id, :artifact_id, :artifact_sha256, :obligation_ids, "
                ":delivery_channel, :target_context_id, :delivered_text, "
                ":delivered_text_sha256, :verification_state, "
                ":evidence_reference, :evidence_checksum, :schema_version, "
                "CURRENT_TIMESTAMP)"
            ),
            values,
        )
        reloaded = connection.execute(
            text(
                "SELECT artifact_id, obligation_ids, delivery_channel "
                "FROM attribution_delivery_evidence WHERE id = :id"
            ),
            {"id": str(evidence_id)},
        ).one()
        assert str(reloaded.artifact_id) == str(artifact_id)
        assert json.loads(reloaded.obligation_ids) == [obligation_id]
        assert reloaded.delivery_channel == "EXPORT_SIDECAR"

        conflicting = dict(values)
        conflicting["delivery_channel"] = "PHYSICAL_RENDER"
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO attribution_delivery_evidence "
                    "(id, artifact_id, artifact_sha256, obligation_ids, "
                    "delivery_channel, target_context_id, delivered_text, "
                    "delivered_text_sha256, verification_state, evidence_reference, "
                    "evidence_checksum, schema_version, recorded_at) VALUES "
                    "(:id, :artifact_id, :artifact_sha256, :obligation_ids, "
                    ":delivery_channel, :target_context_id, :delivered_text, "
                    ":delivered_text_sha256, :verification_state, "
                    ":evidence_reference, :evidence_checksum, :schema_version, "
                    "CURRENT_TIMESTAMP)"
                ),
                conflicting,
            )
