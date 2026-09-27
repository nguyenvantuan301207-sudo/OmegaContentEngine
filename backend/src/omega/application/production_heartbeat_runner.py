"""ProductionHeartbeatRunner (P19-LR2).

Dedicated background daemon thread executing periodic compare-and-set
heartbeats for actively rendering workers.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable

from omega.application.production_render_lease_service import (
    HEARTBEAT_INTERVAL_SECONDS,
    WORKER_HEARTBEAT_FAILURE_ABORT_SECONDS,
    ProductionLeaseError,
    ProductionLeaseLostError,
    ProductionRenderLeaseService,
    ProductionWorkerSelfFencedError,
)

logger = logging.getLogger(__name__)


class ProductionHeartbeatRunner(threading.Thread):
    """Lightweight background thread pulsing periodic heartbeats during render execution."""

    def __init__(
        self,
        job_id: uuid.UUID,
        lease_token: uuid.UUID,
        fencing_token: int,
        session_factory: Callable[[], Any] | None = None,
        renewal_fn: Callable[[Any, uuid.UUID, uuid.UUID, int], bool] | None = None,
        interval_seconds: float = float(HEARTBEAT_INTERVAL_SECONDS),
        abort_seconds: float = float(WORKER_HEARTBEAT_FAILURE_ABORT_SECONDS),
    ) -> None:
        super().__init__(daemon=True, name=f"heartbeat-{job_id.hex[:8]}")
        self.job_id = job_id
        self.lease_token = lease_token
        self.fencing_token = fencing_token
        self._session_factory = session_factory
        self._renewal_fn = renewal_fn or ProductionRenderLeaseService.renew_lease_sync
        self.interval_seconds = interval_seconds
        self.abort_seconds = abort_seconds

        self._stop_event = threading.Event()
        self.last_successful_heartbeat = time.monotonic()
        self.lease_lost = False
        self.self_fenced = False
        self.stopped_normally = False

    def _get_session(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory()
        from omega.infrastructure.database_sync import SyncSessionLocal

        return SyncSessionLocal()

    def run(self) -> None:
        """Pulse periodic CAS updates until stopped or fenced."""
        while not self._stop_event.is_set():
            # Wait for next heartbeat interval or until stopped
            if self._stop_event.wait(self.interval_seconds):
                break

            try:
                session = self._get_session()
                try:
                    success = self._renewal_fn(
                        session, self.job_id, self.lease_token, self.fencing_token
                    )
                finally:
                    if hasattr(session, "close"):
                        session.close()

                if success:
                    self.last_successful_heartbeat = time.monotonic()
                else:
                    # CAS returned 0 rows -> Lease was lost, expired, or cancelled!
                    self.lease_lost = True
                    self._stop_event.set()
                    break

            except Exception as exc:
                logger.warning(
                    "Heartbeat pulse encountered DB error; will retry",
                    extra={"job_id": str(self.job_id), "error": str(exc)},
                )

            # Check watchdog self-fence deadline (monotonic elapsed time since last success)
            elapsed = time.monotonic() - self.last_successful_heartbeat
            if elapsed >= self.abort_seconds:
                self.self_fenced = True
                self._stop_event.set()
                logger.warning(
                    "render_worker_self_fenced",
                    extra={
                        "event": "render_worker_self_fenced",
                        "job_id": str(self.job_id),
                        "elapsed_since_success": elapsed,
                    },
                )
                break

    def stop(self, timeout: float = 5.0) -> None:
        """Signal runner to terminate and cleanly join the thread."""
        self.stopped_normally = True
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=timeout)

    def is_healthy(self) -> bool:
        """Return True if lease is active, unfenced, and thread is alive."""
        if self.lease_lost or self.self_fenced:
            return False
        if not self.stopped_normally and not self.is_alive():
            return False
        return True

    def assert_healthy(self) -> None:
        """Raise appropriate exception if lease was lost or self-fenced."""
        if self.lease_lost:
            raise ProductionLeaseLostError(
                f"Worker lost lease fence for job {self.job_id}; execution must abort."
            )
        if self.self_fenced:
            raise ProductionWorkerSelfFencedError(
                f"Worker self-fenced after {self.abort_seconds}s without successful heartbeat."
            )
        if not self.stopped_normally and not self.is_alive():
            raise ProductionLeaseError(
                f"Heartbeat runner thread for job {self.job_id} died unexpectedly."
            )
