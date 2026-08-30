"""Guardian Subsystem API endpoints.

Exposes REST APIs for Rulesets, Checks, Status, Exceptions, Resolutions, and Cost Records.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.api.dependencies import get_db
from omega.application.guardian.engine import GuardianEngine
from omega.application.guardian.exceptions import GuardianExceptionManager
from omega.application.mission_service import resume_mission
from omega.domain.guardian import (
    CostRecordCreate,
    CostRecordResponse,
    GuardianCheckCreate,
    GuardianCheckpoint,
    GuardianCheckResponse,
    GuardianConsolidatedStatus,
    GuardianExceptionCreate,
    GuardianExceptionResponse,
    GuardianExceptionRevoke,
    GuardianGateState,
    GuardianResolutionCreate,
    GuardianResolutionResponse,
    GuardianRuleSetCreate,
    GuardianRuleSetResponse,
    RuleSetStatus,
    compute_rules_config_checksum,
)
from omega.domain.mission import MissionResponse
from omega.infrastructure.models import (
    CostRecord,
    GuardianCheck,
    GuardianException,
    GuardianResolutionEvent,
    GuardianRuleSet,
    Mission,
)

router = APIRouter(prefix="/api/v1/guardian", tags=["guardian"])


# ── Rulesets ──


@router.get("/rulesets", response_model=list[GuardianRuleSetResponse])
async def list_rulesets(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[GuardianRuleSetResponse]:
    stmt = select(GuardianRuleSet).order_by(GuardianRuleSet.created_at.desc())
    res = await db.execute(stmt)
    return [
        GuardianRuleSetResponse(
            id=r.id,
            version=r.version,
            status=RuleSetStatus(r.status),
            effective_at=r.effective_at,
            checksum=r.checksum,
            rules_config=r.rules_config,
            created_at=r.created_at,
        )
        for r in res.scalars().all()
    ]


@router.get("/rulesets/active", response_model=GuardianRuleSetResponse)
async def get_active_ruleset(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GuardianRuleSetResponse:
    engine = GuardianEngine(session_factory=lambda: db)
    ruleset = await engine.get_or_create_default_ruleset(db)
    return GuardianRuleSetResponse(
        id=ruleset.id,
        version=ruleset.version,
        status=RuleSetStatus(ruleset.status),
        effective_at=ruleset.effective_at,
        checksum=ruleset.checksum,
        rules_config=ruleset.rules_config,
        created_at=ruleset.created_at,
    )


@router.post(
    "/rulesets", response_model=GuardianRuleSetResponse, status_code=status.HTTP_201_CREATED
)
async def create_ruleset(
    payload: GuardianRuleSetCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GuardianRuleSetResponse:
    existing = await db.execute(
        select(GuardianRuleSet).where(GuardianRuleSet.version == payload.version)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ruleset version '{payload.version}' already exists. Rulesets are immutable.",
        )

    checksum = compute_rules_config_checksum(payload.rules_config)
    ruleset = GuardianRuleSet(
        id=uuid4(),
        version=payload.version,
        status=payload.status.value,
        effective_at=payload.effective_at,
        checksum=checksum,
        rules_config=payload.rules_config,
    )
    db.add(ruleset)
    await db.commit()
    return GuardianRuleSetResponse(
        id=ruleset.id,
        version=ruleset.version,
        status=RuleSetStatus(ruleset.status),
        effective_at=ruleset.effective_at,
        checksum=ruleset.checksum,
        rules_config=ruleset.rules_config,
        created_at=ruleset.created_at,
    )


@router.post("/rulesets/{ruleset_id}/activate", response_model=GuardianRuleSetResponse)
async def activate_ruleset(
    ruleset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GuardianRuleSetResponse:
    res = await db.execute(
        select(GuardianRuleSet).where(GuardianRuleSet.id == ruleset_id).with_for_update()
    )
    ruleset = res.scalar_one_or_none()
    if not ruleset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ruleset not found")

    current_actives = await db.execute(
        select(GuardianRuleSet).where(GuardianRuleSet.status == RuleSetStatus.ACTIVE.value)
    )
    for ca in current_actives.scalars().all():
        ca.status = RuleSetStatus.ARCHIVED.value

    ruleset.status = RuleSetStatus.ACTIVE.value
    await db.commit()
    return GuardianRuleSetResponse(
        id=ruleset.id,
        version=ruleset.version,
        status=RuleSetStatus(ruleset.status),
        effective_at=ruleset.effective_at,
        checksum=ruleset.checksum,
        rules_config=ruleset.rules_config,
        created_at=ruleset.created_at,
    )


# ── Checks ──


@router.post("/checks", response_model=GuardianCheckResponse)
async def trigger_guardian_check(
    payload: GuardianCheckCreate,
    _db: Annotated[AsyncSession, Depends(get_db)],
) -> GuardianCheckResponse:
    from omega.infrastructure.database import AsyncSessionLocal

    engine = GuardianEngine(session_factory=AsyncSessionLocal)
    return await engine.execute_check(payload)


@router.get("/checks/{check_id}", response_model=GuardianCheckResponse)
async def get_guardian_check(
    check_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GuardianCheckResponse:
    stmt = (
        select(GuardianCheck)
        .where(GuardianCheck.id == check_id)
        .options(
            selectinload(GuardianCheck.decision),
            selectinload(GuardianCheck.findings),
            selectinload(GuardianCheck.detector_runs),
        )
    )
    res = await db.execute(stmt)
    check = res.scalar_one_or_none()
    if not check:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Guardian check not found"
        )

    engine = GuardianEngine(session_factory=lambda: db)
    return engine._build_check_response(check)


@router.get("/missions/{mission_id}/checks", response_model=list[GuardianCheckResponse])
async def list_mission_checks(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[GuardianCheckResponse]:
    stmt = (
        select(GuardianCheck)
        .where(GuardianCheck.mission_id == mission_id)
        .options(
            selectinload(GuardianCheck.decision),
            selectinload(GuardianCheck.findings),
            selectinload(GuardianCheck.detector_runs),
        )
        .order_by(GuardianCheck.created_at.desc())
    )
    res = await db.execute(stmt)
    engine = GuardianEngine(session_factory=lambda: db)
    return [engine._build_check_response(c) for c in res.scalars().all()]


@router.get("/missions/{mission_id}/status", response_model=GuardianConsolidatedStatus)
async def get_mission_guardian_status(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GuardianConsolidatedStatus:
    mission_res = await db.execute(select(Mission).where(Mission.id == mission_id))
    mission = mission_res.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")

    checks_stmt = (
        select(GuardianCheck)
        .where(GuardianCheck.mission_id == mission_id)
        .options(
            selectinload(GuardianCheck.decision),
            selectinload(GuardianCheck.findings),
        )
        .order_by(GuardianCheck.created_at.desc())
    )
    res = await db.execute(checks_stmt)
    all_checks = res.scalars().all()

    checkpoint_states: dict[str, GuardianGateState] = {}
    blocking_checkpoints: list[GuardianCheckpoint] = []
    open_findings = []

    for c in all_checks:
        cp_key = c.checkpoint
        if cp_key not in checkpoint_states and c.decision:
            gate_state = GuardianGateState(c.decision.resulting_gate_state)
            checkpoint_states[cp_key] = gate_state
            if gate_state == GuardianGateState.BLOCKED:
                blocking_checkpoints.append(GuardianCheckpoint(c.checkpoint))
                for f in c.findings:
                    open_findings.append(f.message)

    overall_gate_state = GuardianGateState.OPEN
    if any(s == GuardianGateState.BLOCKED for s in checkpoint_states.values()):
        overall_gate_state = GuardianGateState.BLOCKED
    elif any(s == GuardianGateState.RESTRICTED for s in checkpoint_states.values()):
        overall_gate_state = GuardianGateState.RESTRICTED

    cost_stmt = select(func.coalesce(func.sum(CostRecord.amount_usd), Decimal("0.00"))).where(
        CostRecord.mission_id == mission_id
    )
    cost_res = await db.execute(cost_stmt)
    accumulated_cost = Decimal(str(cost_res.scalar_one() or 0.0))

    engine = GuardianEngine(session_factory=lambda: db)
    ruleset = await engine.get_or_create_default_ruleset(db)
    budget_ceiling = Decimal(
        str((ruleset.rules_config or {}).get("max_mission_budget_usd", "50.00"))
    )
    remaining_budget = max(Decimal("0.00"), budget_ceiling - accumulated_cost)

    return GuardianConsolidatedStatus(
        mission_id=mission_id,
        guardian_epoch=mission.guardian_epoch,
        overall_gate_state=overall_gate_state,
        checkpoint_states=checkpoint_states,
        blocking_checkpoints=blocking_checkpoints,
        open_findings_count=len(open_findings),
        accumulated_cost_usd=accumulated_cost,
        budget_ceiling_usd=budget_ceiling,
        remaining_budget_usd=remaining_budget,
    )


# ── Exceptions ──


@router.post(
    "/exceptions", response_model=GuardianExceptionResponse, status_code=status.HTTP_201_CREATED
)
async def create_exception(
    payload: GuardianExceptionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GuardianExceptionResponse:
    return await GuardianExceptionManager.create_exception(db, payload)


@router.post("/exceptions/{exception_id}/revoke", response_model=GuardianExceptionResponse)
async def revoke_exception(
    exception_id: UUID,
    payload: GuardianExceptionRevoke,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GuardianExceptionResponse:
    return await GuardianExceptionManager.revoke_exception(db, exception_id, payload)


@router.get("/exceptions", response_model=list[GuardianExceptionResponse])
async def list_exceptions(
    db: Annotated[AsyncSession, Depends(get_db)],
    active_only: Annotated[bool, Query()] = True,
) -> list[GuardianExceptionResponse]:
    stmt = select(GuardianException).order_by(GuardianException.created_at.desc())
    res = await db.execute(stmt)
    exceptions = res.scalars().all()
    now = datetime.now(UTC)

    results = []
    for exc in exceptions:
        is_active = (exc.revoked_at is None) and (exc.expires_at > now)
        if active_only and not is_active:
            continue
        results.append(
            GuardianExceptionResponse(
                id=exc.id,
                rule_id=exc.rule_id,
                risk_type=exc.risk_type,
                channel_id=exc.channel_id,
                mission_id=exc.mission_id,
                expires_at=exc.expires_at,
                created_by=exc.created_by,
                created_reason=exc.created_reason,
                revoked_at=exc.revoked_at,
                revoked_by=exc.revoked_by,
                revocation_reason=exc.revocation_reason,
                is_active=is_active,
                created_at=exc.created_at,
            )
        )
    return results


# ── Costs ──


@router.post("/costs", response_model=CostRecordResponse, status_code=status.HTTP_201_CREATED)
async def record_cost(
    payload: CostRecordCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CostRecordResponse:
    idempotency_key = payload.idempotency_key
    if not idempotency_key:
        raw = f"{payload.source_type}:{payload.source_id}:{payload.cost_type.value}:{payload.mission_id}"
        idempotency_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    existing = await db.execute(
        select(CostRecord).where(CostRecord.idempotency_key == idempotency_key)
    )
    existing_rec = existing.scalar_one_or_none()
    if existing_rec:
        return CostRecordResponse(
            id=existing_rec.id,
            mission_id=existing_rec.mission_id,
            task_id=existing_rec.task_id,
            production_request_id=existing_rec.production_request_id,
            cost_type=existing_rec.cost_type,
            amount_usd=existing_rec.amount_usd,
            units=existing_rec.units,
            source_type=existing_rec.source_type,
            source_id=existing_rec.source_id,
            idempotency_key=existing_rec.idempotency_key,
            recorded_at=existing_rec.recorded_at,
            created_at=existing_rec.created_at,
        )

    rec = CostRecord(
        id=uuid4(),
        mission_id=payload.mission_id,
        task_id=payload.task_id,
        production_request_id=payload.production_request_id,
        cost_type=payload.cost_type.value,
        amount_usd=payload.amount_usd,
        units=payload.units,
        source_type=payload.source_type,
        source_id=payload.source_id,
        idempotency_key=idempotency_key,
    )
    db.add(rec)
    await db.commit()
    return CostRecordResponse(
        id=rec.id,
        mission_id=rec.mission_id,
        task_id=rec.task_id,
        production_request_id=rec.production_request_id,
        cost_type=rec.cost_type,
        amount_usd=rec.amount_usd,
        units=rec.units,
        source_type=rec.source_type,
        source_id=rec.source_id,
        idempotency_key=rec.idempotency_key,
        recorded_at=rec.recorded_at,
        created_at=rec.created_at,
    )


@router.get("/missions/{mission_id}/costs", response_model=list[CostRecordResponse])
async def list_mission_costs(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[CostRecordResponse]:
    stmt = (
        select(CostRecord)
        .where(CostRecord.mission_id == mission_id)
        .order_by(CostRecord.recorded_at.desc())
    )
    res = await db.execute(stmt)
    return [
        CostRecordResponse(
            id=c.id,
            mission_id=c.mission_id,
            task_id=c.task_id,
            production_request_id=c.production_request_id,
            cost_type=c.cost_type,
            amount_usd=c.amount_usd,
            units=c.units,
            source_type=c.source_type,
            source_id=c.source_id,
            idempotency_key=c.idempotency_key,
            recorded_at=c.recorded_at,
            created_at=c.created_at,
        )
        for c in res.scalars().all()
    ]


# ── Resolutions & Safe Resume ──


@router.post("/resolutions", response_model=GuardianResolutionResponse)
async def record_resolution_event(
    payload: GuardianResolutionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GuardianResolutionResponse:
    event = GuardianResolutionEvent(
        id=uuid4(),
        finding_id=payload.finding_id,
        decision_id=payload.decision_id,
        resolution_type=payload.resolution_type.value,
        actor=payload.actor,
        reason=payload.reason,
    )
    db.add(event)
    await db.commit()
    return GuardianResolutionResponse(
        id=event.id,
        finding_id=event.finding_id,
        decision_id=event.decision_id,
        resolution_type=event.resolution_type,
        actor=event.actor,
        reason=event.reason,
        created_at=event.created_at,
    )


@router.post("/missions/{mission_id}/resume", response_model=MissionResponse)
async def trigger_safe_resume(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[str, Query()] = "OPERATOR",
    reason: Annotated[str, Query()] = "Safe resume triggered by operator",
) -> MissionResponse:
    try:
        mission_res = await resume_mission(db, mission_id, actor=actor, reason=reason)
        if not mission_res:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
        return mission_res
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
