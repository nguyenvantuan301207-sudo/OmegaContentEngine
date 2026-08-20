"""OMEGA structured logging.

Uses structlog for JSON-formatted structured logging with correlation fields:
- request_id: per-HTTP-request UUID (API only)
- job_id: job UUID (when applicable)
- service: service name (omega-api / omega-worker)
- timestamp: ISO 8601 UTC
- level: log level
- event: what happened

SECURITY: Never log secrets, credentials, API keys, OAuth tokens, or passwords.
"""

from __future__ import annotations

import logging
import sys

import structlog


def setup_logging(log_level: str = "INFO", service_name: str = "omega-api") -> None:
    """Configure structured logging for the application."""
    log_level_int = getattr(logging, log_level.upper(), logging.INFO)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to route through structlog
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level_int)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level_int)
    root_logger.handlers = [handler]

    # Quiet noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(service: str | None = None, **kwargs: object) -> structlog.stdlib.BoundLogger:
    """Get a bound structured logger with optional context fields."""
    logger = structlog.get_logger()
    if service:
        logger = logger.bind(service=service)
    if kwargs:
        logger = logger.bind(**kwargs)
    return logger
