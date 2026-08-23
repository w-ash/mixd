"""Discogs API client behavior at the transport boundary (MockTransport).

Covers: personal-access-token header on every request (lazy-loaded from
storage, or injected as a constructed auth strategy), the +url User-Agent
form, actionable 401, 429 + Retry-After retry, pagination to completion and
the 10,000-item ceiling, instance-wide serialization of concurrent calls,
and the image client bypassing auth and the queue.
"""

import asyncio

import httpx2
import pytest

from src import __version__
from src.domain.exceptions import ConnectorSyncError, DiscogsAuthRequiredError
from tests.integration.connectors.discogs.conftest import (
    DISCOGS_TOKEN,
    TEST_USERNAME,
    FakeTokenStorage,
    collection_page_payload,
    empty_collection_page_payload,
    identity_payload,
    master_payload,
    release_payload,
)


def recording(handler):
    """Wrap a handler so every request is captured for assertions."""
    requests: list[httpx2.Request] = []

    def _handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    return _handler, requests


def routed_handler(request: httpx2.Request) -> httpx2.Response:
    """Canned happy-path responses for the endpoints under test."""
    path = request.url.path
    if path == "/oauth/identity":
        return httpx2.Response(200, json=identity_payload())
    if path == f"/users/{TEST_USERNAME}/collection/folders/0/releases":
        page = int(request.url.params.get("page", "1"))
        per_page = int(request.url.params.get("per_page", "100"))
        return httpx2.Response(
            200,
            json=collection_page_payload(
                page=page, pages=3, per_page=per_page, items=250
            ),
        )
    if path.startswith("/releases/"):
        return httpx2.Response(200, json=release_payload(int(path.rsplit("/", 1)[1])))
    if path.startswith("/masters/"):
        return httpx2.Response(200, json=master_payload(int(path.rsplit("/", 1)[1])))
    return httpx2.Response(404, json={"message": "not found"})


class TestAuth:
    async def test_every_request_carries_the_discogs_token_header(self, make_client):
        handler, requests = recording(routed_handler)
        client = make_client(handler)

        _ = await client.get_identity()
        _ = await client.get_release(1477251)
        _ = await client.get_master(4422)

        assert len(requests) == 3
        for request in requests:
            assert request.headers["Authorization"] == f"Discogs token={DISCOGS_TOKEN}"

    async def test_token_loads_from_storage_once(
        self, make_client, storage: FakeTokenStorage
    ):
        handler, _requests = recording(routed_handler)
        client = make_client(handler)

        _ = await client.get_identity()
        _ = await client.get_identity()

        assert storage.loads == [("discogs", "discogs-test-user")]

    async def test_missing_token_raises_before_any_request(
        self, make_client, storage: FakeTokenStorage
    ):
        storage.token = None
        handler, requests = recording(routed_handler)
        client = make_client(handler)

        with pytest.raises(DiscogsAuthRequiredError):
            _ = await client.get_identity()

        assert requests == []

    async def test_injected_auth_strategy_wins_over_storage(
        self, make_client, storage: FakeTokenStorage
    ):
        # The auth seam: any httpx2.Auth injected at construction is used
        # verbatim — storage is never consulted (OAuth 1.0a slots in here).
        class StubAuth(httpx2.Auth):
            def auth_flow(self, request: httpx2.Request):
                request.headers["Authorization"] = "OAuth stub-signature"
                yield request

        handler, requests = recording(routed_handler)
        client = make_client(handler, auth=StubAuth())

        identity = await client.get_identity()

        assert identity is not None
        assert requests[0].headers["Authorization"] == "OAuth stub-signature"
        assert storage.loads == []

    async def test_401_raises_actionable_auth_error_without_retry(self, make_client):
        handler, requests = recording(
            lambda _request: httpx2.Response(401, json={"message": "bad token"})
        )
        client = make_client(handler)

        with pytest.raises(DiscogsAuthRequiredError, match="personal access token"):
            _ = await client.get_identity()

        assert len(requests) == 1


class TestUserAgent:
    async def test_requests_carry_the_plus_url_user_agent(self, make_client):
        handler, requests = recording(routed_handler)
        client = make_client(handler)

        _ = await client.get_identity()

        user_agent = requests[0].headers["User-Agent"]
        assert user_agent.startswith(f"Mixd/{__version__} +http")
        assert "github.com/w-ash/mixd" in user_agent


