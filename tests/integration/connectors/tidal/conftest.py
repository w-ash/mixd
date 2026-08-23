"""Fixtures for Tidal client transport-boundary tests.

Builds a real ``TidalAPIClient`` whose factory-built httpx2 client has its
transport swapped for a ``MockTransport`` handler — base URL, headers, event
hooks, ``TidalBearerAuth``, and the ``TidalTokenManager`` wiring all stay
the genuine article, so every test exercises the real auth/retry/parse path
with no network.

Two deliberate substitutions keep the tests hermetic:

- Token storage and the user-context lookup are monkeypatched at their
  source modules BEFORE construction (``__attrs_post_init__`` re-imports
  them per instance), so the token manager reads a ``FakeTokenStorage``
  holding a far-future token — no refresh is ever due.
- ``TidalTokenManager.force_refresh`` is replaced with a recorder returning
  the same access token: the real implementation reaches the Postgres
  advisory-lock single-flight guard, and the 401-replay test only needs
  "a forced refresh happened, the replay still 401'd".

Retry sleeps are captured instead of slept: the tenacity policy's ``sleep``
is replaced with a recorder, so ``RetryAfterWait`` stays real and tests can
assert the exact honored ``Retry-After`` without waiting on wall time.

Payload shapes are T2's PROVISIONAL spec-derived fixtures (see
``oas_models.py``) — the v0.11.3 T7 probe reconciles them to the live wire.
"""

import time

import httpx2
import pytest

from src.infrastructure.connectors.tidal.auth import TidalTokenManager
from tests.fixtures.connector_transport import (
    FakeTokenStorage,
    Handler,
    sleep_recorder,
)

TIDAL_ACCESS_TOKEN = "test-tidal-access-token"
TEST_USER_ID = "tidal-test-user"


@pytest.fixture
def storage() -> FakeTokenStorage:
    """A connected user: valid access token, expiry far beyond the buffer."""
    return FakeTokenStorage(
        token={
            "access_token": TIDAL_ACCESS_TOKEN,
            "refresh_token": "test-tidal-refresh-token",
            "expires_at": int(time.time()) + 3600,
        }
    )


@pytest.fixture
def forced_refreshes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record forced refreshes instead of running the Postgres-backed guard.

    Autoused via ``make_client`` depending on it: any test that trips a 401
    exercises the bearer auth's refresh-and-replay without touching the
    advisory-lock repository. The same token comes back, so a persistent
    401 stays a persistent 401 — exactly the "surviving 401 is real" case.
    """
    refreshes: list[str] = []

    async def record_force_refresh(_self: TidalTokenManager) -> str:
        refreshes.append(TIDAL_ACCESS_TOKEN)
        return TIDAL_ACCESS_TOKEN

    monkeypatch.setattr(TidalTokenManager, "force_refresh", record_force_refresh)
    return refreshes


@pytest.fixture
def make_client(
    storage: FakeTokenStorage,
    retry_sleeps: list[float],
    forced_refreshes: list[str],
    monkeypatch: pytest.MonkeyPatch,
):
    """Factory building a TidalAPIClient over a MockTransport handler."""
    del forced_refreshes  # dependency only — armed before any client exists
    from src.infrastructure.connectors.tidal.client import TidalAPIClient

    monkeypatch.setattr(
        "src.infrastructure.connectors._shared.token_storage.get_token_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        "src.infrastructure.persistence.database.user_context."
        "get_current_user_id_from_context",
        lambda: TEST_USER_ID,
    )

    def _make(handler: Handler) -> TidalAPIClient:
        client = TidalAPIClient()
        # Capture waits instead of sleeping; RetryAfterWait stays real.
        client._retry_policy.sleep = sleep_recorder(retry_sleeps)
        # Swap ONLY the transport — factory headers/auth/hooks stay real.
        client._client._transport = httpx2.MockTransport(handler)
        return client

    return _make


# -------------------------------------------------------------------------
# Canned payload builders (PROVISIONAL shapes — reconciled by the T7 probe)
# -------------------------------------------------------------------------


def track_resource_payload(
    track_id: str, isrc: str | None = "QMJMT1701229"
) -> dict[str, object]:
    attributes: dict[str, object] = {
        "title": f"Track {track_id}",
        "duration": "PT2M58S",
        "explicit": False,
    }
    if isrc is not None:
        attributes["isrc"] = isrc
    return {"id": track_id, "type": "tracks", "attributes": attributes}


def tracks_page_payload(
    track_ids: list[str], self_url: str, next_url: str | None
) -> dict[str, object]:
    links: dict[str, object] = {"self": self_url}
    if next_url is not None:
        links["next"] = next_url
    return {
        "data": [track_resource_payload(track_id) for track_id in track_ids],
        "links": links,
    }


def single_track_payload(
    track_id: str, replacement_id: str | None = None
) -> dict[str, object]:
    resource = track_resource_payload(track_id)
    if replacement_id is not None:
        resource["relationships"] = {
            "replacement": {
                "data": {"id": replacement_id, "type": "tracks"},
                "links": {"self": f"/tracks/{track_id}/relationships/replacement"},
            },
        }
    return {"data": resource, "links": {"self": f"/tracks/{track_id}"}}


def collection_item_payload(track_id: str) -> dict[str, object]:
    return {
        "id": track_id,
        "type": "tracks",
        "meta": {"addedAt": "2026-08-01T12:34:56.789Z"},
    }


def collection_items_page_payload(
    track_ids: list[str], next_cursor: str | None
) -> dict[str, object]:
    self_url = "/userCollectionTracks/me/relationships/items"
    links: dict[str, object] = {"self": self_url}
    if next_cursor is not None:
        links["next"] = f"{self_url}?sort=-addedAt&page[cursor]={next_cursor}"
    return {
        "data": [collection_item_payload(track_id) for track_id in track_ids],
        "links": links,
    }


def error_payload(status: str, detail: str) -> dict[str, object]:
    return {"errors": [{"status": status, "code": "TEST", "detail": detail}]}
