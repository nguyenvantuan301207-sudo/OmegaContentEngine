"""Integration tests for Migration 026 and database constraint enforcement on isolated PostgreSQL."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError


ISOLATED_SYNC_URL = "postgresql+psycopg2://omega_p20b:p20b_isolated_test_password@localhost:45432/omega_p20b_test"


def get_alembic_config() -> Config:
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    ini_path = os.path.join(backend_dir, "alembic.ini")
    cfg = Config(ini_path)
    cfg.set_main_option("sqlalchemy.url", ISOLATED_SYNC_URL)
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    os.environ["DATABASE_URL_SYNC"] = ISOLATED_SYNC_URL
    return cfg


def test_01_migration_upgrade_downgrade_reupgrade_cycle():
    """Prove 025 -> 026 upgrade, alembic current = 026, 026 -> 025 downgrade, and 025 -> 026 re-upgrade."""
    cfg = get_alembic_config()
    engine = create_engine(ISOLATED_SYNC_URL)

    # 0. Ensure starting at 025
    command.downgrade(cfg, "025")
    with engine.connect() as conn:
        ver = conn.execute(text("SELECT version_num FROM alembic_version;")).scalar()
        assert ver == "025"

    # 1. Upgrade to 026
    command.upgrade(cfg, "026")
    with engine.connect() as conn:
        ver = conn.execute(text("SELECT version_num FROM alembic_version;")).scalar()
        assert ver == "026"

        # Verify pipeline_analytics_rollups table exists
        exists = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pipeline_analytics_rollups');")
        ).scalar()
        assert exists is True

    # 3. Downgrade to 025
    command.downgrade(cfg, "025")
    with engine.connect() as conn:
        ver = conn.execute(text("SELECT version_num FROM alembic_version;")).scalar()
        assert ver == "025"

        # Verify table dropped
        exists = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pipeline_analytics_rollups');")
        ).scalar()
        assert exists is False

    # 4. Re-upgrade to 026
    command.upgrade(cfg, "026")
    with engine.connect() as conn:
        ver = conn.execute(text("SELECT version_num FROM alembic_version;")).scalar()
        assert ver == "026"


def test_02_migration_026_constraint_enforcement():
    """Prove strict PostgreSQL check and unique constraint enforcement on isolated database."""
    engine = create_engine(ISOLATED_SYNC_URL)

    t_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    # Clean existing rows
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pipeline_analytics_rollups;"))

    # 1. Valid insert succeeds
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO pipeline_analytics_rollups
                (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                VALUES
                (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start, :b_end, '{"total_terminal_jobs": 10}', 10, 1);
            """),
            {"id": uuid4(), "b_start": t_start, "b_end": t_end},
        )

    # 2. Duplicate rollup key (family, dim_type, dim_val, bucket_start) fails
    with pytest.raises(IntegrityError, match="uq_pipeline_analytics_rollup_bucket"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_analytics_rollups
                    (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                    VALUES
                    (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start, :b_end, '{"total_terminal_jobs": 5}', 5, 1);
                """),
                {"id": uuid4(), "b_start": t_start, "b_end": t_end},
            )

    # 3. Invalid metric family fails
    with pytest.raises(IntegrityError, match="chk_rollup_"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_analytics_rollups
                    (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                    VALUES
                    (:id, 'invalid_family', 'GLOBAL', 'ALL', :b_start, :b_end, '{}', 0, 1);
                """),
                {"id": uuid4(), "b_start": t_start, "b_end": t_end},
            )

    # 4. Invalid dimension type fails
    with pytest.raises(IntegrityError, match="chk_rollup_"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_analytics_rollups
                    (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                    VALUES
                    (:id, 'render_reliability', 'INVALID_DIM', 'ALL', :b_start, :b_end, '{}', 0, 1);
                """),
                {"id": uuid4(), "b_start": t_start, "b_end": t_end},
            )

    # 5. Invalid family-dimension matrix fails (scheduler_reliability with VIDEO_CODEC)
    with pytest.raises(IntegrityError, match="chk_rollup_family_dimension_matrix"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_analytics_rollups
                    (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                    VALUES
                    (:id, 'scheduler_reliability', 'VIDEO_CODEC', 'h264', :b_start, :b_end, '{}', 0, 1);
                """),
                {"id": uuid4(), "b_start": t_start, "b_end": t_end},
            )

    # 6. Invalid bucket range (not exactly 1 day) fails
    with pytest.raises(IntegrityError, match="chk_rollup_daily_utc_bucket"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_analytics_rollups
                    (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                    VALUES
                    (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start, :b_end_2d, '{}', 0, 1);
                """),
                {"id": uuid4(), "b_start": t_start, "b_end_2d": t_start + timedelta(days=2)},
            )

    # 7. Non-midnight start (e.g. 12:00 UTC) fails daily UTC constraint
    with pytest.raises(IntegrityError, match="chk_rollup_daily_utc_bucket"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_analytics_rollups
                    (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                    VALUES
                    (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start_noon, :b_end_noon, '{}', 0, 1);
                """),
                {
                    "id": uuid4(),
                    "b_start_noon": t_start + timedelta(hours=12),
                    "b_end_noon": t_start + timedelta(hours=36),
                },
            )

    # 8. Negative sample count fails
    with pytest.raises(IntegrityError, match="chk_rollup_sample_count"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_analytics_rollups
                    (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                    VALUES
                    (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start, :b_end, '{}', -1, 1);
                """),
                {"id": uuid4(), "b_start": t_start, "b_end": t_end},
            )

    # 9. Schema version <= 0 fails
    with pytest.raises(IntegrityError, match="chk_rollup_schema_version"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO pipeline_analytics_rollups
                    (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                    VALUES
                    (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start, :b_end, '{}', 0, 0);
                """),
                {"id": uuid4(), "b_start": t_start, "b_end": t_end},
            )


