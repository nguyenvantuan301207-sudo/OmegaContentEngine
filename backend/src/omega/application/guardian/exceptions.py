"""Guardian Exception Manager.

Manages auditable, append-only exceptions and matching logic for findings.
Exceptions are never deleted and revocation is one-way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.guardian import (
    GuardianExceptionCreate,
    GuardianExceptionResponse,
    GuardianExceptionRevoke,
    GuardianFindingData,
    GuardianResolutionType,
)
from omega.infrastructure.models import GuardianException, GuardianFinding, GuardianResolutionEvent


class GuardianExceptionManager:
    """Manages creation, revocation, and evaluation of scoped Guardian exceptions."""

    @staticmethod
    async def create_exception(
        session: AsyncSession, payload: GuardianExceptionCreate
    ) -> GuardianExceptionResponse:
        """Create a new scoped exception and record an immutable creation audit event."""
        now = datetime.now(UTC)
        exc = GuardianException(
            id=uuid4(),
            rule_id=payload.rule_id,
            risk_type=payload.risk_type.value if payload.risk_type else None,
            channel_id=payload.channel_id,
            mission_id=payload.mission_id,
            expires_at=payload.expires_at,
            created_by=payload.created_by,
            created_reason=payload.created_reason,
            created_at=now,
        )
        session.add(exc)
        await session.flush()

        res_event = GuardianResolutionEvent(
            id=uuid4(),
            resolution_type=GuardianResolutionType.EXCEPTION_APPLIED.value,
            actor=payload.created_by,
            reason=f"Created exception for rule '{payload.rule_id}'/risk '{payload.risk_type}': {payload.created_reason}",
            created_at=now,
        )
        session.add(res_event)

        is_active = now < payload.expires_at
        return GuardianExceptionResponse(
            id=exc.id,
            rule_id=exc.rule_id,
            risk_type=exc.risk_type,
            channel_id=exc.channel_id,
            mission_id=exc.mission_id,
            expires_at=exc.expires_at,
            created_by=exc.created_by,
            created_reason=exc.created_reason,
            revoked_at=None,
            revoked_by=None,
            revocation_reason=None,
            is_active=is_active,
            created_at=exc.created_at,
        )

    @staticmethod
    async def revoke_exception(
        session: AsyncSession, exception_id: UUID, payload: GuardianExceptionRevoke
    ) -> GuardianExceptionResponse:
        """Revoke an active exception. One-way operation; cannot be rewritten."""
        stmt = (
            select(GuardianException).where(GuardianException.id == exception_id).with_for_update()
        )
        res = await session.execute(stmt)
        exc = res.scalar_one_or_none()
        if not exc:
            raise ValueError(f"GuardianException '{exception_id}' not found.")

        if exc.revoked_at is not None:
            raise ValueError(
                f"GuardianException '{exception_id}' was already revoked at {exc.revoked_at}."
            )

        now = datetime.now(UTC)
        exc.revoked_at = now
        exc.revoked_by = payload.revoked_by
        exc.revocation_reason = payload.revocation_reason

        res_event = GuardianResolutionEvent(
            id=uuid4(),
            resolution_type=GuardianResolutionType.EXCEPTION_REVOKED.value,
            actor=payload.revoked_by,
            reason=f"Revoked exception {exception_id}: {payload.revocation_reason}",
            created_at=now,
        )
        session.add(res_event)

        return GuardianExceptionResponse(
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
            is_active=False,
            created_at=exc.created_at,
        )

    @staticmethod
    async def find_matching_exception(
        session: AsyncSession,
        finding: GuardianFindingData | GuardianFinding,
        mission_id: UUID,
        channel_id: UUID | None = None,
    ) -> UUID | None:
        """Match an active, unrevoked exception covering the given finding."""
        now = datetime.now(UTC)
        stmt = select(GuardianException).where(
            GuardianException.expires_at > now,
            GuardianException.revoked_at.is_(None),
        )
        res = await session.execute(stmt)
        active_exceptions = res.scalars().all()

        rule_id = finding.rule_id
        risk_type = (
            finding.risk_type.value
            if hasattr(finding.risk_type, "value")
            else str(finding.risk_type)
        )

        for exc in active_exceptions:
            # Channel scope check
            if (
                exc.channel_id is not None
                and channel_id is not None
                and exc.channel_id != channel_id
            ):
                continue

            # Mission scope check
            if exc.mission_id is not None and exc.mission_id != mission_id:
                continue

            # Rule or Risk check
            rule_match = exc.rule_id is None or exc.rule_id == rule_id
            risk_match = exc.risk_type is None or exc.risk_type == risk_type

            if rule_match and risk_match:
                return exc.id

        return None