class TestPagination:
    async def test_follows_pages_to_completion(self, make_client):
        handler, requests = recording(routed_handler)
        client = make_client(handler)

        releases = await client.get_all_collection_releases(TEST_USERNAME)

        assert len(requests) == 3
        assert [request.url.params["page"] for request in requests] == ["1", "2", "3"]
        assert len(releases) == 250
        # Order preserved across pages, no duplicates.
        assert [release.instance_id for release in releases] == list(range(1, 251))

    async def test_single_page_collection_stops_after_one_request(self, make_client):
        def one_page(request: httpx2.Request) -> httpx2.Response:
            per_page = int(request.url.params.get("per_page", "100"))
            return httpx2.Response(
                200,
                json=collection_page_payload(
                    page=1, pages=1, per_page=per_page, items=2
                ),
            )

        handler, requests = recording(one_page)
        client = make_client(handler)

        releases = await client.get_all_collection_releases(TEST_USERNAME)

        assert len(requests) == 1
        assert len(releases) == 2

    async def test_real_empty_collection_capture_returns_zero_releases(
        self, make_client
    ):
        # Verbatim real capture shape: items=0, pages=1, urls={} — an
        # authenticated user with an empty collection, not an error state.
        def empty(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json=empty_collection_page_payload())

        handler, requests = recording(empty)
        client = make_client(handler)

        releases = await client.get_all_collection_releases(TEST_USERNAME)

        assert len(requests) == 1
        assert releases == []

    async def test_stops_at_the_item_ceiling(
        self, make_client, monkeypatch: pytest.MonkeyPatch
    ):
        # Behavior probed with a shrunk ceiling (10) to keep the fast suite
        # fast; the real 10,000 value is pinned below.
        from src.infrastructure.connectors.discogs import client as client_module

        monkeypatch.setattr(client_module, "COLLECTION_ITEM_CEILING", 10)

        def endless(request: httpx2.Request) -> httpx2.Response:
            page = int(request.url.params.get("page", "1"))
            return httpx2.Response(
                200,
                json=collection_page_payload(
                    page=page, pages=500, per_page=5, items=2500
                ),
            )

        handler, requests = recording(endless)
        client = make_client(handler)

        releases = await client.get_all_collection_releases(TEST_USERNAME, per_page=5)

        assert len(requests) == 2  # 5 items/page: the walk stops once 10 are held
        assert len(releases) == 10

    def test_ceiling_is_discogs_documented_10k(self):
        from src.infrastructure.connectors.discogs.client import (
            COLLECTION_ITEM_CEILING,
        )

        assert COLLECTION_ITEM_CEILING == 10_000


class TestCollectionPageValidation:
    """Boundary validation of one collection page (the snapshot's seam)."""

    async def test_item_missing_basic_information_does_not_fail_the_page(
        self, make_client
    ):
        payload = collection_page_payload(page=1, pages=1, per_page=10, items=2)
        del payload["releases"][0]["basic_information"]

        def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json=payload)

        client = make_client(handler)

        page = await client.get_collection_page(TEST_USERNAME, per_page=10)

        assert page is not None
        assert page.pagination.items == 2
        assert page.releases[0].basic_information is None
        second = page.releases[1].basic_information
        assert second is not None
        assert second.title == "Release 2"

    async def test_unreadable_page_raises_connector_error_not_validation_error(
        self, make_client
    ):
        # A body Discogs served but we cannot read is an upstream-contract
        # failure: it must surface as the connector-flavored error the
        # callers already handle, never a raw ValidationError → 500.
        def garbage(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"pagination": "nope", "releases": 3})

        client = make_client(garbage)

        with pytest.raises(ConnectorSyncError):
            _ = await client.get_collection_page(TEST_USERNAME, per_page=10)


class TestRateLimitRetry:
    async def test_429_retry_after_is_honored_and_retried(
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
                    json={"message": "too many requests"},
                )
            return httpx2.Response(200, json=identity_payload())

        client = make_client(flaky)

        identity = await client.get_identity()

        assert identity is not None
        assert identity.username == TEST_USERNAME
        assert calls == 2
        # RetryAfterWait sleeps the stated window plus a 1s margin.
        assert retry_sleeps == [8.0]


