"""API v1 router aggregator.

Mounts all v1 sub-routers.
"""

from __future__ import annotations

from fastapi import APIRouter

from omega.api.health import router as health_router
from omega.api.jobs import router as jobs_router
from omega.api.system import router as system_router

# Health is mounted at root level (/health)
# System and jobs are mounted at /api/v1/...
api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(system_router)
api_router.include_router(jobs_router)
