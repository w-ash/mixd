"""Unit tests for the Discogs collection snapshot use case (v0.11.1).

The snapshot is a raw read — zero canonical writes by design (import-with-
matching is v0.13.1's job). The connector is mocked behind the
``DiscogsCollectionConnector`` capability protocol: the empty collection is
the primary acceptance path (the live account has 0 items), the count
refresh is best-effort (a save failure never fails the read), and connector
lifecycle belongs to the UoW (``__aexit__`` closes cached connectors), so
the use case must never close it itself.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.get_discogs_snapshot import (
    GetDiscogsSnapshotCommand,
    GetDiscogsSnapshotUseCase,
)
from src.domain.exceptions import ConnectorSyncError, DiscogsAuthRequiredError
from tests.fixtures import make_mock_uow

_USER = "default"


def _page(items: int, releases: list[dict[str, object]]) -> dict[str, object]:
    return {
        "pagination": {
            "page": 1,
            "pages": 1,
            "per_page": 10,
            "items": items,
            "urls": {"next": None},
        },
        "releases": releases,
    }


def _release(
    title: str,
    *,
    artists: list[dict[str, str]] | None = None,
    year: int = 1982,
    formats: list[dict[str, object]] | None = None,
    date_added: str = "2026-08-01T10:00:00-07:00",
) -> dict[str, object]:
    return {
        "id": 249504,
        "instance_id": 1,
        "date_added": date_added,
        "basic_information": {
            "id": 249504,
            "title": title,
            "year": year,
            "artists": artists or [{"name": "Duran Duran", "anv": "", "join": ""}],
            "labels": [{"name": "EMI", "catno": "EMC 3411"}],
            "formats": formats
            or [{"name": "Vinyl", "qty": "1", "descriptions": ["LP", "Album"]}],
        },
    }


def _make_connector(
    *,
    username: str | None = "attritus",
    page: dict[str, object] | None = None,
    live_username: str | None = None,
) -> AsyncMock:
    connector = AsyncMock()
    connector.get_stored_username.return_value = username
    connector.get_collection_page_data.return_value = page
    if username is None and live_username is None:
        # No stored token at all: the client raises before any request.
        connector.fetch_username.side_effect = DiscogsAuthRequiredError
    else:
        connector.fetch_username.return_value = live_username
    return connector


def _make_uow(connector: AsyncMock) -> MagicMock:
    provider = MagicMock()
    provider.get_connector.return_value = connector
    return make_mock_uow(connector_provider=provider)


async def _execute(connector: AsyncMock, recent_limit: int = 10):
    uow = _make_uow(connector)
    command = GetDiscogsSnapshotCommand(user_id=_USER, recent_limit=recent_limit)
    result = await GetDiscogsSnapshotUseCase().execute(command, uow)
    return result, uow


class TestEmptyCollection:
    async def test_zero_state_is_a_normal_result(self) -> None:
        connector = _make_connector(page=_page(0, []))

        result, _uow = await _execute(connector)

        assert result.username == "attritus"
        assert result.total_items == 0
        assert result.recent == ()

    async def test_no_canonical_writes(self) -> None:
        connector = _make_connector(page=_page(0, []))

        _result, uow = await _execute(connector)

        uow.get_track_repository.return_value.save_batch.assert_not_called()
        assert uow.get_track_repository.return_value.mock_calls == []
        assert uow.get_connector_repository.return_value.mock_calls == []

    async def test_use_case_never_closes_the_connector(self) -> None:
        # Lifecycle belongs to the UoW: the connector is cached on it and
        # closed by its __aexit__ — an aclose here would be a double-close.
        connector = _make_connector(page=_page(0, []))

        await _execute(connector)

        connector.aclose.assert_not_awaited()


class TestPopulatedCollection:
    async def test_items_mapped_in_returned_order(self) -> None:
        releases = [
            _release("Rio"),
            _release(
                "Under Pressure",
                artists=[
                    {"name": "Queen", "anv": "", "join": "&"},
                    {"name": "David Bowie", "anv": "", "join": ""},
                ],
                year=0,
                formats=[
                    {"name": "Vinyl", "qty": "2", "descriptions": ['7"']},
                ],
                date_added="2026-08-02T09:00:00-07:00",
            ),
        ]
        connector = _make_connector(page=_page(2, releases))

        result, _uow = await _execute(connector)

        assert result.total_items == 2
        assert [i.title for i in result.recent] == ["Rio", "Under Pressure"]
        first, second = result.recent
        assert first.artists == "Duran Duran"
        assert first.year == 1982
        assert first.formats == "Vinyl (LP, Album)"
        assert first.date_added == "2026-08-01T10:00:00-07:00"
        # Join phrases thread the credits; year 0 means "unknown" → None.
        assert second.artists == "Queen & David Bowie"
        assert second.year is None
        assert second.formats == '2x Vinyl (7")'

    async def test_recent_limit_bounds_page_and_items(self) -> None:
        releases = [_release(f"R{i}") for i in range(3)]
        connector = _make_connector(page=_page(40, releases))

        result, _uow = await _execute(connector, recent_limit=2)

        connector.get_collection_page_data.assert_awaited_once_with(
            "attritus", page=1, per_page=2
        )
        assert result.total_items == 40
        assert len(result.recent) == 2

    async def test_item_without_basic_information_is_skipped(self) -> None:
        # The boundary model allows basic_information to be absent (dumped as
        # None): such an item has nothing to display, so the snapshot skips
        # it — the good items still render and the total stays authoritative
        # from pagination. Never an exception, never a 500.
        malformed = dict(_release("ignored"))
        malformed["basic_information"] = None
        connector = _make_connector(page=_page(3, [_release("Rio"), malformed]))

        result, _uow = await _execute(connector)

        assert result.total_items == 3
        assert [i.title for i in result.recent] == ["Rio"]

    async def test_count_refresh_saved(self) -> None:
        connector = _make_connector(page=_page(2, [_release("Rio")]))

        await _execute(connector)

        connector.save_collection_count.assert_awaited_once_with(2)

    async def test_count_refresh_failure_is_swallowed(self) -> None:
        connector = _make_connector(page=_page(1, [_release("Rio")]))
        connector.save_collection_count.side_effect = RuntimeError("db down")

        result, _uow = await _execute(connector)

        assert result.total_items == 1


class TestUsernameFallback:
    """A stored token missing ``account_name`` must not read as disconnected.

    The status probe reports "connected" whenever a token row exists, so the
    snapshot re-derives the username live instead of raising not-connected.
    """

    async def test_missing_stored_username_falls_back_to_live_identity(self) -> None:
        connector = _make_connector(
            username=None, live_username="wash", page=_page(1, [_release("Rio")])
        )

        result, _uow = await _execute(connector)

        assert result.username == "wash"
        connector.fetch_username.assert_awaited_once_with()
        connector.get_collection_page_data.assert_awaited_once_with(
            "wash", page=1, per_page=10
        )

    async def test_stored_username_skips_the_live_probe(self) -> None:
        connector = _make_connector(page=_page(0, []))

        await _execute(connector)

        connector.fetch_username.assert_not_awaited()

    async def test_unreachable_identity_raises_sync_error(self) -> None:
        connector = _make_connector(username=None, live_username=None)
        connector.fetch_username.side_effect = None
        connector.fetch_username.return_value = None

        with pytest.raises(ConnectorSyncError):
            await _execute(connector)


class TestErrors:
    async def test_not_connected_raises_auth_required(self) -> None:
        # No token stored at all: the live-identity fallback raises the
        # auth-required error from the client before any request is sent.
        connector = _make_connector(username=None)

        with pytest.raises(DiscogsAuthRequiredError):
            await _execute(connector)

        connector.get_collection_page_data.assert_not_awaited()
        connector.aclose.assert_not_awaited()

    async def test_transport_failure_raises_sync_error(self) -> None:
        connector = _make_connector(page=None)

        with pytest.raises(ConnectorSyncError):
            await _execute(connector)

        connector.aclose.assert_not_awaited()
