"""Unit tests for job domain model."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from omega.domain.job import JobCreate, JobCreatedResponse, JobResponse, JobState


class TestJobState:
    """Test JobState enum completeness."""

    def test_all_states_defined(self) -> None:
        """All 7 required job states must be defined."""
        expected = {"PENDING", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "RETRY", "DEAD"}
        actual = {state.value for state in JobState}
        assert actual == expected

    def test_state_count(self) -> None:
        """Exactly 7 states."""
        assert len(JobState) == 7

    def test_state_is_string(self) -> None:
        """Job states should be string enums."""
        for state in JobState:
            assert isinstance(state.value, str)

    def test_pending_state(self) -> None:
        assert JobState.PENDING.value == "PENDING"

    def test_queued_state(self) -> None:
        assert JobState.QUEUED.value == "QUEUED"

    def test_running_state(self) -> None:
        assert JobState.RUNNING.value == "RUNNING"

    def test_succeeded_state(self) -> None:
        assert JobState.SUCCEEDED.value == "SUCCEEDED"

    def test_failed_state(self) -> None:
        assert JobState.FAILED.value == "FAILED"

    def test_retry_state(self) -> None:
        assert JobState.RETRY.value == "RETRY"

    def test_dead_state(self) -> None:
        assert JobState.DEAD.value == "DEAD"


class TestJobCreate:
    """Test job creation schema."""

    def test_defaults(self) -> None:
        """JobCreate should have sensible defaults."""
        job = JobCreate()
        assert job.job_type == "test"
        assert job.payload is None
        assert job.max_retries == 3

    def test_custom_values(self) -> None:
        """JobCreate should accept custom values."""
        job = JobCreate(job_type="custom", payload={"key": "value"}, max_retries=5)
        assert job.job_type == "custom"
        assert job.payload == {"key": "value"}
        assert job.max_retries == 5


class TestJobResponse:
    """Test job response schema."""

    def test_from_dict(self) -> None:
        """JobResponse should parse from dict."""
        now = datetime.now(UTC)
        data = {
            "id": uuid4(),
            "job_type": "test",
            "state": JobState.SUCCEEDED,
            "payload": None,
            "result": {"message": "done"},
            "error": None,
            "retry_count": 0,
            "max_retries": 3,
            "created_at": now,
            "queued_at": now,
            "started_at": now,
            "completed_at": now,
            "updated_at": now,
        }
        response = JobResponse(**data)
        assert response.state == JobState.SUCCEEDED
        assert response.result == {"message": "done"}


class TestJobCreatedResponse:
    """Test job created response schema."""

    def test_fields(self) -> None:
        """JobCreatedResponse should have job_id and state."""
        job_id = uuid4()
        response = JobCreatedResponse(job_id=job_id, state=JobState.QUEUED)
        assert response.job_id == job_id
        assert response.state == JobState.QUEUED
