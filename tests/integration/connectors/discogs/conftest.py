"""Fixtures for Discogs client transport-boundary tests.

Builds a real ``DiscogsAPIClient`` whose factory-built httpx2 client has its
transport swapped for a ``MockTransport`` handler — base URL, headers
(User-Agent included), event hooks, and auth wiring all stay the genuine
article, so every test exercises the real auth/retry/parse path with no
network.

Payload shapes are reconciled to a live, token-authenticated 2026-08-22
probe (identity, an empty collection page, release 249504, master 96559) —
the same key sets as the unit model tests' fixtures, sized for pagination
scenarios.

Retry sleeps are captured instead of slept: the tenacity policy's ``sleep``
is replaced with a recorder, so ``RetryAfterWait`` stays real and tests can
assert the exact honored ``Retry-After`` without waiting on wall time.
"""

import httpx2
import pytest

from tests.fixtures import make_discogs_collection_page, make_discogs_release
from tests.fixtures.connector_transport import (
    FakeTokenStorage,
    Handler,
    sleep_recorder,
)

DISCOGS_TOKEN = "test-discogs-token"
TEST_USER_ID = "discogs-test-user"
TEST_USERNAME = "example"


@pytest.fixture(autouse=True)
def fresh_queue():
    """Each test gets a fresh instance-wide Discogs queue."""
    from src.infrastructure.connectors.discogs.pacer import reset_discogs_queue

    reset_discogs_queue()
    yield
    reset_discogs_queue()


@pytest.fixture
def storage() -> FakeTokenStorage:
    return FakeTokenStorage(token={"access_token": DISCOGS_TOKEN, "extra_data": {}})


@pytest.fixture
def make_client(storage: FakeTokenStorage, retry_sleeps: list[float]):
    """Factory building a DiscogsAPIClient over a MockTransport handler."""
    from src.infrastructure.connectors.discogs.client import DiscogsAPIClient

    def _make(handler: Handler, auth: httpx2.Auth | None = None) -> DiscogsAPIClient:
        client = DiscogsAPIClient(auth=auth)
        # Capture waits instead of sleeping; RetryAfterWait stays real.
        client._retry_policy.sleep = sleep_recorder(retry_sleeps)
        client._storage = storage
        client._user_id = TEST_USER_ID
        # Swap ONLY the transport — factory headers/auth/hooks stay real.
        client._client._transport = httpx2.MockTransport(handler)
        return client

    return _make


# -------------------------------------------------------------------------
# Canned payload builders (PROVISIONAL shapes — replace after the live probe)
# -------------------------------------------------------------------------


def identity_payload(username: str = TEST_USERNAME) -> dict[str, object]:
    # Redacted real capture shape: /oauth/identity returns exactly these keys.
    return {
        "id": 12345,
        "username": username,
        "resource_url": f"https://api.discogs.com/users/{username}",
        "consumer_name": "Mixd",
    }


def collection_release_payload(instance_id: int) -> dict[str, object]:
    return make_discogs_release(
        f"Release {instance_id}",
        release_id=1000000 + instance_id,
        instance_id=instance_id,
        year=2001,
        artists=[{"name": "Daft Punk", "anv": "", "join": ""}],
        labels=[{"name": "Virgin", "catno": "CAT-1"}],
        formats=[{"name": "Vinyl", "qty": "1", "descriptions": ["LP"]}],
        date_added="2024-04-15T08:00:00-07:00",
    )


def collection_page_payload(
    page: int, pages: int, per_page: int, items: int
) -> dict[str, object]:
    start = (page - 1) * per_page
    count = max(0, min(per_page, items - start))
    return make_discogs_collection_page(
        [collection_release_payload(start + offset + 1) for offset in range(count)],
        page=page,
        pages=pages,
        per_page=per_page,
        items=items,
    )


def empty_collection_page_payload() -> dict[str, object]:
    """Verbatim real capture: an authenticated user with zero collection items."""
    return make_discogs_collection_page(per_page=3)


def release_payload(release_id: int = 1477251) -> dict[str, object]:
    return {
        "id": release_id,
        "title": "Discovery",
        "year": 2001,
        "artists": [{"name": "Daft Punk", "anv": "", "join": ""}],
        "labels": [{"name": "Virgin", "catno": "CAT-1"}],
        "formats": [{"name": "Vinyl", "qty": "2", "descriptions": ["LP"]}],
        "genres": ["Electronic"],
        "styles": ["House"],
        "tracklist": [
            # "type_" mirrors the real wire key (release 249504 capture) —
            # present but unconsumed; must not break parsing.
            {
                "position": "A1",
                "type_": "track",
                "title": "One More Time",
                "duration": "",
            },
        ],
    }


def master_payload(master_id: int = 4422) -> dict[str, object]:
    return {
        "id": master_id,
        "title": "Discovery",
        "year": 2001,
        "main_release": 1477251,
    }