class TestRateHeaderSelfCorrection:
    async def test_low_remaining_header_brakes_the_limiter(
        self, make_client, monkeypatch: pytest.MonkeyPatch
    ):
        # The client must run apply_rate_headers on every API response —
        # a low X-Discogs-Ratelimit-Remaining pauses the shared limiter.
        from src.infrastructure.connectors.discogs import pacer

        pauses: list[float] = []

        class RecordingLimiter:
            def pause_for(self, seconds: float) -> None:
                pauses.append(seconds)

        monkeypatch.setattr(
            pacer, "get_connector_rate_limiter", lambda _name: RecordingLimiter()
        )

        def low_headroom(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                headers={"X-Discogs-Ratelimit-Remaining": "2"},
                json=identity_payload(),
            )

        client = make_client(low_headroom)

        _ = await client.get_identity()

        assert pauses == [4.0]  # (LOW_WATER 5 - 2 + 1) * 1.0


class TestInstanceWideSerialization:
    async def test_concurrent_clients_serialize_through_one_queue(self, make_client):
        in_flight = 0
        concurrency_seen: list[int] = []

        async def slow_handler(_request: httpx2.Request) -> httpx2.Response:
            nonlocal in_flight
            in_flight += 1
            concurrency_seen.append(in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return httpx2.Response(200, json=identity_payload())

        # Two separate clients — the queue is instance-wide, not per-client.
        client_a = make_client(slow_handler)
        client_b = make_client(slow_handler)

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(client_a.get_identity())
            _ = tg.create_task(client_b.get_identity())

        assert concurrency_seen == [1, 1]


class TestImageClient:
    async def test_image_fetch_sends_no_auth_and_bypasses_the_queue(self):
        from src.infrastructure.connectors._shared.http_client import (
            make_discogs_image_client,
        )
        from src.infrastructure.connectors.discogs.pacer import get_discogs_queue

        requests: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(200, content=b"\x89PNG")

        image_client = make_discogs_image_client()
        image_client._transport = httpx2.MockTransport(handler)

        # Held API queue must not block an image fetch — separate bucket.
        async with get_discogs_queue():
            async with image_client:
                response = await image_client.get("/rel/1477251.jpg")

        assert response.status_code == 200
        assert "Authorization" not in requests[0].headers
        assert "github.com/w-ash/mixd" in requests[0].headers["User-Agent"]


class TestStoredTokenHelpers:
    """Storage-only helpers backing the collection snapshot (v0.11.1).

    Neither method may spend Discogs budget: no request leaves the client.
    """

    async def test_get_stored_username_reads_account_name(self, make_client) -> None:
        client = make_client(routed_handler)
        client._storage.token = {
            "access_token": DISCOGS_TOKEN,
            "account_name": "attritus",
        }

        assert await client.get_stored_username() == "attritus"

    async def test_get_stored_username_none_when_not_connected(
        self, make_client
    ) -> None:
        client = make_client(routed_handler)
        client._storage.token = None

        assert await client.get_stored_username() is None

    async def test_save_collection_count_merges_extra_data(self, make_client) -> None:
        client = make_client(routed_handler)
        client._storage.token = {
            "access_token": DISCOGS_TOKEN,
            "account_name": "attritus",
            "extra_data": {"validated_at": 1_755_000_000, "collection_count": 1},
        }

        await client.save_collection_count(7)

        token = client._storage.token
        assert token is not None
        assert token["extra_data"]["collection_count"] == 7
        # Sibling keys survive the refresh.
        assert token["extra_data"]["validated_at"] == 1_755_000_000
        # Narrow write: token columns never rewritten from a stale load.
        assert client._storage.saved == []
        assert token["access_token"] == DISCOGS_TOKEN

    async def test_save_collection_count_noop_without_token(self, make_client) -> None:
        client = make_client(routed_handler)
        client._storage.token = None

        await client.save_collection_count(7)

        assert client._storage.token is None

    async def test_save_account_name_backfills_and_preserves_siblings(
        self, make_client
    ) -> None:
        client = make_client(routed_handler)
        client._storage.token = {
            "access_token": DISCOGS_TOKEN,
            "extra_data": {"collection_count": 1},
        }

        await client.save_account_name("attritus")

        token = client._storage.token
        assert token is not None
        assert token["account_name"] == "attritus"
        assert token["access_token"] == DISCOGS_TOKEN
        assert token["extra_data"]["collection_count"] == 1
        # Narrow write: token columns never rewritten from a stale load.
        assert client._storage.saved == []

    async def test_save_account_name_noop_without_token(self, make_client) -> None:
        client = make_client(routed_handler)
        client._storage.token = None

        await client.save_account_name("attritus")

        assert client._storage.saved == []
        assert client._storage.token is None
