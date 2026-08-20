"""OMEGA FastAPI application.

App factory with lifespan handler for clean startup/shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from omega.api.errors import register_error_handlers
from omega.api.middleware import RequestIDMiddleware
from omega.api.router import api_router
from omega.config import get_settings
from omega.logging import get_logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown."""
    settings = get_settings()
    setup_logging(log_level=settings.log_level, service_name=settings.service_name)
    logger = get_logger(service=settings.service_name)
    logger.info(
        "OMEGA starting",
        environment=settings.environment,
        version=settings.app_version,
    )
    yield
    # Shutdown
    from omega.infrastructure.database import async_engine
    from omega.infrastructure.redis import redis_client

    await async_engine.dispose()
    await redis_client.aclose()
    logger.info("OMEGA shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="OMEGA API",
        description="OMEGA — Autonomous Content Operating System",
        version=settings.app_version,
        lifespan=lifespan,
    )

    # CORS — configurable origins, no wildcard default
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request-ID middleware
    app.add_middleware(RequestIDMiddleware)

    # Error handlers
    register_error_handlers(app)

    # Routes
    app.include_router(api_router)

    return app


app = create_app()
