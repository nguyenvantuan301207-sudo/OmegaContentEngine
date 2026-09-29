"""Focused contracts for P19-CB2 campaign admission authority."""

import hashlib
from uuid import uuid4

import pytest

from omega.application import campaign_admission_service as service
from omega.config import Settings
from omega.domain.content_campaign import (
    ContentCampaignItemAdmissionState,
    ContentCampaignOrchestrationMode,
    ContentCampaignStatus,
    compute_campaign_plan_checksum,
)
from omega.domain.content_campaign_execution import ContentCampaignExecutionStatus


def test_runtime_enums_are_exact() -> None:
    assert {value.value for value in ContentCampaignStatus} == {
        "READY",
        "RUNNING",
        "PAUSED",
        "CANCELLING",
        "SUCCEEDED",
        "PARTIAL",
        "FAILED",
        "CANCELLED",
    }
    assert {value.value for value in ContentCampaignOrchestrationMode} == {
        "LEGACY_UPFRONT",
        "LAZY_ADMISSION_V1",
    }
    assert {value.value for value in ContentCampaignItemAdmissionState} == {
        "PENDING",
        "ADMITTED",
        "MATERIALIZED",
        "FAILED",
        "CANCELLED",
    }
    assert {value.value for value in ContentCampaignExecutionStatus} == {
        "ACTIVE",
        "MATERIALIZED",
        "CANCELLED",
    }


def test_runtime_defaults_are_safe_and_bounded() -> None:
    settings = Settings(_env_file=None)
    assert settings.campaign_orchestration_enabled is False
    assert settings.campaign_default_concurrency == 1
    assert settings.campaign_max_concurrency == 10
    assert settings.campaign_channel_max_active_missions == 1
    assert settings.campaign_reconciliation_interval_seconds == 10
    assert settings.campaign_reconciliation_batch_size == 25
    assert settings.campaign_materialization_max_attempts == 3


def test_channel_admission_lock_key_is_stable_signed_sha256() -> None:
    channel_id = uuid4()
    raw = f"campaign-channel-admission:{channel_id}".encode()
    expected = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)
    assert service.channel_admission_lock_key(channel_id) == expected
    assert service.channel_admission_lock_key(channel_id) == expected


def test_checksum_v2_includes_item_key_while_v1_remains_legacy_compatible() -> None:
    channel_id = uuid4()
    dna_id = uuid4()
    run_id = uuid4()
    decision_id = uuid4()
    candidate_id = uuid4()
    base = {
        "position": 1,
        "selection_run_id": run_id,
        "selection_decision_id": decision_id,
        "topic_candidate_id": candidate_id,
        "target_content_type": "YOUTUBE_LONGFORM",
        "planned_release_at": None,
    }
    kwargs = dict(
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        title="Plan",
        objective=None,
        priority=1,
    )
    legacy_a = compute_campaign_plan_checksum(
        **kwargs, items=[{**base, "item_key": "a"}], version=1
    )
    legacy_b = compute_campaign_plan_checksum(
        **kwargs, items=[{**base, "item_key": "b"}], version=1
    )
    lazy_a = compute_campaign_plan_checksum(**kwargs, items=[{**base, "item_key": "a"}], version=2)
    lazy_b = compute_campaign_plan_checksum(**kwargs, items=[{**base, "item_key": "b"}], version=2)
    assert legacy_a == legacy_b
    assert lazy_a != lazy_b


@pytest.mark.parametrize("code", ["40001", "40P01", "55P03", "08006"])
def test_only_recognized_database_codes_are_transient(code: str) -> None:
    class Orig:
        sqlstate = code

    from sqlalchemy.exc import OperationalError

    exc = OperationalError("statement", {}, Orig())
    assert service._transient_db_error(exc) is True


def test_unknown_exception_is_not_retryable() -> None:
    assert service._transient_db_error(RuntimeError("unknown")) is False
