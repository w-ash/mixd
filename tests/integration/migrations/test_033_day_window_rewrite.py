"""End-to-end smoke test for migration 033 (day-window JSONB key rewrite).

The companion unit test (``tests/unit/migrations/test_day_window_key_rename.py``)
pins the pure transform. This one runs the *real* Alembic ``upgrade``/``downgrade``
against a throwaway Postgres, so the DB row-loop (``_rewrite``) and the JSONB
read/write round-trip across all three ``WorkflowDef`` columns are exercised
end-to-end — the gap the integration harness (schema via ``create_all``, not the
migration chain) structurally cannot cover.

Marked ``slow``: spins a dedicated container and runs the chain to revision 032
before applying the data migration. Runs under ``uv run pytest -m slow`` / ``-m ""``.
"""

from pathlib import Path

from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from alembic import command
from src.infrastructure.persistence.database.db_models import (
    DBWorkflow,
    DBWorkflowRun,
    DBWorkflowVersion,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRE_MIGRATION = "032_operation_run_request_params"  # revision just before 033
_MIGRATION = "033_rename_day_window_keys"

pytestmark = pytest.mark.slow

# A WorkflowDef carrying both legacy keys (across two tasks) plus a task with
# neither — the control that must survive the rewrite untouched.
_OLD_DEF: dict[str, object] = {
    "id": "wf",
    "name": "WF",
    "version": "1.0",
    "tasks": [
        {
            "id": "a",
            "type": "filter.by_play_history",
            "config": {"min_plays": 8, "max_days_back": 30},
        },
        {
            "id": "b",
            "type": "sorter.by_play_history",
            "config": {"min_days_back": 180, "reverse": True},
        },
        {"id": "c", "type": "selector.limit", "config": {"count": 40}},
    ],
}
_NEW_DEF: dict[str, object] = {
    "id": "wf",
    "name": "WF",
    "version": "1.0",
    "tasks": [
        {
            "id": "a",
            "type": "filter.by_play_history",
            "config": {"min_plays": 8, "played_within_days": 30},
        },
        {
            "id": "b",
            "type": "sorter.by_play_history",
            "config": {"not_played_in_days": 180, "reverse": True},
        },
        {"id": "c", "type": "selector.limit", "config": {"count": 40}},
    ],
}


@pytest.fixture
def migration_db(monkeypatch: pytest.MonkeyPatch):
    """A throwaway Postgres whose schema is owned by Alembic, not ``create_all``.

    ``DATABASE_URL`` is what ``alembic/env.py`` reads to bind its engine.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine") as pg:
        url = pg.get_connection_url().replace("psycopg2://", "psycopg://")
        monkeypatch.setenv("DATABASE_URL", url)
        yield url


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return cfg


def _stored_definitions(engine: sa.Engine) -> tuple[object, object, object]:
    """The WorkflowDef read back from each of the three JSONB columns."""
    with Session(engine) as session:
        return (
            session.query(DBWorkflow).one().definition,
            session.query(DBWorkflowVersion).one().definition,
            session.query(DBWorkflowRun).one().definition_snapshot,
        )


def test_033_rewrites_all_three_columns_and_reverses(migration_db: str) -> None:
    cfg = _alembic_config()
    engine = sa.create_engine(migration_db)
    try:
        # Build the schema up to the revision *before* the data migration, then
        # seed the legacy shape into all three WorkflowDef columns.
        command.upgrade(cfg, _PRE_MIGRATION)
        with Session(engine) as session:
            workflow = DBWorkflow(name="wf", definition=_OLD_DEF)
            session.add(workflow)
            session.flush()  # populate workflow.id for the FK references below
            session.add(
                DBWorkflowVersion(
                    workflow_id=workflow.id, version=1, definition=_OLD_DEF
                )
            )
            session.add(
                DBWorkflowRun(workflow_id=workflow.id, definition_snapshot=_OLD_DEF)
            )
            session.commit()

        # upgrade renames the keys in every column …
        command.upgrade(cfg, _MIGRATION)
        assert _stored_definitions(engine) == (_NEW_DEF, _NEW_DEF, _NEW_DEF)

        # … and downgrade is its exact inverse.
        command.downgrade(cfg, _PRE_MIGRATION)
        assert _stored_definitions(engine) == (_OLD_DEF, _OLD_DEF, _OLD_DEF)
    finally:
        engine.dispose()
