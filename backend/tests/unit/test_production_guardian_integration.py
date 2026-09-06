import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omega.application.guardian.detectors.base import GuardianEvaluationContext
from omega.application.guardian.detectors.content_quality import (
    ContentQualityDetector,
)
from omega.application.guardian.detectors.policy_risk import PolicyRiskDetector
from omega.application.render_service import ProductionRenderService
from omega.domain.guardian import (
    CheckTriggerType,
    GuardianAction,
    GuardianCheckpoint,
)
from omega.domain.production import ProductionQAStatus, RenderErrorCode


class AsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def result_with_scalar(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_content_quality_uses_contradictions_and_request_duration():
    detector = ContentQualityDetector()

    mission_id = uuid.uuid4()
    research_brief_id = uuid.uuid4()

    context = GuardianEvaluationContext(
        mission_id=mission_id,
        checkpoint=GuardianCheckpoint.PRE_RENDER,
        trigger_type=CheckTriggerType.PRE_RENDER,
    )

    mission = SimpleNamespace(
        id=mission_id,
        channel_id=None,
    )

    content_request = SimpleNamespace(
        research_brief_id=research_brief_id,
        target_duration_seconds=120,
    )

    script_version = SimpleNamespace(
        hook_text="hook",
        closing_text="close",
        cta_text="cta",
        estimated_duration_seconds=60,
        sections=[],
        content_request=content_request,
    )

    brief = SimpleNamespace(
        contradictions=[
            {
                "severity": "LOW",
                "claim_id": "c1",
            }
        ]
    )

    session = AsyncMock()
    session.execute.side_effect = [
        result_with_scalar(mission),
        result_with_scalar(script_version),
        result_with_scalar(brief),
    ]

    def factory():
        return AsyncSessionContext(session)

    with patch(
        "omega.application.guardian.detectors.content_quality."
        "ContentQAAdapter.evaluate"
    ) as evaluate:
        evaluate.return_value = []

        await detector.evaluate(context, factory)

    args = evaluate.call_args.args

    assert args[1] == 120
    assert args[3] == {
        "contradictions": [
            {
                "severity": "LOW",
                "claim_id": "c1",
            }
        ]
    }


@pytest.mark.asyncio
async def test_policy_risk_db_asset_unknown_commercial_use():
    detector = PolicyRiskDetector()

    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        production_request_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.PRE_RENDER,
        trigger_type=CheckTriggerType.PRE_RENDER,
    )

    # Intentionally has NO metadata_ attribute.
    asset = SimpleNamespace(
        id=uuid.uuid4(),
        source_ref="pexels:test",
        license_status="UNKNOWN",
        asset_requirement_id=uuid.uuid4(),
    )

    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = [asset]

    session = AsyncMock()
    session.execute.return_value = db_result

    findings = await detector.evaluate(
        context,
        lambda: AsyncSessionContext(session),
    )

    rules = {finding.rule_id for finding in findings}

    assert "UNRESOLVED_ASSET_LICENSE" in rules
    assert "COMMERCIAL_USE_INCOMPATIBLE" not in rules


def make_pre_render_job():
    req = SimpleNamespace(mode="MISSION_EXECUTION")
    return SimpleNamespace(
        state="QUEUED",
        production_request=req,
    )


