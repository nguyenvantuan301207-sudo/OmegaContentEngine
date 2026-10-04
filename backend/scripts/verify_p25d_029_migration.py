"""Verify migration 029 on an explicitly disposable PostgreSQL test database."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from omega.infrastructure.models import Base

TABLES = {
    "learning_policy_roots",
    "learning_policy_revisions",
    "learning_loop_evidence",
    "learning_hypothesis_roots",
    "learning_hypothesis_revisions",
    "learning_evaluations",
    "learning_evaluation_evidence_memberships",
    "learning_insights",
    "learning_recommendations",
    "learning_candidate_adaptations",
    "learning_adaptation_approval_history",
}


def main() -> None:
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if (
        url.database != "p25d029_repair_test"
        or url.host not in {"localhost", "127.0.0.1"}
        or url.port != 5433
    ):
        raise RuntimeError(
            "Lifecycle verification requires the dedicated local p25d029_repair_test database"
        )
    sync = url.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False)
    os.environ["DATABASE_URL_SYNC"] = sync
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(sync)
    with engine.connect() as connection:
        existing = inspect(connection).get_table_names()
        if existing:
            raise RuntimeError("Lifecycle verification requires a fresh empty dedicated database")
    command.upgrade(config, "028")
    command.upgrade(config, "029")
    command.downgrade(config, "028")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "028"
        )
        assert TABLES.isdisjoint(inspect(connection).get_table_names())
        assert "learning_hypotheses" in inspect(connection).get_table_names()
        assert "experiment_attribution_results" in inspect(connection).get_table_names()
    command.upgrade(config, "029")
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "include_object": lambda obj, name, kind, reflected, comparison: (
                    name in TABLES
                    if kind == "table"
                    else getattr(obj, "table", None) is None or obj.table.name in TABLES
                ),
                "compare_type": True,
            },
        )
        differences = compare_metadata(context, Base.metadata)
        assert not differences, differences
        inspector = inspect(connection)
        for name in TABLES:
            expected = {
                c.name
                for c in Base.metadata.tables[name].constraints
                if c.__class__.__name__ == "CheckConstraint"
            }
            actual = {c["name"] for c in inspector.get_check_constraints(name)}
            assert expected == actual, (name, expected, actual)
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "029"
        )
    engine.dispose()
    print(
        "028 -> 029 -> 028 -> 029 PASS; migration/ORM tables, columns, types, nullability, FKs, indexes, uniqueness, checks PASS"
    )


if __name__ == "__main__":
    main()
