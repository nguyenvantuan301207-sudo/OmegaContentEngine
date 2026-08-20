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
)
