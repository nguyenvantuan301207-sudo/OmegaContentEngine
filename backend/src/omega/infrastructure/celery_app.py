"""Celery application configuration.

Uses Redis as both broker and result backend.
"""

from __future__ import annotations

from celery import Celery

from omega.config import get_settings

settings = get_settings()

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
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    beat_schedule={
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
    },
)