def test_03_utc_daily_bucket_dst_and_timezone_independence():
    """Prove chk_rollup_daily_utc_bucket is strictly session-timezone independent across DST boundaries.

    Tests both US Spring-Forward (2026-03-08) and Fall-Back (2026-11-01) boundaries under
    'America/Los_Angeles' and 'UTC' session time zones.
    """
    engine = create_engine(ISOLATED_SYNC_URL)

    test_zones = ["America/Los_Angeles", "UTC"]
    dst_cases = [
        ("spring_forward", datetime(2026, 3, 8, 0, 0, 0, tzinfo=UTC), datetime(2026, 3, 9, 0, 0, 0, tzinfo=UTC)),
        ("fall_back", datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC), datetime(2026, 11, 2, 0, 0, 0, tzinfo=UTC)),
    ]

    for zone in test_zones:
        with engine.connect() as conn:
            conn.execute(text(f"SET TIME ZONE '{zone}';"))
            current_tz = conn.execute(text("SHOW TIME ZONE;")).scalar()
            assert current_tz == zone

            for case_name, valid_start, valid_end in dst_cases:
                # 1. Valid UTC midnight-to-midnight (24 wall-clock UTC hours) succeeds
                with conn.begin_nested():
                    conn.execute(text("DELETE FROM pipeline_analytics_rollups;"))
                    conn.execute(
                        text("""
                            INSERT INTO pipeline_analytics_rollups
                            (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                            VALUES
                            (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start, :b_end, '{}', 0, 1);
                        """),
                        {"id": uuid4(), "b_start": valid_start, "b_end": valid_end},
                    )

                # 2. Invalid start: midday (12:00Z) fails
                with pytest.raises(IntegrityError, match="chk_rollup_daily_utc_bucket"):
                    with conn.begin_nested():
                        conn.execute(
                            text("""
                                INSERT INTO pipeline_analytics_rollups
                                (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                                VALUES
                                (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start, :b_end, '{}', 0, 1);
                            """),
                            {"id": uuid4(), "b_start": valid_start + timedelta(hours=12), "b_end": valid_start + timedelta(hours=36)},
                        )

                # 3. Invalid end: 23-hour bucket (bucket_end at 23:00Z) fails
                with pytest.raises(IntegrityError, match="chk_rollup_daily_utc_bucket"):
                    with conn.begin_nested():
                        conn.execute(
                            text("""
                                INSERT INTO pipeline_analytics_rollups
                                (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                                VALUES
                                (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start, :b_end, '{}', 0, 1);
                            """),
                            {"id": uuid4(), "b_start": valid_start, "b_end": valid_start + timedelta(hours=23)},
                        )

                # 4. Invalid end: 25-hour bucket (bucket_end at 01:00Z next day) fails
                with pytest.raises(IntegrityError, match="chk_rollup_daily_utc_bucket"):
                    with conn.begin_nested():
                        conn.execute(
                            text("""
                                INSERT INTO pipeline_analytics_rollups
                                (id, metric_family, dimension_type, dimension_value, bucket_start, bucket_end, metrics, sample_count, schema_version)
                                VALUES
                                (:id, 'render_reliability', 'GLOBAL', 'ALL', :b_start, :b_end, '{}', 0, 1);
                            """),
                            {"id": uuid4(), "b_start": valid_start, "b_end": valid_start + timedelta(hours=25)},
                        )
            conn.rollback()
