"""Job API endpoints.

POST /api/v1/jobs/test  — create and dispatch a test job
GET  /api/v1/jobs/{job_id} — retrieve job status
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.job_service import create_test_job, get_job
from omega.domain.job import JobCreatedResponse, JobResponse

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.post("/test", response_model=JobCreatedResponse, status_code=201)
async def create_test_job_endpoint(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> JobCreatedResponse:
    """Create a test job and dispatch it to the worker."""
    return await create_test_job(session)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job_endpoint(
    job_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> JobResponse:
    """Retrieve a job by ID."""
    job = await get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

