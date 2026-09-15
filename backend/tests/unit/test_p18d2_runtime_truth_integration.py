from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from omega.api.production import (
    get_artifact_qa_result,
    get_artifact_runtime_truth,
)
from omega.application.render_service import ProductionRenderService
from omega.domain.production import ProductionQAStatus


class _RuntimeResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class _QAResult:
    def __init__(self, qa):
        self._qa = qa

    def scalar_one_or_none(self):
        return self._qa


@pytest.mark.asyncio
async def test_planned_v1_v2_v3_v4_artifact_authority_lifecycle():
    """One acceptance flow proves current selection and immutable historical reads."""
    channel_id = uuid4()
    request_id = uuid4()
    render_plan_id = uuid4()
    service = ProductionRenderService()

    artifacts = []
    runtime_truth_by_artifact = {}
    qa_by_artifact = {}
    jobs = {}

    # Planned-only requests must not fabricate rendered authority.
    assert artifacts == []
    missing_runtime_session = SimpleNamespace(execute=AsyncMock(return_value=_RuntimeResult(None)))
    with pytest.raises(HTTPException) as planned_missing:
        await get_artifact_runtime_truth(channel_id, request_id, uuid4(), missing_runtime_session)
    assert planned_missing.value.status_code == 404

    async def finalize(version, qa_status):
        artifact = SimpleNamespace(
            id=uuid4(),
            production_request_id=request_id,
            render_job_id=uuid4(),
            version=version,
            is_current=False,
        )
        artifacts.append(artifact)

        async def demote_previous(_statement):
            for existing in artifacts:
                if existing.id != artifact.id:
                    existing.is_current = False

        selection_session = SimpleNamespace(
            execute=AsyncMock(side_effect=demote_previous),
            flush=AsyncMock(),
        )
        await service._apply_artifact_current_selection(
            selection_session,
            request_id=request_id,
            candidate=artifact,
            qa_status=qa_status,
        )

        jobs[artifact.render_job_id] = SimpleNamespace(
            id=artifact.render_job_id,
            render_plan_id=render_plan_id,
        )
        runtime_truth_by_artifact[artifact.id] = SimpleNamespace(
            payload={
                "schema_version": 1,
                "artifact": {
                    "media_artifact_id": str(artifact.id),
                    "version": version,
                },
            }
        )
        qa_by_artifact[artifact.id] = SimpleNamespace(
            id=uuid4(),
            production_request_id=request_id,
            artifact_id=artifact.id,
            status=qa_status.value,
            findings=[],
        )
        return artifact

    v1 = await finalize(1, ProductionQAStatus.PASSED)
    assert v1.is_current is True

    v2 = await finalize(2, ProductionQAStatus.BLOCKED)
    assert v1.is_current is True
    assert v2.is_current is False

    # A failed v3 creates no artifact, truth, or QA authority.
    v3_job_id = uuid4()
    jobs[v3_job_id] = SimpleNamespace(id=v3_job_id, state="FAILED")
    assert [artifact.version for artifact in artifacts] == [1, 2]
    assert sum(artifact.is_current for artifact in artifacts) == 1

    v4 = await finalize(4, ProductionQAStatus.PASSED)
    assert v4.is_current is True
    assert v1.is_current is False
    assert v2.is_current is False
    assert sum(artifact.is_current for artifact in artifacts) == 1

    # Every produced version owns one distinct truth row and one aligned QA row.
    assert set(runtime_truth_by_artifact) == {v1.id, v2.id, v4.id}
    assert set(qa_by_artifact) == {v1.id, v2.id, v4.id}
    assert len({id(row) for row in runtime_truth_by_artifact.values()}) == 3
    assert len({row.id for row in qa_by_artifact.values()}) == 3

    # Explicit historical/forensic selection reads each exact artifact envelope.
    for artifact in (v1, v2, v4):
        truth = runtime_truth_by_artifact[artifact.id]
        job = jobs[artifact.render_job_id]
        runtime_session = SimpleNamespace(
            execute=AsyncMock(return_value=_RuntimeResult((truth, artifact, job)))
        )
        runtime_response = await get_artifact_runtime_truth(
            channel_id, request_id, artifact.id, runtime_session
        )
        assert runtime_response.artifact_id == artifact.id
        assert runtime_response.render_version == artifact.version
        assert runtime_response.runtime_snapshot["artifact"]["media_artifact_id"] == str(
            artifact.id
        )

        runtime_params = runtime_session.execute.await_args.args[0].compile().params.values()
        assert channel_id in runtime_params
        assert request_id in runtime_params
        assert artifact.id in runtime_params

        qa_session = SimpleNamespace(
            execute=AsyncMock(return_value=_QAResult(qa_by_artifact[artifact.id]))
        )
        qa_response = await get_artifact_qa_result(channel_id, request_id, artifact.id, qa_session)
        assert qa_response.artifact_id == artifact.id
        assert qa_response.status == qa_by_artifact[artifact.id].status

        qa_params = qa_session.execute.await_args.args[0].compile().params.values()
        assert channel_id in qa_params
        assert request_id in qa_params
        assert artifact.id in qa_params

    failed_runtime_session = SimpleNamespace(execute=AsyncMock(return_value=_RuntimeResult(None)))
    with pytest.raises(HTTPException) as failed_missing:
        await get_artifact_runtime_truth(channel_id, request_id, uuid4(), failed_runtime_session)
    assert failed_missing.value.status_code == 404
