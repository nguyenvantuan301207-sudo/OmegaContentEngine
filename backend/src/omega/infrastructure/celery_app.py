"""Celery application configuration.

Uses Redis as both broker and result backend.
"""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from omega.config import get_settings

settings = get_settings()

DEFAULT_QUEUE = "celery"
PUBLISHER_QUEUE = "omega-publisher"
PUBLISHER_TASK_NAME = "omega.publisher.execute_publish"
GENERAL_WORKER_QUEUES = (DEFAULT_QUEUE,)
PUBLISHER_WORKER_QUEUES = (PUBLISHER_QUEUE,)
PUBLISHER_WORKER_CONCURRENCY_DEFAULT = 1
PUBLISHER_WORKER_PREFETCH_MULTIPLIER = 1
PUBLISHER_TASK_ACKS_LATE = True
PUBLISHER_TASK_REJECT_ON_WORKER_LOST = True

celery_app = Celery(
    "omega",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["omega.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_default_queue=DEFAULT_QUEUE,
    task_queues=(Queue(DEFAULT_QUEUE), Queue(PUBLISHER_QUEUE)),
    task_routes={PUBLISHER_TASK_NAME: {"queue": PUBLISHER_QUEUE}},
    task_create_missing_queues=False,
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "durable-dispatch-relay": {
            "task": "omega.dispatch.relay",
            "schedule": 5.0,
            "options": {"expires": 15},
        },
        "schedule-dispatch-sweep": {
            "task": "omega.scheduler.dispatch_sweep",
            "schedule": 10.0,
            "options": {"expires": 30},
        },
        "schedule-outbox-relay": {
            "task": "omega.scheduler.outbox_relay",
            "schedule": 5.0,
            "options": {"expires": 15},
        },
        "schedule-expiration-sweep": {
            "task": "omega.scheduler.expiration_sweep",
            "schedule": 60.0,
            "options": {"expires": 120},
        },
        "schedule-stale-dispatching-sweep": {
            "task": "omega.scheduler.stale_dispatching_sweep",
            "schedule": 120.0,
            "options": {"expires": 240},
        },
        "guardian-alert-outbox": {
            "task": "omega.guardian.process_alert_outbox",
            "schedule": 30.0,
            "options": {"expires": 60},
        },
        "publisher-handoff-sweep": {
            "task": "omega.publisher.handoff_sweep",
            "schedule": 15.0,
            "options": {"expires": 45},
        },
        "publisher-reconciliation-sweep": {
            "task": "omega.publisher.reconciliation_sweep",
            "schedule": 60.0,
            "options": {"expires": 180},
        },
        "analytics-poll-sweep": {
            "task": "omega.analytics.poll_sweep",
            "schedule": 60.0,
            "options": {"expires": 120},
        },
        "analytics-daily-reconciliation-sweep": {
            "task": "omega.analytics.daily_reconciliation_sweep",
            "schedule": 3600.0,
            "options": {"expires": 1800},
        },
        "learning-ingest-sweep": {
            "task": "omega.learning.ingest_observations_sweep",
            "schedule": 120.0,
            "options": {"expires": 240},
        },
        "autonomy-tick-sweep": {
            "task": "omega.autonomy.tick_sweep",
            "schedule": 60.0,
            "options": {"expires": 120},
        },
        "autonomy-reconciliation-sweep": {
            "task": "omega.autonomy.reconciliation_sweep",
            "schedule": 300.0,
            "options": {"expires": 600},
        },
        "autonomy-approval-expiry-sweep": {
            "task": "omega.autonomy.approval_expiry_sweep",
            "schedule": 600.0,
            "options": {"expires": 1200},
        },
    },
)


def resolve_task_queue(task_name: str) -> str:
    """Return the queue selected by canonical Celery routing for a task name."""
    route = celery_app.amqp.router.route({}, task_name)
    queue = route["queue"]
    return queue.name if hasattr(queue, "name") else str(queue)


def publisher_worker_metadata() -> dict[str, object]:
    """Expose stable read-only publisher fleet configuration for operations tooling."""
    return {
        "queue_name": PUBLISHER_QUEUE,
        "worker_role": "publisher",
        "worker_queues": PUBLISHER_WORKER_QUEUES,
        "concurrency_default": PUBLISHER_WORKER_CONCURRENCY_DEFAULT,
        "prefetch_multiplier": PUBLISHER_WORKER_PREFETCH_MULTIPLIER,
        "max_in_flight_per_worker": PUBLISHER_WORKER_CONCURRENCY_DEFAULT
        * PUBLISHER_WORKER_PREFETCH_MULTIPLIER,
        "task_name": PUBLISHER_TASK_NAME,
        "acks_late": PUBLISHER_TASK_ACKS_LATE,
        "reject_on_worker_lost": PUBLISHER_TASK_REJECT_ON_WORKER_LOST,
        "queue_backlog_count": None,
    }
