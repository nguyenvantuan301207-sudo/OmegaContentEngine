"""Request-ID middleware.

Generates a UUID request_id for every incoming HTTP request,
binds it to the structlog context, and returns it as X-Request-ID header.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique request_id to every HTTP request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())

        # Bind request_id to structlog context for this request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Record Prometheus HTTP request count and latency using bounded route templates."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        import time

        from omega.application.observability.metrics import (
            HTTP_REQUEST_DURATION_SECONDS,
            HTTP_REQUESTS_TOTAL,
            resolve_route_template,
        )

        start_time = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start_time

        try:
            endpoint = resolve_route_template(request)
            method = (
                request.method
                if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                else "UNKNOWN"
            )
            HTTP_REQUESTS_TOTAL.labels(
                method=method, endpoint=endpoint, status_code=str(response.status_code)
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, endpoint=endpoint).observe(duration)
        except Exception:
            pass
        return response
