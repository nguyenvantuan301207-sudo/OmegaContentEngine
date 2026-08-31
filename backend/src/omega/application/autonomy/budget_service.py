"""Durable budget accounting, reservation ledger, and liability governance for OMEGA-014."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.helpers import compute_reservation_key
from omega.domain.autonomy import (
    ACTIVE_BUDGET_LIABILITY_STATES,
    BudgetReservationStatus,
)
from omega.infrastructure.models import (
    AutonomyBudgetLedger,
    AutonomyBudgetReservation,
)


class HardBudgetExceededError(Exception):
    """Raised when an action's budget liability exceeds daily or lifetime caps."""

    pass


class BudgetStateError(Exception):
    """Raised when an illegal budget state transition is attempted."""

    pass


class AutonomyBudgetService:
    """Service managing durable budget ledgers, per-action reservations, and liability tracking."""

    @classmethod
    async def get_or_create_ledger(
        cls,
        session: AsyncSession,
        loop_id: UUID,
        lifetime_cap_usd: Decimal,
        daily_cap_usd: Decimal,
        currency: str = "USD",
    ) -> AutonomyBudgetLedger:
        """Retrieve or initialize the authoritative budget ledger for a loop."""
        stmt = select(AutonomyBudgetLedger).where(AutonomyBudgetLedger.loop_id == loop_id)
        ledger = (await session.execute(stmt)).scalar_one_or_none()
        if not ledger:
            # Query current UTC date from PostgreSQL
            date_res = await session.execute(text("SELECT (NOW() AT TIME ZONE 'UTC')::DATE"))
            db_date = date_res.scalar()

            ledger = AutonomyBudgetLedger(
                loop_id=loop_id,
                lifetime_cap_usd=lifetime_cap_usd,
                daily_cap_usd=daily_cap_usd,
                lifetime_spend_usd=Decimal("0.0000"),
                daily_spend_usd=Decimal("0.0000"),
                current_day_utc=db_date,
                currency=currency,
            )
            session.add(ledger)
            await session.flush()
        return ledger

    @classmethod
    async def reserve_budget(
        cls,
        session: AsyncSession,
        loop_id: UUID,
        action_plan_id: UUID,
        semantic_action_key: str,
        estimated_amount: Decimal,
        currency: str = "USD",
    ) -> AutonomyBudgetReservation:
        """Atomically reserve funds for a planned action under row lock.

        Ensures:
        - Strict idempotency on reservation_key (zero duplicate liability on retry).
        - Unresolved reservations (RESERVED + RECONCILIATION_REQUIRED) are counted in liability.
        - Previous-day unresolved reservations count against lifetime cap, not today's daily cap.
        """
        # Quantize to 4 decimal places rounding ceiling to prevent under-reservation
        est_amount = estimated_amount.quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
        res_key = compute_reservation_key(loop_id, action_plan_id, semantic_action_key)

        # 1. Lock authoritative ledger
        stmt_ledger = (
            select(AutonomyBudgetLedger)
            .where(AutonomyBudgetLedger.loop_id == loop_id)
            .with_for_update()
        )
        ledger = (await session.execute(stmt_ledger)).scalar_one_or_none()
        if not ledger:
            raise ValueError(f"No budget ledger found for loop '{loop_id}'.")

        # 2. Check for existing reservation with exact reservation_key (Idempotency)
        stmt_existing = select(AutonomyBudgetReservation).where(
            AutonomyBudgetReservation.reservation_key == res_key
        )
        existing = (await session.execute(stmt_existing)).scalar_one_or_none()
        if existing and existing.status in (
            BudgetReservationStatus.RESERVED.value,
            BudgetReservationStatus.CAPTURED.value,
            BudgetReservationStatus.RECONCILIATION_REQUIRED.value,
        ):
            return existing  # Idempotently return existing reservation without double-charging

        # 3. Synchronize DB UTC Date and Rollover
        date_res = await session.execute(text("SELECT (NOW() AT TIME ZONE 'UTC')::DATE"))
        current_db_date = date_res.scalar()

        if current_db_date > ledger.current_day_utc:
            ledger.daily_spend_usd = Decimal("0.0000")
            ledger.current_day_utc = current_db_date
            await session.flush()

        # 4. Calculate Lifetime Unresolved Liability (ALL days)
        stmt_lifetime = select(
            func.coalesce(func.sum(AutonomyBudgetReservation.estimated_amount), Decimal("0.0000"))
        ).where(
            AutonomyBudgetReservation.loop_id == loop_id,
            AutonomyBudgetReservation.status.in_([s.value for s in ACTIVE_BUDGET_LIABILITY_STATES]),
        )
        lifetime_unresolved = (await session.execute(stmt_lifetime)).scalar()

        # 5. Calculate Daily Unresolved Liability (Current DB UTC Day ONLY)
        stmt_daily = select(
            func.coalesce(func.sum(AutonomyBudgetReservation.estimated_amount), Decimal("0.0000"))
        ).where(
            AutonomyBudgetReservation.loop_id == loop_id,
            AutonomyBudgetReservation.status.in_([s.value for s in ACTIVE_BUDGET_LIABILITY_STATES]),
            AutonomyBudgetReservation.budget_day_utc == current_db_date,
        )
        daily_unresolved = (await session.execute(stmt_daily)).scalar()

        # 6. Hard-Cap Validation (Must include existing spend + unresolved liabilities + new estimate)
        total_lifetime_liability = ledger.lifetime_spend_usd + lifetime_unresolved + est_amount
        total_daily_liability = ledger.daily_spend_usd + daily_unresolved + est_amount

        if total_lifetime_liability > ledger.lifetime_cap_usd:
            raise HardBudgetExceededError(
                f"Lifetime budget cap ${ledger.lifetime_cap_usd} breached by total liability ${total_lifetime_liability}."
            )

        if total_daily_liability > ledger.daily_cap_usd:
            raise HardBudgetExceededError(
                f"Daily budget cap ${ledger.daily_cap_usd} breached by daily liability ${total_daily_liability}."
            )

        # 7. Create New Reservation
        # Expiry is set using DB NOW() + INTERVAL '15 minutes'
        time_res = await session.execute(text("SELECT NOW(), NOW() + INTERVAL '15 minutes'"))
        db_now, expires_at = time_res.first()

        reservation = AutonomyBudgetReservation(
            id=uuid4(),
            loop_id=loop_id,
            action_plan_id=action_plan_id,
            semantic_action_key=semantic_action_key,
            reservation_key=res_key,
            estimated_amount=est_amount,
            currency=currency,
            status=BudgetReservationStatus.RESERVED.value,
            budget_day_utc=current_db_date,
            reserved_at=db_now,
            expires_at=expires_at,
        )
        session.add(reservation)
        await session.flush()
        return reservation

    @classmethod
    async def capture_reservation(
        cls,
        session: AsyncSession,
        reservation_id: UUID,
        captured_amount: Decimal,
        provider_cost_reference: str | None = None,
    ) -> AutonomyBudgetReservation:
        """Capture an active or reconciliation-required reservation idempotently."""
        stmt = (
            select(AutonomyBudgetReservation)
            .where(AutonomyBudgetReservation.id == reservation_id)
            .with_for_update()
        )
        res = (await session.execute(stmt)).scalar_one_or_none()
        if not res:
            raise ValueError(f"Reservation '{reservation_id}' not found.")

        # Idempotency: if already CAPTURED, return unchanged
        if res.status == BudgetReservationStatus.CAPTURED.value:
            return res

        if res.status == BudgetReservationStatus.RELEASED.value:
            raise BudgetStateError("Cannot capture an already released budget reservation.")

        amt = captured_amount.quantize(Decimal("0.0001"))

        # Lock and update ledger spend
        stmt_ledger = (
            select(AutonomyBudgetLedger)
            .where(AutonomyBudgetLedger.loop_id == res.loop_id)
            .with_for_update()
        )
        ledger = (await session.execute(stmt_ledger)).scalar_one()

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        res.status = BudgetReservationStatus.CAPTURED.value
        res.captured_amount = amt
        res.captured_at = db_now
        if provider_cost_reference:
            res.provider_cost_reference = provider_cost_reference

        ledger.lifetime_spend_usd += amt
        ledger.daily_spend_usd += amt
        ledger.updated_at = db_now
        await session.flush()
        return res

    @classmethod
    async def release_reservation(
        cls,
        session: AsyncSession,
        reservation_id: UUID,
        release_reason: str,
    ) -> AutonomyBudgetReservation:
        """Release an unresolved reservation, freeing liability idempotently."""
        stmt = (
            select(AutonomyBudgetReservation)
            .where(AutonomyBudgetReservation.id == reservation_id)
            .with_for_update()
        )
        res = (await session.execute(stmt)).scalar_one_or_none()
        if not res:
            raise ValueError(f"Reservation '{reservation_id}' not found.")

        # Idempotency: if already RELEASED, return unchanged
        if res.status == BudgetReservationStatus.RELEASED.value:
            return res

        if res.status == BudgetReservationStatus.CAPTURED.value:
            raise BudgetStateError("Cannot release an already captured budget reservation.")

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        res.status = BudgetReservationStatus.RELEASED.value
        res.released_at = db_now
        res.release_reason = release_reason
        await session.flush()
        return res
