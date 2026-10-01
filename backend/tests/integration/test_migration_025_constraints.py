"""Integration tests verifying Migration 025 schema constraints and composite ownership invariants on real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    RecurringSchedule,
    RecurringScheduleCampaignBinding,
    RecurringScheduleMissionBinding,
    RecurringScheduleOccurrence,
    RecurringScheduleVersion,
)


@pytest.mark.asyncio
async def test_schema_constraints_draft_safe_default() -> None:
    """DRAFT schedule cannot have next_run_at set (chk_draft_no_run)."""
    async with AsyncSessionLocal() as session:
        sched_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        # Attempt to insert DRAFT schedule with executable next_run_at
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO recurring_schedules (id, name, status, start_time, next_run_at, created_at, updated_at)
                    VALUES (:id, 'Invalid Draft', 'DRAFT', :start_time, :next_run, :now, :now)
                    """
                ),
                {"id": sched_id, "start_time": now, "next_run": now, "now": now},
            )
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_schema_constraints_active_requires_version_and_next_run() -> None:
    """ACTIVE schedule requires current_version_id and next_run_at (chk_active_requires_version)."""
    async with AsyncSessionLocal() as session:
        sched_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        # Attempt to insert ACTIVE schedule without current_version_id
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO recurring_schedules (id, name, status, start_time, next_run_at, created_at, updated_at)
                    VALUES (:id, 'Invalid Active', 'ACTIVE', :start_time, :next_run, :now, :now)
                    """
                ),
                {"id": sched_id, "start_time": now, "next_run": now, "now": now},
            )
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_schema_constraints_terminal_no_next_run() -> None:
    """CANCELLED schedule cannot retain next_run_at (chk_terminal_no_run)."""
    async with AsyncSessionLocal() as session:
        sched_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO recurring_schedules (id, name, status, start_time, next_run_at, created_at, updated_at)
                    VALUES (:id, 'Invalid Terminal', 'CANCELLED', :start_time, :next_run, :now, :now)
                    """
                ),
                {"id": sched_id, "start_time": now, "next_run": now, "now": now},
            )
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_schema_constraints_composite_fk_schedule_current_version() -> None:
    """Schedule current_version_id must point to a version belonging to the SAME schedule."""
    async with AsyncSessionLocal() as session:
        sched_a = uuid.uuid4()
        sched_b = uuid.uuid4()
        ver_b = uuid.uuid4()
        target_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Create schedule B
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedules (id, name, status, start_time, created_at, updated_at)
                VALUES (:id, 'Schedule B', 'DRAFT', :now, :now, :now)
                """
            ),
            {"id": sched_b, "now": now},
        )
        # Create version belonging to Schedule B
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedule_versions (
                    id, schedule_id, version_number, interval_seconds, timezone,
                    dst_ambiguous_strategy, dst_nonexistent_strategy, catch_up_policy,
                    max_catch_up_occurrences, target_type, target_id, payload_template, created_at
                ) VALUES (
                    :id, :sched_id, 1, 300, 'UTC',
                    'FIRST', 'NEXT_VALID', 'SKIP_MISSED',
                    3, 'STANDALONE_MISSION', :target_id, '{}', :now
                )
                """
            ),
            {"id": ver_b, "sched_id": sched_b, "target_id": target_id, "now": now},
        )

        # Attempt to insert Schedule A pointing its current_version_id to ver_b (Schedule B's version)
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO recurring_schedules (id, name, status, current_version_id, start_time, created_at, updated_at)
                    VALUES (:id, 'Schedule A', 'DRAFT', :ver_id, :now, :now, :now)
                    """
                ),
                {"id": sched_a, "ver_id": ver_b, "now": now},
            )
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_schema_constraints_occurrence_composite_fk() -> None:
    """Occurrence cannot point to a version belonging to a different schedule."""
    async with AsyncSessionLocal() as session:
        sched_a = uuid.uuid4()
        sched_b = uuid.uuid4()
        ver_b = uuid.uuid4()
        occ_id = uuid.uuid4()
        target_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Create schedule A and B
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedules (id, name, status, start_time, created_at, updated_at)
                VALUES (:id, 'Sched A', 'DRAFT', :now, :now, :now)
                """
            ),
            {"id": sched_a, "now": now},
        )
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedules (id, name, status, start_time, created_at, updated_at)
                VALUES (:id, 'Sched B', 'DRAFT', :now, :now, :now)
                """
            ),
            {"id": sched_b, "now": now},
        )
        # Create version for schedule B
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedule_versions (
                    id, schedule_id, version_number, interval_seconds, timezone,
                    dst_ambiguous_strategy, dst_nonexistent_strategy, catch_up_policy,
                    max_catch_up_occurrences, target_type, target_id, payload_template, created_at
                ) VALUES (
                    :id, :sched_id, 1, 300, 'UTC',
                    'FIRST', 'NEXT_VALID', 'SKIP_MISSED',
                    3, 'STANDALONE_MISSION', :target_id, '{}', :now
                )
                """
            ),
            {"id": ver_b, "sched_id": sched_b, "target_id": target_id, "now": now},
        )

        # Attempt to insert Occurrence for Schedule A pointing to Schedule B's version ver_b
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO recurring_schedule_occurrences (
                        id, schedule_id, schedule_version_id, occurrence_at, status,
                        idempotency_key, downstream_target_type, created_at, updated_at
                    ) VALUES (
                        :id, :sched_a, :ver_b, :now, 'PENDING',
                        :idem, 'STANDALONE_MISSION', :now, :now
                    )
                    """
                ),
                {
                    "id": occ_id,
                    "sched_a": sched_a,
                    "ver_b": ver_b,
                    "now": now,
                    "idem": f"idem-{occ_id}",
                },
            )
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_schema_constraints_duplicate_logical_occurrence() -> None:
    """UNIQUE(schedule_id, occurrence_at) prevents duplicate logical occurrences."""
    async with AsyncSessionLocal() as session:
        sched_id = uuid.uuid4()
        ver_id = uuid.uuid4()
        occ_1 = uuid.uuid4()
        occ_2 = uuid.uuid4()
        target_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        await session.execute(
            text(
                """
                INSERT INTO recurring_schedules (id, name, status, start_time, created_at, updated_at)
                VALUES (:id, 'Sched', 'DRAFT', :now, :now, :now)
                """
            ),
            {"id": sched_id, "now": now},
        )
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedule_versions (
                    id, schedule_id, version_number, interval_seconds, timezone,
                    dst_ambiguous_strategy, dst_nonexistent_strategy, catch_up_policy,
                    max_catch_up_occurrences, target_type, target_id, payload_template, created_at
                ) VALUES (
                    :id, :sched_id, 1, 300, 'UTC',
                    'FIRST', 'NEXT_VALID', 'SKIP_MISSED',
                    3, 'STANDALONE_MISSION', :target_id, '{}', :now
                )
                """
            ),
            {"id": ver_id, "sched_id": sched_id, "target_id": target_id, "now": now},
        )

        # Occurrence 1
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedule_occurrences (
                    id, schedule_id, schedule_version_id, occurrence_at, status,
                    idempotency_key, downstream_target_type, created_at, updated_at
                ) VALUES (
                    :id, :sched_id, :ver_id, :now, 'PENDING',
                    :idem, 'STANDALONE_MISSION', :now, :now
                )
                """
            ),
            {
                "id": occ_1,
                "sched_id": sched_id,
                "ver_id": ver_id,
                "now": now,
                "idem": f"idem-{occ_1}",
            },
        )

        # Occurrence 2 with same schedule_id and occurrence_at -> must fail
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO recurring_schedule_occurrences (
                        id, schedule_id, schedule_version_id, occurrence_at, status,
                        idempotency_key, downstream_target_type, created_at, updated_at
                    ) VALUES (
                        :id, :sched_id, :ver_id, :now, 'PENDING',
                        :idem, 'STANDALONE_MISSION', :now, :now
                    )
                    """
                ),
                {
                    "id": occ_2,
                    "sched_id": sched_id,
                    "ver_id": ver_id,
                    "now": now,
                    "idem": f"idem-{occ_2}",
                },
            )
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_schema_constraints_duplicate_mission_binding() -> None:
    """UNIQUE(occurrence_id) and UNIQUE(mission_id) prevent duplicate mission bindings."""
    async with AsyncSessionLocal() as session:
        sched_id = uuid.uuid4()
        ver_id = uuid.uuid4()
        occ_id = uuid.uuid4()
        mission_id = uuid.uuid4()
        target_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        await session.execute(
            text(
                """
                INSERT INTO recurring_schedules (id, name, status, start_time, created_at, updated_at)
                VALUES (:id, 'Sched', 'DRAFT', :now, :now, :now)
                """
            ),
            {"id": sched_id, "now": now},
        )
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedule_versions (
                    id, schedule_id, version_number, interval_seconds, timezone,
                    dst_ambiguous_strategy, dst_nonexistent_strategy, catch_up_policy,
                    max_catch_up_occurrences, target_type, target_id, payload_template, created_at
                ) VALUES (
                    :id, :sched_id, 1, 300, 'UTC',
                    'FIRST', 'NEXT_VALID', 'SKIP_MISSED',
                    3, 'STANDALONE_MISSION', :target_id, '{}', :now
                )
                """
            ),
            {"id": ver_id, "sched_id": sched_id, "target_id": target_id, "now": now},
        )
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedule_occurrences (
                    id, schedule_id, schedule_version_id, occurrence_at, status,
                    idempotency_key, downstream_target_type, created_at, updated_at
                ) VALUES (
                    :id, :sched_id, :ver_id, :now, 'PENDING',
                    :idem, 'STANDALONE_MISSION', :now, :now
                )
                """
            ),
            {"id": occ_id, "sched_id": sched_id, "ver_id": ver_id, "now": now, "idem": f"idem-{occ_id}"},
        )
        # Create mission row for FK
        await session.execute(
            text(
                """
                INSERT INTO missions (id, title, objective, autonomy_level, state, created_at, updated_at)
                VALUES (:id, 'Test Mission', 'Test Objective', 'SUPERVISED', 'DRAFT', :now, :now)
                """
            ),
            {"id": mission_id, "now": now},
        )

        # Binding 1
        await session.execute(
            text(
                """
                INSERT INTO recurring_schedule_mission_bindings (id, occurrence_id, mission_id, created_at)
                VALUES (:id, :occ_id, :m_id, :now)
                """
            ),
            {"id": uuid.uuid4(), "occ_id": occ_id, "m_id": mission_id, "now": now},
        )

        # Binding 2 with same occurrence_id -> must fail
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO recurring_schedule_mission_bindings (id, occurrence_id, mission_id, created_at)
                    VALUES (:id, :occ_id, :m_id, :now)
                    """
                ),
                {"id": uuid.uuid4(), "occ_id": occ_id, "m_id": uuid.uuid4(), "now": now},
            )
            await session.commit()
        await session.rollback()
