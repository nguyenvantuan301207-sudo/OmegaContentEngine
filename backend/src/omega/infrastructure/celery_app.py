"""Celery application configuration.

Uses Redis as both broker and result backend.
"""

from __future__ import annotations

from celery import Celery
import time
from typing import Any
from celery.beat import PersistentScheduler
from celery.signals import task_postrun, task_prerun, setup_logging as celery_setup_logging
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

class OmegaBeatScheduler(PersistentScheduler):
    """Custom Beat scheduler with process-local liveness and Redis loop advancement signals."""

    def tick(self, *args: Any, **kwargs: Any) -> float:
        # 1. Process-local liveness touchfile (zero external dependency)
        try:
            with open("/tmp/beat_heartbeat", "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except Exception:
            pass

        # 2. Redis loop advancement telemetry (best-effort, fail-safe)
        try:
            import redis
            sync_redis = redis.Redis.from_url(settings.redis_url, socket_timeout=0.2, socket_connect_timeout=0.2)
            sync_redis.set("omega:beat:last_tick", str(time.time()), ex=120)
            sync_redis.close()
        except Exception:
            pass

        return super().tick(*args, **kwargs)


@celery_setup_logging.connect
def configure_worker_logging(**kwargs: Any) -> None:
    from omega.logging import setup_logging
    setup_logging(settings.log_level)


@task_prerun.connect
def on_task_prerun(task_id: str, task: Any, *args: Any, **kwargs: Any) -> None:
    try:
        from omega.application.observability.logging_context import bind_correlation_context, clear_correlation_context
        clear_correlation_context()
        bind_correlation_context(task_id=task_id, task_name=getattr(task, "name", None), **{k:v for k,v in (kwargs.get("kwargs") or {}).items() if k not in {"task_id", "task_name"}})
        with open("/tmp/worker_heartbeat", "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass


@task_postrun.connect
def on_task_postrun(task_id: str, task: Any, *args: Any, **kwargs: Any) -> None:
    from omega.application.observability.logging_context import clear_correlation_context

    try:
        clear_correlation_context()
    except Exception:
        pass


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
    # ── Celery Transport Semantics (H3) ──
    # 1. Broker redelivery on ungraceful worker termination:
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 3600},
    # Note on Retry Architecture:
    # - Celery redelivery: transport-level failure recovery for unacknowledged tasks.
    # - DurableDispatch retry: transactional outbox retry with generation and backoff.
    # - RenderJob redispatch: bounded generation increments for timed-out/failed jobs.
    # - Mission retry: immutable parent-child lineage preservation across attempts.
    task_default_queue=DEFAULT_QUEUE,
    task_queues=(Queue(DEFAULT_QUEUE), Queue(PUBLISHER_QUEUE)),
    task_routes={PUBLISHER_TASK_NAME: {"queue": PUBLISHER_QUEUE}},
    task_create_missing_queues=False,
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    beat_scheduler=OmegaBeatScheduler,
    beat_max_loop_interval=5.0,
    beat_schedule={
        "durable-dispatch-relay": {
            "task": "omega.dispatch.relay",
            "schedule": 5.0,
            "options": {"expires": 15},
        },
        "campaign-reconciliation": {
            "task": "omega.campaign.reconcile",
            "schedule": float(settings.campaign_reconciliation_interval_seconds),
            "options": {"expires": settings.campaign_reconciliation_interval_seconds * 3},
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
        "production-orphan-reconciliation-sweep": {
            "task": "omega.production.reconcile_orphans_sweep",
            "schedule": 60.0,
            "options": {"expires": 120},
        },
        "production-dispatch-stall-sweep": {
            "task": "omega.production.reconcile_dispatch_stalls",
            "schedule": 60.0,
            "options": {"expires": 120},
        },
        "recurring-schedule-sweep": {
            "task": "omega.scheduler.recurring_schedule_sweep",
            "schedule": float(settings.scheduler_poll_interval_seconds),
            "options": {"expires": settings.scheduler_poll_interval_seconds * 2},
        },
        "recurring-stale-reconciliation-sweep": {
            "task": "omega.scheduler.recurring_reconcile_sweep",
            "schedule": 60.0,
            "options": {"expires": 120},
        },
        "pipeline-analytics-rollup-sweep": {
            "task": "omega.analytics.pipeline_rollup_sweep",
            "schedule": float(settings.analytics_rollup_interval_seconds),
            "options": {"expires": settings.analytics_rollup_interval_seconds * 2},
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
