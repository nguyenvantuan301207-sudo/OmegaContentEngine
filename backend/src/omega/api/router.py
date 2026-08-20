"""API v1 router aggregator.

Mounts all v1 sub-routers:
- health (liveness probe at /health)
- system (readiness probe at /api/v1/system/...)
- jobs (OMEGA-001 Foundation test jobs at /api/v1/jobs/...)
- missions (OMEGA-002 Mission Engine at /api/v1/missions/...)
- tasks (OMEGA-002 Task Engine at /api/v1/tasks/...)
- channels (OMEGA-003 Channel Manager at /api/v1/channels/...)
- topics (OMEGA-004 Topic Intelligence at /api/v1/channels/{channel_id}/topics/...)
- research (OMEGA-005 Research Engine at /api/v1/channels/{channel_id}/research/...)
"""

from __future__ import annotations

from fastapi import APIRouter

from omega.api.channels import router as channels_router
from omega.api.content import router as content_router
from omega.api.health import router as health_router
from omega.api.jobs import router as jobs_router
from omega.api.missions import router as missions_router
from omega.api.research import router as research_router
from omega.api.system import router as system_router
from omega.api.tasks import router as tasks_router
from omega.api.topics import router as topics_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(system_router)
api_router.include_router(jobs_router)
api_router.include_router(missions_router)
api_router.include_router(tasks_router)
api_router.include_router(channels_router)
api_router.include_router(topics_router)
api_router.include_router(research_router)
api_router.include_router(content_router)
