"""Global error handlers.

Sanitizes unexpected errors before returning them to clients.
Never exposes stack traces, secrets, or internal paths.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from omega.logging import get_logger

logger = get_logger(service="omega-api")


def register_error_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": "Resource not found"},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Log full diagnostic info server-side
        logger.error(
            "Unhandled exception",
            path=str(request.url.path),
            method=request.method,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        # Return sanitized error to client
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )
