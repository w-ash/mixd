"""Unit tests for migration 033's pure key-rewrite transform.

The DB row loop (``_rewrite``) needs an Alembic context, and the integration
test harness builds its schema via ``metadata.create_all`` rather than running
migrations — so the round-trip across the three JSONB columns is covered by the
manual ``alembic upgrade head`` smoke step. Here we pin the load-bearing logic:
``_rename_definition_keys`` renames the day-window keys inside every task config
and nowhere else, and upgrade/downgrade are exact inverses.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "033_rename_day_window_keys.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_033", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()
UPGRADE = migration._UPGRADE_RENAME
DOWNGRADE = migration._DOWNGRADE_RENAME
rename_keys = migration._rename_definition_keys


def _def(*configs: dict[str, Any]) -> dict[str, Any]:
    """A WorkflowDef-shaped dict with one task per given config."""
    return {
        "id": "wf",
        "name": "WF",
        "version": "1.0",
        "tasks": [
            {"id": f"t{i}", "type": "filter.by_play_history", "config": c}
            for i, c in enumerate(configs)
        ],
    }


class TestRenameDefinitionKeys:
    def test_renames_both_keys_across_tasks(self):
        definition = _def(
            {"min_plays": 8, "max_days_back": 30},
            {"min_days_back": 180},
        )
        changed = rename_keys(definition, UPGRADE)

        assert changed is True
        c0 = definition["tasks"][0]["config"]
        c1 = definition["tasks"][1]["config"]
        assert c0 == {"min_plays": 8, "played_within_days": 30}
        assert c1 == {"not_played_in_days": 180}

    def test_leaves_unrelated_config_untouched(self):
        definition = _def({"min_plays": 5, "reverse": True})
        assert rename_keys(definition, UPGRADE) is False
        assert definition["tasks"][0]["config"] == {
            "min_plays": 5,
            "reverse": True,
        }

    def test_upgrade_then_downgrade_round_trips(self):
        original = _def({"min_days_back": 90, "max_days_back": 30})
        roundtrip = _def({"min_days_back": 90, "max_days_back": 30})

        rename_keys(roundtrip, UPGRADE)
        rename_keys(roundtrip, DOWNGRADE)
        assert roundtrip == original

    def test_idempotent_second_upgrade_is_noop(self):
        definition = _def({"max_days_back": 30})
        assert rename_keys(definition, UPGRADE) is True
        assert rename_keys(definition, UPGRADE) is False
        assert definition["tasks"][0]["config"] == {"played_within_days": 30}

    @pytest.mark.parametrize(
        "definition",
        [
            None,
            "not-a-dict",
            {"tasks": "not-a-list"},
            {"tasks": [None, 42, {"type": "x"}]},  # no/!dict configs
            {"tasks": [{"config": "not-a-dict"}]},
        ],
    )
    def test_malformed_input_is_skipped_not_raised(self, definition: object):
        # A stored row that doesn't match the expected shape must not break the
        # migration — return False and mutate nothing.
        assert rename_keys(definition, UPGRADE) is False
