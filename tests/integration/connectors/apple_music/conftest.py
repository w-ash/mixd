"""Fixtures for Apple Music client transport-boundary tests.

Builds a real ``AppleMusicAPIClient`` whose httpx2 client rides on a
``MockTransport`` with a canned-response handler, a fake token storage holding
a Music User Token, and a stub developer-token provider — so every test
exercises the genuine auth/retry/parse wiring with no network.

Response payload shapes are REDACTED versions of live captures (probe
2026-08-22): structural keys are real; artwork URLs are placeholders and
``previews`` is dropped.
"""

from collections.abc import Callable, Mapping

import httpx2
import pytest
from tenacity import wait_none

from src.infrastructure.connectors._shared.token_storage import StoredToken

DEV_TOKEN = "test-dev-token"
MUSIC_USER_TOKEN = "test-music-user-token"
TEST_USER_ID = "apple-test-user"

type Handler = Callable[[httpx2.Request], httpx2.Response]


class FakeTokenStorage:
    """In-memory TokenStorage double recording loads and saves."""

    def __init__(self, token: StoredToken | None = None) -> None:
        self.token = token
        self.loads: list[tuple[str, str]] = []
        self.saved: list[tuple[str, str, StoredToken]] = []
        self.extra_updates: list[tuple[str, str, dict[str, object]]] = []
        self.fail_saves = False

    async def load_token(self, service: str, user_id: str) -> StoredToken | None:
        self.loads.append((service, user_id))
        return self.token

    async def save_token(
        self, service: str, user_id: str, token_data: StoredToken
    ) -> None:
        if self.fail_saves:
            raise RuntimeError("storage write failed")
        self.token = token_data
        self.saved.append((service, user_id, token_data))

    async def update_extra_data(
        self,
        service: str,
        user_id: str,
        updates: Mapping[str, object],
        *,
        account_name: str | None = None,
    ) -> None:
        if self.fail_saves:
            raise RuntimeError("storage write failed")
        self.extra_updates.append((service, user_id, dict(updates)))
        if self.token is None:
            return
        merged = dict(self.token.get("extra_data") or {})
        merged.update(updates)
        self.token["extra_data"] = merged
        if account_name is not None:
            self.token["account_name"] = account_name

    async def delete_token(self, service: str, user_id: str) -> None:
        self.token = None


class StubDeveloperTokenProvider:
    """Duck-typed stand-in for DeveloperTokenProvider — no key material."""

    def get_token(self) -> str:
        return DEV_TOKEN


@pytest.fixture(autouse=True)
def no_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic — no pacing sleeps from the shared limiter."""
    monkeypatch.setattr(
        "src.infrastructure.connectors.base.get_connector_rate_limiter",
        lambda _service_name: None,
    )


@pytest.fixture
def storage() -> FakeTokenStorage:
    return FakeTokenStorage(token={"access_token": MUSIC_USER_TOKEN, "extra_data": {}})


@pytest.fixture
def make_client(storage: FakeTokenStorage):
    """Factory building an AppleMusicAPIClient over a MockTransport handler."""
    from src.infrastructure.connectors._shared.http_client import (
        APPLE_MUSIC_API_BASE,
    )
    from src.infrastructure.connectors.apple_music.auth import (
        AppleMusicDeveloperAuth,
    )
    from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient

    def _make(handler: Handler) -> AppleMusicAPIClient:
        client = AppleMusicAPIClient()
        # Zero the backoff waits; predicate/classifier wiring stays real.
        client._retry_policy.wait = wait_none()
        client._storage = storage
        client._user_id = TEST_USER_ID
        client._client = httpx2.AsyncClient(
            base_url=APPLE_MUSIC_API_BASE,
            auth=AppleMusicDeveloperAuth(StubDeveloperTokenProvider()),
            transport=httpx2.MockTransport(handler),
        )
        return client

    return _make


# -------------------------------------------------------------------------
# Canned payload builders (shapes verified against the 2026-08-22 live probe)
# -------------------------------------------------------------------------


def song_payload(song_id: str = "1613600188", **attribute_overrides: object):
    attributes: dict[str, object] = {
        "albumName": "GUTS",
        "artistName": "Olivia Rodrigo",
        "artwork": {
            "bgColor": "151412",
            "height": 3000,
            "url": "https://artwork.example/{w}x{h}bb.jpg",
            "width": 3000,
        },
        "discNumber": 1,
        "durationInMillis": 219724,
        "genreNames": ["Pop", "Music"],
        "hasLyrics": True,
        "isrc": "USUM72309818",
        "name": "Vampire",
        "playParams": {"id": song_id, "kind": "song"},
        "releaseDate": "2023-06-30",
        "trackNumber": 1,
        "url": f"https://music.apple.com/us/album/x/1?i={song_id}",
    }
    attributes.update(attribute_overrides)
    return {
        "id": song_id,
        "type": "songs",
        "href": f"/v1/catalog/us/songs/{song_id}",
        "attributes": attributes,
    }


def songs_response(*song_ids: str) -> dict[str, object]:
    return {"data": [song_payload(song_id) for song_id in song_ids]}


def storefront_response(storefront_id: str = "us") -> dict[str, object]:
    return {"data": [{"id": storefront_id, "type": "storefronts"}]}


def invalid_mut_error_body() -> dict[str, object]:
    """The VERBATIM 403 body Apple returns for a dead/invalid Music User Token.

    Captured live 2026-08-22 (bad_mut_error.json) — note there is no
    token-specific marker string, just "Invalid authentication".
    """
    return {
        "errors": [
            {
                "code": "40300",
                "detail": "Invalid authentication",
                "id": "JXGSMLML5NSKKEUMZQFFIY7O4Y",
                "status": "403",
                "title": "Forbidden",
            }
        ]
    }