@pytest.mark.asyncio
async def test_pre_render_non_allow_fails_closed_before_render():
    service = ProductionRenderService()

    session = AsyncMock()
    session.execute.return_value = result_with_scalar(
        make_pre_render_job()
    )

    mission_id = uuid.uuid4()

    check = SimpleNamespace(
        decision=SimpleNamespace(
            action=GuardianAction.PAUSE,
            reason="test block",
        )
    )

    engine = MagicMock()
    engine.execute_check = AsyncMock(return_value=check)

    record_failure = AsyncMock()
    render_backend = AsyncMock()

    with (
        patch.object(
            service,
            "_resolve_mission_id",
            new=AsyncMock(return_value=mission_id),
        ),
        patch.object(
            service,
            "_record_job_failure",
            new=record_failure,
        ),
        patch.object(
            service,
            "_render_v2_staging",
            new=render_backend,
        ),
        patch(
            "omega.application.guardian.engine.GuardianEngine",
            return_value=engine,
        ),
    ):
        result, status = await service.execute_render_job(
            session=session,
            channel_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
        )

    assert result is None
    assert status == ProductionQAStatus.BLOCKED

    record_failure.assert_awaited_once()
    failure_args = record_failure.await_args.args

    assert failure_args[2] == RenderErrorCode.INPUT_INVALID
    assert "Guardian PRE_RENDER held: test block" in failure_args[3]

    render_backend.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_render_missing_decision_fails_closed():
    service = ProductionRenderService()

    session = AsyncMock()
    session.execute.return_value = result_with_scalar(
        make_pre_render_job()
    )

    engine = MagicMock()
    engine.execute_check = AsyncMock(
        return_value=SimpleNamespace(decision=None)
    )

    record_failure = AsyncMock()
    render_backend = AsyncMock()

    with (
        patch.object(
            service,
            "_resolve_mission_id",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch.object(
            service,
            "_record_job_failure",
            new=record_failure,
        ),
        patch.object(
            service,
            "_render_v2_staging",
            new=render_backend,
        ),
        patch(
            "omega.application.guardian.engine.GuardianEngine",
            return_value=engine,
        ),
    ):
        result, status = await service.execute_render_job(
            session=session,
            channel_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
        )

    assert result is None
    assert status == ProductionQAStatus.BLOCKED

    failure_args = record_failure.await_args.args
    assert failure_args[2] == RenderErrorCode.INPUT_INVALID
    assert "missing Guardian decision" in failure_args[3]

    render_backend.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_render_exception_fails_closed_before_render():
    service = ProductionRenderService()

    session = AsyncMock()
    session.execute.return_value = result_with_scalar(
        make_pre_render_job()
    )

    engine = MagicMock()
    engine.execute_check = AsyncMock(
        side_effect=RuntimeError("Guardian died")
    )

    record_failure = AsyncMock()
    render_backend = AsyncMock()

    with (
        patch.object(
            service,
            "_resolve_mission_id",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch.object(
            service,
            "_record_job_failure",
            new=record_failure,
        ),
        patch.object(
            service,
            "_render_v2_staging",
            new=render_backend,
        ),
        patch(
            "omega.application.guardian.engine.GuardianEngine",
            return_value=engine,
        ),
    ):
        result, status = await service.execute_render_job(
            session=session,
            channel_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
        )

    assert result is None
    assert status == ProductionQAStatus.BLOCKED

    failure_args = record_failure.await_args.args
    assert failure_args[2] == RenderErrorCode.INPUT_INVALID
    assert (
        "Guardian PRE_RENDER evaluation failed: Guardian died"
        in failure_args[3]
    )

    render_backend.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_render_payload_uses_request_target():
    service = ProductionRenderService()

    mission_id = uuid.uuid4()
    request_id = uuid.uuid4()
    artifact_id = uuid.uuid4()

    check = SimpleNamespace(
        decision=SimpleNamespace(
            action=GuardianAction.ALLOW,
            reason="ok",
        )
    )

    engine = MagicMock()
    engine.execute_check = AsyncMock(return_value=check)

    with patch(
        "omega.application.guardian.engine.GuardianEngine",
        return_value=engine,
    ):
        status = await service._evaluate_post_render_guardian(
            mission_id,
            request_id,
            artifact_id,
            {"width": 1920},
            "abc123",
            Path("test.mp4"),
            ProductionQAStatus.PASSED,
        )

    assert status == ProductionQAStatus.PASSED

    engine.execute_check.assert_awaited_once()
    payload = engine.execute_check.await_args.args[0]

    assert payload.production_request_id == request_id
    assert payload.media_artifact_id is None
    assert payload.diagnostic_context["artifact_id"] == str(artifact_id)
    assert payload.diagnostic_context["expected_hash"] == "abc123"
    assert payload.diagnostic_context["media_probe_summary"] == {
        "width": 1920
    }


@pytest.mark.asyncio
async def test_post_render_non_allow_blocks():
    service = ProductionRenderService()

    engine = MagicMock()
    engine.execute_check = AsyncMock(
        return_value=SimpleNamespace(
            decision=SimpleNamespace(
                action=GuardianAction.REQUIRE_REVIEW,
                reason="review",
            )
        )
    )

    with patch(
        "omega.application.guardian.engine.GuardianEngine",
        return_value=engine,
    ):
        status = await service._evaluate_post_render_guardian(
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            {},
            "hash",
            None,
            ProductionQAStatus.PASSED,
        )

    assert status == ProductionQAStatus.BLOCKED


@pytest.mark.asyncio
async def test_post_render_missing_decision_blocks():
    service = ProductionRenderService()

    engine = MagicMock()
    engine.execute_check = AsyncMock(
        return_value=SimpleNamespace(decision=None)
    )

    with patch(
        "omega.application.guardian.engine.GuardianEngine",
        return_value=engine,
    ):
        status = await service._evaluate_post_render_guardian(
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            {},
            "hash",
            None,
            ProductionQAStatus.PASSED,
        )

    assert status == ProductionQAStatus.BLOCKED


@pytest.mark.asyncio
async def test_post_render_exception_blocks():
    service = ProductionRenderService()

    engine = MagicMock()
    engine.execute_check = AsyncMock(
        side_effect=RuntimeError("Guardian died")
    )

    with patch(
        "omega.application.guardian.engine.GuardianEngine",
        return_value=engine,
    ):
        status = await service._evaluate_post_render_guardian(
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            {},
            "hash",
            None,
            ProductionQAStatus.PASSED_WITH_WARNINGS,
        )

    assert status == ProductionQAStatus.BLOCKED


def test_render_service_has_no_validation_failed_reference():
    source = inspect.getsource(ProductionRenderService)

    assert "RenderErrorCode.VALIDATION_FAILED" not in source
