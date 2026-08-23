"""Tidal API client behavior at the transport boundary (MockTransport).

Covers: the bearer header on every request (real ``TidalBearerAuth`` over a
fake token store), 401-after-replay surfacing as ``TidalAuthRequiredError``
(the bearer auth already forced one refresh, so a surviving 401 is real),
429 + ``Retry-After`` honored via recorded tenacity sleeps (Retry-After is
Tidal's ONLY rate signal — no invented backoff numbers), 429 without the
header falling back to the default exponential policy, cursor pagination
following ``links.next`` to completion with a page-cap runaway guard,
one-ISRC-per-request enforcement, single-track fetch with the
``include=artists,replacement`` parameter (artists always ride along —
canonical minting needs their side-loaded names), and collection items paging with a
server-controlled page size (no size parameter is ever sent).
"""

import httpx2
import pytest

from src.config import settings
from src.domain.exceptions import TidalAuthRequiredError
from tests.fixtures.connector_transport import recording
from tests.integration.connectors.tidal.conftest import (
    TIDAL_ACCESS_TOKEN,
    collection_items_page_payload,
    error_payload,
    single_track_payload,
    tracks_page_payload,
)

ISRC = "QMJMT1701229"


def three_page_tracks_handler(request: httpx2.Request) -> httpx2.Response:
    """/tracks paginated over three cursor pages: c2, c3, then no next."""
    base = f"/tracks?countryCode=US&filter[isrc]={ISRC}"
    cursor = request.url.params.get("page[cursor]")
    if cursor is None:
        payload = tracks_page_payload(["1", "2"], base, f"{base}&page[cursor]=c2")
    elif cursor == "c2":
        payload = tracks_page_payload(["3", "4"], base, f"{base}&page[cursor]=c3")
    else:
        payload = tracks_page_payload(["5"], base, None)
    return httpx2.Response(200, json=payload)


class TestBearerAuth:
    async def test_every_request_carries_the_bearer_header(self, make_client):
        handler, requests = recording(three_page_tracks_handler)
        client = make_client(handler)

        _ = await client.get_tracks_by_isrc(ISRC, "US")

        assert len(requests) == 3
        for request in requests:
            assert request.headers["Authorization"] == f"Bearer {TIDAL_ACCESS_TOKEN}"

    async def test_401_after_replay_raises_auth_required(
        self, make_client, forced_refreshes: list[str]
    ):
        handler, requests = recording(
            lambda _request: httpx2.Response(
                401, json=error_payload("401", "The access token expired")
            )
        )
        client = make_client(handler)

        with pytest.raises(TidalAuthRequiredError):
            _ = await client.get_track("12345", "US")

        # Original request + the bearer auth's one forced-refresh replay —
        # then the surviving 401 surfaces as the typed error, no retry loop.
        assert len(requests) == 2
        assert forced_refreshes == [TIDAL_ACCESS_TOKEN]


