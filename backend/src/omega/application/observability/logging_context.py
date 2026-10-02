"""Correlation context helpers for structured logging across API and Worker boundaries.

Binds operational IDs into structlog contextvars and ensures context is cleanly
cleared after task execution to prevent cross-task context leakage.
Strictly forbids logging raw lease_token or secrets.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import structlog

# Allowlist of operational IDs permitted in correlation context
ALLOWED_CORRELATION_KEYS = frozenset(
    {
        "request_id",
        "mission_id",
        "mission_execution_id",
        "task_id",
        "production_request_id",
        "render_job_id",
        "dispatch_intent_id",
        "campaign_id",
        "campaign_item_id",
        "campaign_execution_id",
        "schedule_id",
        "occurrence_id",
        "worker_instance_id",
        "fencing_token",
        "dispatch_generation",
        "task_name",
    }
)


def bind_correlation_context(**kwargs: Any) -> None:
    """Bind allowed correlation keys into structlog contextvars.

    Silently ignores forbidden credential keys such as lease_token.
    """
    to_bind: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k == "lease_token":
            # RAW LEASE TOKEN MUST NEVER BE LOGGED
            continue
        if k in ALLOWED_CORRELATION_KEYS and v is not None:
            to_bind[k] = str(v)

    if to_bind:
        structlog.contextvars.bind_contextvars(**to_bind)


def clear_correlation_context() -> None:
    """Clear all structlog contextvars to prevent leakage across tasks."""
    structlog.contextvars.clear_contextvars()


@contextlib.contextmanager
def correlation_context(**kwargs: Any) -> Iterator[None]:
    """Context manager that temporarily binds correlation context and cleans up on exit."""
    # Snapshot current context
    current = structlog.contextvars.get_contextvars()
    bind_correlation_context(**kwargs)
    try:
        yield
    finally:
        structlog.contextvars.clear_contextvars()
        if current:
            structlog.contextvars.bind_contextvars(**current)
