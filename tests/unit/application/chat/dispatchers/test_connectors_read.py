"""Unit tests for the ``get_discogs_snapshot`` chat dispatcher.

Monkeypatches ``connectors_read.execute_use_case`` with a fake async runner
returning a canned snapshot Result, so the tests exercise projection shape
(and the user-data wrapping on Discogs-originated titles/artist credits)
without a database or Discogs.
"""

from collections.abc import Awaitable, Callable

import pytest

from src.application.chat.dispatchers import connectors_read
from src.application.chat.protocols import ToolContext
from src.application.chat.user_data import wrap
from src.application.use_cases.get_discogs_snapshot import (
    DiscogsSnapshotItem,
    GetDiscogsSnapshotResult,
)
from src.domain.exceptions import ToolExecutionError

_CTX = ToolContext(user_id="default")


def _fake_runner(result: object) -> Callable[..., Awaitable[object]]:
    async def _run(factory: object, user_id: str | None = None) -> object:
        return result

    return _run


def _patch(monkeypatch: pytest.MonkeyPatch, result: object) -> None:
    monkeypatch.setattr(connectors_read, "execute_use_case", _fake_runner(result))


class TestGetDiscogsSnapshot:
    async def test_projects_snapshot_with_wrapped_user_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(
            monkeypatch,
            GetDiscogsSnapshotResult(
                username="attritus",
                total_items=2,
                recent=(
                    DiscogsSnapshotItem(
                        title="Rio",
                        artists="Duran Duran",
                        year=1982,
                        formats="Vinyl (LP, Album)",
                        date_added="2026-08-01T10:00:00-07:00",
                    ),
                ),
            ),
        )

        out = await connectors_read.handle_get_discogs_snapshot({}, _CTX)

        assert isinstance(out, dict)
        assert out["username"] == "attritus"
        assert out["total_items"] == 2
        recent = out["recent"]
        assert isinstance(recent, list)
        assert len(recent) == 1
        item = recent[0]
        assert isinstance(item, dict)
        # Discogs-originated free text reaches the model quoted as data.
        assert item["title"] == wrap("Rio")
        assert item["artists"] == wrap("Duran Duran")
        assert item["year"] == 1982
        assert item["formats"] == "Vinyl (LP, Album)"

    async def test_empty_collection_is_a_normal_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(
            monkeypatch,
            GetDiscogsSnapshotResult(username="attritus", total_items=0, recent=()),
        )

        out = await connectors_read.handle_get_discogs_snapshot({}, _CTX)

        assert isinstance(out, dict)
        assert out["total_items"] == 0
        assert out["recent"] == []

    async def test_bad_recent_limit_raises_tool_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(
            monkeypatch,
            GetDiscogsSnapshotResult(username="attritus", total_items=0, recent=()),
        )

        with pytest.raises(ToolExecutionError):
            await connectors_read.handle_get_discogs_snapshot(
                {"recent_limit": "ten"}, _CTX
            )
