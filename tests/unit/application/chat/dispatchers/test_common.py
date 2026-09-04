"""Unit tests for the shared dispatcher helpers in ``_common``.

Covers the three pieces every ``*_write`` dispatcher shares: the ``confirmed``
commit envelope, the ``plural`` suffix, and ``require_bool``.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.application.chat.dispatchers._common import confirmed, plural, require_bool
from src.domain.entities.pending_action import PendingAction
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import ToolExecutionError


def _action(description: str = "Delete tag 'gym'") -> PendingAction:
    return PendingAction(
        action_id=uuid4(),
        user_id="default",
        tool_name="manage_tags",
        tool_input={},
        description=description,
        details={},
        created_at=datetime.now(UTC),
    )


class TestConfirmed:
    def test_builds_the_status_operation_description_spine(self) -> None:
        assert confirmed(_action(), "delete") == {
            "status": "confirmed",
            "operation": "delete",
            "description": "Delete tag 'gym'",
        }

    def test_extra_fields_are_merged_after_the_spine(self) -> None:
        result = confirmed(_action(), "delete", tag="gym", affected_count=3)

        assert result["tag"] == "gym"
        assert result["affected_count"] == 3
        assert list(result) == [
            "status",
            "operation",
            "description",
            "tag",
            "affected_count",
        ]

    def test_extra_may_override_a_spine_field(self) -> None:
        # A projection spread into ``extra`` wins, so an executor stays free to
        # echo its own value under a spine key.
        result = confirmed(_action(), "delete", description="overridden")

        assert result["description"] == "overridden"


class TestPlural:
    @pytest.mark.parametrize(("count", "expected"), [(0, "s"), (1, ""), (2, "s")])
    def test_suffix(self, count: int, expected: str) -> None:
        assert plural(count) == expected


class TestRequireBool:
    def test_returns_the_bool(self) -> None:
        assert require_bool({"enabled": True}, "enabled") is True
        assert require_bool({"enabled": False}, "enabled") is False

    def test_missing_key_rejected(self) -> None:
        with pytest.raises(ToolExecutionError, match="'enabled' is required"):
            require_bool({}, "enabled")

    def test_null_rejected(self) -> None:
        with pytest.raises(ToolExecutionError, match="'enabled' is required"):
            require_bool({"enabled": None}, "enabled")

    @pytest.mark.parametrize("value", ["true", 1, 0, []])
    def test_non_bool_rejected(self, value: JsonValue) -> None:
        with pytest.raises(ToolExecutionError, match="true or false"):
            require_bool({"enabled": value}, "enabled")