class TestRateLimitRetry:
    async def test_429_retry_after_is_honored(
        self, make_client, retry_sleeps: list[float]
    ):
        calls = 0

        def flaky(_request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx2.Response(
                    429,
                    headers={"Retry-After": "7"},
                    json=error_payload("429", "Too many requests"),
                )
            return httpx2.Response(200, json=single_track_payload("12345"))

        client = make_client(flaky)

        track = await client.get_track("12345", "US")

        assert track is not None
        assert calls == 2
        # RetryAfterWait sleeps the stated window plus a 1s margin.
        assert retry_sleeps == [8.0]

    async def test_429_without_header_falls_back_to_default_policy(
        self, make_client, retry_sleeps: list[float]
    ):
        calls = 0

        def flaky(_request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx2.Response(
                    429, json=error_payload("429", "Too many requests")
                )
            return httpx2.Response(200, json=single_track_payload("12345"))

        client = make_client(flaky)

        track = await client.get_track("12345", "US")

        assert track is not None
        assert calls == 2
        # No header → exponential fallback (base delay + up-to-1s jitter),
        # never the Retry-After path's window-plus-margin value.
        assert len(retry_sleeps) == 1
        base = settings.api.tidal.retry_base_delay
        assert 0.0 <= retry_sleeps[0] <= base + 1.0


class TestCursorPagination:
    async def test_follows_next_links_to_completion(self, make_client):
        handler, requests = recording(three_page_tracks_handler)
        client = make_client(handler)

        tracks = await client.get_tracks_by_isrc(ISRC, "US")

        assert len(requests) == 3
        # First request: our own params; the rest follow links.next verbatim.
        assert requests[0].url.params["filter[isrc]"] == ISRC
        assert requests[0].url.params["countryCode"] == "US"
        assert "page[cursor]" not in requests[0].url.params
        assert requests[1].url.params["page[cursor]"] == "c2"
        assert requests[2].url.params["page[cursor]"] == "c3"
        # links.next is relative to the API base — the /v2 prefix survives.
        assert requests[1].url.path.startswith("/v2/")
        # Aggregated in order, stopping when next is absent.
        assert [track.id for track in tracks] == ["1", "2", "3", "4", "5"]

    async def test_single_page_stops_after_one_request(self, make_client):
        def one_page(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200, json=tracks_page_payload(["1"], "/tracks", None)
            )

        handler, requests = recording(one_page)
        client = make_client(handler)

        tracks = await client.get_tracks_by_isrc(ISRC, "US")

        assert len(requests) == 1
        assert [track.id for track in tracks] == ["1"]

    async def test_page_cap_guards_against_runaway_cursors(
        self, make_client, monkeypatch: pytest.MonkeyPatch
    ):
        # Behavior probed with a shrunk cap (5) to keep the fast suite fast;
        # the real value is pinned below.
        from src.infrastructure.connectors.tidal import client as client_module

        monkeypatch.setattr(client_module, "MAX_CURSOR_PAGES", 5)

        def endless(request: httpx2.Request) -> httpx2.Response:
            cursor = request.url.params.get("page[cursor]", "c0")
            page = int(cursor.removeprefix("c"))
            return httpx2.Response(
                200,
                json=tracks_page_payload(
                    [str(page)], "/tracks", f"/tracks?page[cursor]=c{page + 1}"
                ),
            )

        handler, requests = recording(endless)
        client = make_client(handler)

        tracks = await client.get_tracks_by_isrc(ISRC, "US")

        assert len(requests) == 5
        assert len(tracks) == 5

    def test_page_cap_is_fifty(self):
        from src.infrastructure.connectors.tidal.client import MAX_CURSOR_PAGES

        assert MAX_CURSOR_PAGES == 50


class TestOneIsrcPerRequest:
    @pytest.mark.parametrize(
        "bad_input",
        ["QMJMT1701229,USUM72309818", "QMJMT1701229 USUM72309818", "", "  "],
    )
    async def test_multiple_or_empty_codes_are_rejected_before_any_request(
        self, make_client, bad_input: str
    ):
        # Multi-ISRC filter batching returns ONE match per code — a silent
        # partial answer. The client refuses the shape outright.
        handler, requests = recording(three_page_tracks_handler)
        client = make_client(handler)

        with pytest.raises(ValueError, match="ISRC"):
            _ = await client.get_tracks_by_isrc(bad_input, "US")

        assert requests == []


class TestGetTrack:
    async def test_requests_the_replacement_include_by_default(self, make_client):
        handler, requests = recording(
            lambda _request: httpx2.Response(
                200, json=single_track_payload("12345", replacement_id="99999")
            )
        )
        client = make_client(handler)

        document = await client.get_track("12345", "US")

        assert requests[0].url.params["include"] == "artists,replacement"
        assert requests[0].url.params["countryCode"] == "US"
        assert document is not None
        track = document.data
        assert track is not None
        assert track.relationships is not None
        assert track.relationships.replacement is not None
        assert track.relationships.replacement.data is not None
        assert track.relationships.replacement.data.id == "99999"

    async def test_replacement_include_can_be_disabled_artists_stay(self, make_client):
        handler, requests = recording(
            lambda _request: httpx2.Response(200, json=single_track_payload("12345"))
        )
        client = make_client(handler)

        _ = await client.get_track("12345", "US", include_replacement=False)

        assert requests[0].url.params["include"] == "artists"

    async def test_dead_id_404_reads_as_none(self, make_client):
        handler, requests = recording(
            lambda _request: httpx2.Response(
                404, json=error_payload("404", "Track not found")
            )
        )
        client = make_client(handler)

        track = await client.get_track("gone", "US")

        assert track is None
        assert len(requests) == 1  # not_found fails fast, no retries


class TestCollectionItems:
    async def test_one_page_returns_items_and_next_cursor(self, make_client):
        handler, requests = recording(
            lambda _request: httpx2.Response(
                200, json=collection_items_page_payload(["1", "2", "3"], "c2")
            )
        )
        client = make_client(handler)

        page = await client.get_collection_track_items()

        assert len(requests) == 1
        request = requests[0]
        assert request.url.path.endswith("/userCollectionTracks/me/relationships/items")
        assert request.url.params["sort"] == "-addedAt"
        # Page size is server-controlled — no size parameter is ever sent.
        assert "page[size]" not in request.url.params
        assert "limit" not in request.url.params
        assert page is not None
        assert [item.id for item in page.items] == ["1", "2", "3"]
        assert page.next_cursor == "c2"

    async def test_cursor_is_forwarded_and_last_page_has_none(self, make_client):
        handler, requests = recording(
            lambda _request: httpx2.Response(
                200, json=collection_items_page_payload(["4"], None)
            )
        )
        client = make_client(handler)

        page = await client.get_collection_track_items(cursor="c2")

        assert requests[0].url.params["page[cursor]"] == "c2"
        assert page is not None
        assert page.next_cursor is None

    async def test_added_at_survives_to_the_item_meta(self, make_client):
        handler, _requests = recording(
            lambda _request: httpx2.Response(
                200, json=collection_items_page_payload(["1"], None)
            )
        )
        client = make_client(handler)

        page = await client.get_collection_track_items()

        assert page is not None
        assert page.items[0].meta is not None
        assert page.items[0].meta.added_at.year == 2026


class TestFavoritesCount:
    async def test_meta_total_surfaces_on_the_page(self, make_client):
        # The spec's pagination prose: collection sizes, when available,
        # appear as meta.total (and may be approximate). The relationship
        # document schema does not owe us one — absent reads as None.
        def with_total(_request: httpx2.Request) -> httpx2.Response:
            payload = collection_items_page_payload(["1"], None)
            payload["meta"] = {"total": 1204}
            return httpx2.Response(200, json=payload)

        client = make_client(with_total)

        page = await client.get_collection_track_items()

        assert page is not None
        assert page.total == 1204

    async def test_missing_meta_total_reads_as_none(self, make_client):
        client = make_client(
            lambda _request: httpx2.Response(
                200, json=collection_items_page_payload(["1"], None)
            )
        )

        page = await client.get_collection_track_items()

        assert page is not None
        assert page.total is None

    async def test_save_favorites_count_merges_extra_data(
        self, make_client, storage
    ) -> None:
        client = make_client(lambda _request: httpx2.Response(500))
        storage.token = {
            "access_token": TIDAL_ACCESS_TOKEN,
            "refresh_token": "rt-1",
            "extra_data": {"authorized_at": 1_755_000_000, "favorites_count": 1},
        }

        await client.save_favorites_count(7)

        token = storage.token
        assert token is not None
        assert token["extra_data"]["favorites_count"] == 7
        # Sibling keys survive the refresh.
        assert token["extra_data"]["authorized_at"] == 1_755_000_000
        # The narrow write never rewrites token columns: a stale loaded
        # refresh token can no longer clobber a concurrent rotation.
        assert storage.saved == []
        assert token["refresh_token"] == "rt-1"

    async def test_save_favorites_count_noop_without_token(
        self, make_client, storage
    ) -> None:
        client = make_client(lambda _request: httpx2.Response(500))
        storage.token = None

        await client.save_favorites_count(7)

        assert storage.saved == []
        assert storage.token is None
