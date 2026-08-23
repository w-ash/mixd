"""Unit tests for the Tidal favorites snapshot use case (v0.11.3).

The snapshot is a raw read — zero canonical writes by design (matching and
import are v0.13.x's job). The connector is mocked behind the
``TidalFavoritesConnector`` capability protocol: the empty collection is a
normal result (total 0, not an error), the count comes from the page's
``meta.total`` when Tidal serves one and a capped page walk otherwise, the
recent rows need per-track lookups (the relationship carries identifiers +
``addedAt`` only), the count refresh is best-effort (a save failure never
fails the read), and connector lifecycle belongs to the UoW (``__aexit__``
closes cached connectors), so the use case must never close it itself.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.get_tidal_snapshot import (
    GetTidalSnapshotCommand,
    GetTidalSnapshotUseCase,
)
from src.domain.exceptions import ConnectorSyncError, TidalAuthRequiredError
from tests.fixtures import make_mock_uow

_USER = "default"

_ADDED_AT = "2026-08-01T12:34:56+00:00"


def _item(track_id: str, added_at: str | None = _ADDED_AT) -> dict[str, object]:
    return {"id": track_id, "added_at": added_at}


def _page(
    track_ids: list[str],
    *,
    total: int | None = None,
    next_cursor: str | None = None,
) -> dict[str, object]:
    return {
        "items": [_item(track_id) for track_id in track_ids],
        "next_cursor": next_cursor,
        "total": total,
    }


def _display(title: str, artists: list[str]) -> dict[str, object]:
    return {"title": title, "artists": artists}


def _make_connector(
    *,
    pages: list[dict[str, object] | None] | None = None,
    displays: dict[str, dict[str, object] | None] | None = None,
) -> AsyncMock:
    connector = AsyncMock()
    if pages is not None:
        connector.get_collection_items_page.side_effect = pages
    else:
        connector.get_collection_items_page.return_value = None

    lookup = displays or {}

    async def _get_display(track_id: str) -> dict[str, object] | None:
        return lookup.get(track_id)

    connector.get_track_display_data.side_effect = _get_display
    return connector


def _make_uow(connector: AsyncMock) -> MagicMock:
    provider = MagicMock()
    provider.get_connector.return_value = connector
    return make_mock_uow(connector_provider=provider)


async def _execute(connector: AsyncMock, recent_limit: int = 10):
    uow = _make_uow(connector)
    command = GetTidalSnapshotCommand(user_id=_USER, recent_limit=recent_limit)
    result = await GetTidalSnapshotUseCase().execute(command, uow)
    return result, uow


class TestEmptyCollection:
    async def test_zero_state_is_a_normal_result(self) -> None:
        connector = _make_connector(pages=[_page([])])

        result, _uow = await _execute(connector)

        assert result.total_items == 0
        assert result.recent == ()

    async def test_no_canonical_writes(self) -> None:
        connector = _make_connector(pages=[_page([])])

        _result, uow = await _execute(connector)

        uow.get_track_repository.return_value.save_batch.assert_not_called()
        assert uow.get_track_repository.return_value.mock_calls == []
        assert uow.get_connector_repository.return_value.mock_calls == []

    async def test_empty_page_spends_no_track_lookups(self) -> None:
        connector = _make_connector(pages=[_page([])])

        await _execute(connector)

        connector.get_track_display_data.assert_not_awaited()

    async def test_use_case_never_closes_the_connector(self) -> None:
        # Lifecycle belongs to the UoW: the connector is cached on it and
        # closed by its __aexit__ — an aclose here would be a double-close.
        connector = _make_connector(pages=[_page([])])

        await _execute(connector)

        connector.aclose.assert_not_awaited()


class TestPopulatedCollection:
    async def test_items_mapped_in_returned_order(self) -> None:
        connector = _make_connector(
            pages=[_page(["t1", "t2"], total=2)],
            displays={
                "t1": _display("Rio", ["Duran Duran"]),
                "t2": _display("Under Pressure", ["Queen", "David Bowie"]),
            },
        )

        result, _uow = await _execute(connector)

        assert result.total_items == 2
        assert [i.title for i in result.recent] == ["Rio", "Under Pressure"]
        first, second = result.recent
        assert first.artists == "Duran Duran"
        assert first.added_at == _ADDED_AT
        assert second.artists == "Queen, David Bowie"

    async def test_recent_limit_caps_track_lookups(self) -> None:
        connector = _make_connector(
            pages=[_page(["t1", "t2", "t3"], total=40)],
            displays={f"t{i}": _display(f"T{i}", ["A"]) for i in range(1, 4)},
        )

        result, _uow = await _execute(connector, recent_limit=2)

        assert result.total_items == 40
        assert len(result.recent) == 2
        assert connector.get_track_display_data.await_count == 2

    async def test_recent_limit_is_capped_at_25_in_the_use_case(self) -> None:
        # The ceiling is enforced HERE, not just at the interface edge —
        # every recent row costs one Tidal request, so an oversized limit
        # from any caller must not fan out into an unbounded request burst.
        track_ids = [f"t{i}" for i in range(1, 41)]
        connector = _make_connector(
            pages=[_page(track_ids, total=40)],
            displays={tid: _display(tid.upper(), ["A"]) for tid in track_ids},
        )

        result, _uow = await _execute(connector, recent_limit=100)

        assert len(result.recent) == 25
        assert connector.get_track_display_data.await_count == 25

    async def test_unresolvable_track_is_skipped_not_fatal(self) -> None:
        # A favorites entry whose track lookup returns nothing (gone from
        # the catalog, transport hiccup) has nothing to display — the row is
        # skipped with a warning; the total stays authoritative.
        connector = _make_connector(
            pages=[_page(["gone", "t2"], total=2)],
            displays={"t2": _display("Rio", ["Duran Duran"])},
        )

        result, _uow = await _execute(connector)

        assert result.total_items == 2
        assert [i.title for i in result.recent] == ["Rio"]


class TestCountSource:
    async def test_meta_total_answers_without_a_walk(self) -> None:
        connector = _make_connector(
            pages=[_page(["t1"], total=1204, next_cursor="c2")],
            displays={"t1": _display("Rio", ["Duran Duran"])},
        )

        result, _uow = await _execute(connector)

        assert result.total_items == 1204
        connector.get_collection_items_page.assert_awaited_once_with(None)

    async def test_missing_total_walks_pages_and_sums(self) -> None:
        connector = _make_connector(
            pages=[
                _page(["t1", "t2"], next_cursor="c2"),
                _page(["t3", "t4"], next_cursor="c3"),
                _page(["t5"]),
            ],
            displays={f"t{i}": _display(f"T{i}", ["A"]) for i in range(1, 6)},
        )

        result, _uow = await _execute(connector)

        assert result.total_items == 5
        assert connector.get_collection_items_page.await_count == 3
        connector.get_collection_items_page.assert_any_await("c2")
        connector.get_collection_items_page.assert_any_await("c3")

    async def test_walk_transport_failure_raises_sync_error(self) -> None:
        # A partial walk misread as a complete count would poison the cached
        # number — the failure surfaces instead of silently truncating.
        connector = _make_connector(
            pages=[_page(["t1"], next_cursor="c2"), None],
            displays={"t1": _display("Rio", ["Duran Duran"])},
        )

        with pytest.raises(ConnectorSyncError):
            await _execute(connector)


class TestCountRefresh:
    async def test_count_refresh_saved(self) -> None:
        connector = _make_connector(
            pages=[_page(["t1"], total=7)],
            displays={"t1": _display("Rio", ["Duran Duran"])},
        )

        await _execute(connector)

        connector.save_favorites_count.assert_awaited_once_with(7)

    async def test_count_refresh_failure_is_swallowed(self) -> None:
        connector = _make_connector(
            pages=[_page(["t1"], total=1)],
            displays={"t1": _display("Rio", ["Duran Duran"])},
        )
        connector.save_favorites_count.side_effect = RuntimeError("db down")

        result, _uow = await _execute(connector)

        assert result.total_items == 1


class TestErrors:
    async def test_not_connected_raises_auth_required(self) -> None:
        # No token stored: the client's token manager raises before any
        # request is sent — the use case lets it propagate untouched.
        connector = _make_connector()
        connector.get_collection_items_page.side_effect = TidalAuthRequiredError

        with pytest.raises(TidalAuthRequiredError):
            await _execute(connector)

        connector.aclose.assert_not_awaited()

    async def test_transport_failure_raises_sync_error(self) -> None:
        connector = _make_connector(pages=[None])

        with pytest.raises(ConnectorSyncError):
            await _execute(connector)

        connector.aclose.assert_not_awaited()
