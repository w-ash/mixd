"""Unit tests for the ``get_discogs_snapshot`` / ``get_tidal_snapshot`` dispatchers.

Monkeypatches the ``run_get_*_snapshot`` entry points on ``connectors_read``
with fakes returning a canned snapshot Result and recording the arguments, so
the tests exercise projection shape, the user-data wrapping on
service-originated titles/artist credits, and the per-service ``recent_limit``
default and cap without a database or a connector.
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


def _fake_runner(
    result: object, calls: list[tuple[str, int]]
) -> Callable[..., Awaitable[object]]:
    async def _run(user_id: str, recent_limit: int = 10) -> object:
        calls.append((user_id, recent_limit))
        return result

    return _run


def _patch(monkeypatch: pytest.MonkeyPatch, result: object) -> list[tuple[str, int]]:
    """Stub both snapshot entry points; return the (user_id, recent_limit) calls."""
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        connectors_read, "run_get_discogs_snapshot", _fake_runner(result, calls)
    )
    monkeypatch.setattr(
        connectors_read, "run_get_tidal_snapshot", _fake_runner(result, calls)
    )
    return calls


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

    async def test_recent_limit_defaults_to_10_and_caps_at_50(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch(
            monkeypatch,
            GetDiscogsSnapshotResult(username="attritus", total_items=0, recent=()),
        )

        await connectors_read.handle_get_discogs_snapshot({}, _CTX)
        await connectors_read.handle_get_discogs_snapshot({"recent_limit": 50}, _CTX)
        with pytest.raises(ToolExecutionError, match="between 1 and 50"):
            await connectors_read.handle_get_discogs_snapshot(
                {"recent_limit": 51}, _CTX
            )
        with pytest.raises(ToolExecutionError, match="must be an integer"):
            await connectors_read.handle_get_discogs_snapshot(
                {"recent_limit": "ten"}, _CTX
            )

        assert calls == [("default", 10), ("default", 50)]


class TestGetTidalSnapshot:
    async def test_projects_snapshot_with_wrapped_user_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.application.use_cases.get_tidal_snapshot import (
            GetTidalSnapshotResult,
            TidalSnapshotItem,
        )

        _patch(
            monkeypatch,
            GetTidalSnapshotResult(
                total_items=2,
                recent=(
                    TidalSnapshotItem(
                        title="Rio",
                        artists="Duran Duran",
                        added_at="2026-08-01T12:34:56+00:00",
                    ),
                ),
            ),
        )

        out = await connectors_read.handle_get_tidal_snapshot({}, _CTX)

        assert isinstance(out, dict)
        assert out["total_items"] == 2
        recent = out["recent"]
        assert isinstance(recent, list)
        assert len(recent) == 1
        item = recent[0]
        assert isinstance(item, dict)
        # Tidal-originated free text reaches the model quoted as data.
        assert item["title"] == wrap("Rio")
        assert item["artists"] == wrap("Duran Duran")
        assert item["added_at"] == "2026-08-01T12:34:56+00:00"

    async def test_empty_collection_is_a_normal_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.application.use_cases.get_tidal_snapshot import GetTidalSnapshotResult

        _patch(monkeypatch, GetTidalSnapshotResult(total_items=0, recent=()))

        out = await connectors_read.handle_get_tidal_snapshot({}, _CTX)

        assert isinstance(out, dict)
        assert out["total_items"] == 0
        assert out["recent"] == []

    async def test_recent_limit_defaults_to_10_and_caps_at_25(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Each recent row costs one per-track Tidal lookup, so the cap is lower
        # than Discogs'.
        from src.application.use_cases.get_tidal_snapshot import GetTidalSnapshotResult

        calls = _patch(monkeypatch, GetTidalSnapshotResult(total_items=0, recent=()))

        await connectors_read.handle_get_tidal_snapshot({}, _CTX)
        await connectors_read.handle_get_tidal_snapshot({"recent_limit": 25}, _CTX)
        with pytest.raises(ToolExecutionError, match="between 1 and 25"):
            await connectors_read.handle_get_tidal_snapshot({"recent_limit": 26}, _CTX)
        with pytest.raises(ToolExecutionError, match="must be an integer"):
            await connectors_read.handle_get_tidal_snapshot(
                {"recent_limit": "ten"}, _CTX
            )

        assert calls == [("default", 10), ("default", 25)]
