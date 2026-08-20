"""Health endpoint — lightweight liveness probe.

GET /health returns {"status": "ok"} with no dependency checks.
Use /api/v1/system/status for deep readiness checks.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])
async def health() -> dict:
    """Lightweight liveness probe. No dependency checks."""
    return {"status": "ok"}
