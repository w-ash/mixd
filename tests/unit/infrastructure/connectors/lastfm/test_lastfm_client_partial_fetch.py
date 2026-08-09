"""The recent-tracks pagination never returns a silently partial span.

``get_recent_tracks`` walks pages until the requested limit or the last page;
a page whose request fails after retries used to break out and return the
rows gathered so far — indistinguishable from a complete quiet span to the
importer, which would then checkpoint past the hole. It now raises
``LastFMPartialFetchError`` instead.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.infrastructure.connectors.lastfm.client import (
    LastFMAPIClient,
    LastFMPartialFetchError,
)


def _client() -> LastFMAPIClient:
    return LastFMAPIClient(api_key="test-key", api_secret="test-secret")


def _entry(name: str):
    from src.infrastructure.connectors.lastfm.models import LastFMTrackEntry

    return LastFMTrackEntry.model_validate({
        "name": name,
        "artist": {"name": "Artist"},
        "date": {"uts": "1700000000"},
    })


class TestPartialFetchRaises:
    async def test_failed_page_mid_pagination_raises(self):
        """Page 1 lands, page 2's request fails after retries (suppressed to
        None by _api_call) — the fetch must fail loudly, not shrink."""
        client = _client()
        pages = {
            1: ([_entry("a"), _entry("b")], 3),
            2: None,  # suppressed request failure
        }

        async def _api_call(operation, impl, user, page, *args):
            return pages[page]

        with patch.object(
            LastFMAPIClient, "_api_call", new=AsyncMock(side_effect=_api_call)
        ):
            with pytest.raises(LastFMPartialFetchError, match="page 2/3"):
                _ = await client.get_recent_tracks(username="someone", limit=1000)

    async def test_first_page_failure_also_raises(self):
        """An empty return used to stand in for total failure — same hole."""
        client = _client()

        with patch.object(
            LastFMAPIClient, "_api_call", new=AsyncMock(return_value=None)
        ):
            with pytest.raises(LastFMPartialFetchError):
                _ = await client.get_recent_tracks(username="someone", limit=1000)

    async def test_validation_error_mid_pagination_raises(self):
        """Page 1 lands with total_pages=3; page 2's body fails boundary
        validation. Returning ``([], 1)`` for it reported an empty SUCCESS —
        total_pages collapsed to 1 and pages 1..N-1 came back as a complete
        fetch, which the importer then checkpointed past."""
        client = _client()
        bodies = {
            "1": {
                "recenttracks": {
                    "track": [
                        {
                            "name": "a",
                            "artist": {"name": "Artist"},
                            "date": {"uts": "1700000000"},
                        }
                    ],
                    "@attr": {"totalPages": "3", "page": "1", "total": "500"},
                }
            },
            "2": {"recenttracks": {"@attr": "not-a-mapping"}},
        }

        async def _passthrough(operation, impl, *args):
            return await impl(*args)

        async def _api_request(method, params, authenticated=False):
            return bodies[params["page"]]

        with (
            patch.object(
                LastFMAPIClient, "_api_call", new=AsyncMock(side_effect=_passthrough)
            ),
            patch.object(
                LastFMAPIClient,
                "_api_request",
                new=AsyncMock(side_effect=_api_request),
            ),
        ):
            with pytest.raises(LastFMPartialFetchError, match=r"page 2.*validation"):
                _ = await client.get_recent_tracks(username="someone", limit=1000)

    async def test_missing_recenttracks_node_raises(self):
        """A 200 body without the recenttracks node is an unexpected shape, not
        a quiet week — an empty page still carries the node."""
        client = _client()

        async def _passthrough(operation, impl, *args):
            return await impl(*args)

        with (
            patch.object(
                LastFMAPIClient, "_api_call", new=AsyncMock(side_effect=_passthrough)
            ),
            patch.object(
                LastFMAPIClient,
                "_api_request",
                new=AsyncMock(return_value={"unexpected": "shape"}),
            ),
        ):
            with pytest.raises(LastFMPartialFetchError, match="no recenttracks node"):
                _ = await client.get_recent_tracks(username="someone", limit=1000)

    async def test_complete_pagination_still_returns_all_rows(self):
        client = _client()
        pages = {
            1: ([_entry("a"), _entry("b")], 2),
            2: ([_entry("c")], 2),
        }

        async def _api_call(operation, impl, user, page, *args):
            return pages[page]

        with patch.object(
            LastFMAPIClient, "_api_call", new=AsyncMock(side_effect=_api_call)
        ):
            tracks = await client.get_recent_tracks(username="someone", limit=1000)

        assert [t.name for t in tracks] == ["a", "b", "c"]
