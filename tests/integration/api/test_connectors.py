"""Integration tests for connector status and Spotify-playlist endpoints.

Route-level coverage only: registry serialization, the Spotify-playlist
browse + import routes (via the ``mock_connector_provider`` fixture so no
live API calls leak from the integration env's real OAuth tokens), play
polling, disconnect scoping, and the Discogs token flow. Per-connector
status-probe behavior is unit-tested beside each connector in
``tests/unit/infrastructure/connectors/<service>/test_status.py``, and the
shared stored-token primitive in
``tests/unit/infrastructure/connectors/_shared/test_connector_status.py``.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
import time
from unittest.mock import AsyncMock, patch
import uuid

import httpx2
import pytest

from src.application.connector_protocols import UserPlaylistsConnector
from src.domain.entities.connector import ConnectorStatus
from src.domain.exceptions import DiscogsInvalidTokenError
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    get_token_storage,
)
from src.infrastructure.connectors.discovery import discover_connectors
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import DBConnectorPlaylist
from src.infrastructure.persistence.database.user_context import user_context
from src.interface.api.app import create_app
from src.interface.api.deps import get_current_user_id
from src.interface.api.rate_limit import InMemoryRateLimiter
import src.interface.api.routes.connectors as connectors_route
from tests.integration.api.conftest import _test_db_env


class TestGetConnectors:
    """GET /api/v1/connectors returns connector status array."""

    async def test_returns_all_registered_connectors(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/connectors")

        assert response.status_code == 200
        connectors = response.json()
        assert isinstance(connectors, list)
        names = {c["name"] for c in connectors}
        assert names == {
            "spotify",
            "lastfm",
            "musicbrainz",
            "apple_music",
            "discogs",
            "tidal",
        }

    async def test_discogs_registered_as_token_physical(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/connectors")

        discogs = next(c for c in response.json() if c["name"] == "discogs")
        assert discogs["display_name"] == "Discogs"
        assert discogs["auth_method"] == "token"
        assert discogs["category"] == "physical"
        assert discogs["capabilities"] == []

    async def test_tidal_registered_as_oauth_streaming(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/connectors")

        tidal = next(c for c in response.json() if c["name"] == "tidal")
        assert tidal["display_name"] == "TIDAL"
        assert tidal["auth_method"] == "oauth"
        assert tidal["category"] == "streaming"
        # Favorites snapshot writes no canonical data — capabilities stay
        # empty until a later epic adds one.
        assert tidal["capabilities"] == []

    async def test_apple_music_registered_as_browser_bridge(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/connectors")

        apple = next(c for c in response.json() if c["name"] == "apple_music")
        assert apple["auth_method"] == "browser_bridge"
        assert apple["capabilities"] == ["history_import_api"]

    async def test_musicbrainz_probe_serializes_end_to_end(
        self, client: httpx2.AsyncClient
    ) -> None:
        # The one probe with no credential dependence — a deterministic
        # smoke that the route runs a real status probe and serializes it.
        response = await client.get("/api/v1/connectors")

        mb = next(c for c in response.json() if c["name"] == "musicbrainz")
        assert mb["connected"] is True
        assert mb["account_name"] is None
        assert mb["token_expires_at"] is None


class TestLastfmStatus:
    """Last.fm connector status from TokenStorage + settings."""

    async def test_status_reflects_settings(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/v1/connectors")

        lastfm = next(c for c in response.json() if c["name"] == "lastfm")
        # Result depends on env vars — just verify shape
        assert "connected" in lastfm
        assert "account_name" in lastfm


class TestConnectorDetailField:
    """`detail` (v0.11.1 D1) — generic short status suffix, e.g. "1,204 releases"."""

    async def test_detail_serializes_through_to_response(
        self, client: httpx2.AsyncClient
    ) -> None:
        registry = discover_connectors()
        statuses = [
            ConnectorStatus(
                name=name,
                auth_method=config["auth_method"],
                connected=True,
                detail="1,204 releases" if name == "musicbrainz" else None,
            )
            for name, config in registry.items()
        ]
        mock_statuses = AsyncMock(return_value=statuses)
        with patch(
            "src.interface.api.routes.connectors.get_all_connector_statuses",
            mock_statuses,
        ):
            response = await client.get("/api/v1/connectors")

        assert response.status_code == 200
        by_name = {c["name"]: c for c in response.json()}
        assert by_name["musicbrainz"]["detail"] == "1,204 releases"
        assert by_name["spotify"]["detail"] is None


class TestDeleteTokenGate:
    """DELETE /connectors/{service}/token — which auth methods can disconnect."""

    async def test_token_auth_connector_can_disconnect(
        self, client: httpx2.AsyncClient
    ) -> None:
        """v0.11.1 D1: the gate widens from {oauth, browser_bridge} to include
        ``token`` (Discogs' personal access token) alongside the vocabulary
        substrate — a fake registry entry stands in since no ``token``-auth
        connector is registered yet."""
        fake_config = {
            "factory": object,
            "metrics": {},
            "display_name": "Fake Token Service",
            "category": "streaming",
            "auth_method": "token",
            "capabilities": frozenset(),
            "status_fn": AsyncMock(),
            "build_auth_url": None,
        }
        with patch(
            "src.interface.api.routes.connectors.discover_connectors",
            return_value={"fake_token_svc": fake_config},
        ):
            response = await client.delete("/api/v1/connectors/fake_token_svc/token")

        assert response.status_code == 204

    async def test_declared_on_disconnect_hook_runs(
        self, client: httpx2.AsyncClient
    ) -> None:
        """A config-declared ``on_disconnect`` hook is awaited with the user id.

        The absent-hook case is the test above: no ``on_disconnect`` key, and
        the delete still returns 204.
        """
        hook = AsyncMock()
        fake_config = {
            "factory": object,
            "metrics": {},
            "display_name": "Fake Token Service",
            "category": "streaming",
            "auth_method": "token",
            "capabilities": frozenset(),
            "status_fn": AsyncMock(),
            "build_auth_url": None,
            "on_disconnect": hook,
        }
        with patch(
            "src.interface.api.routes.connectors.discover_connectors",
            return_value={"fake_token_svc": fake_config},
        ):
            response = await client.delete("/api/v1/connectors/fake_token_svc/token")

        assert response.status_code == 204
        hook.assert_awaited_once_with("default")


class TestSpotifyPlaylistBrowse:
    """GET /api/v1/connectors/spotify/playlists — route-level behavior."""

    async def test_cache_hit_returns_seeded_playlists(
        self, client: httpx2.AsyncClient
    ) -> None:
        """Default (force_refresh=false) reads from DBConnectorPlaylist.

        No connector touched on the cache-hit path — proves the route's
        cache-first contract holds without the fixture having to stub a
        connector.
        """
        async with get_session() as session:
            session.add(
                DBConnectorPlaylist(
                    connector_name="spotify",
                    connector_playlist_identifier="sp_test_1",
                    name="Cached Playlist",
                    description=None,
                    owner="alice",
                    owner_id="alice_id",
                    is_public=True,
                    collaborative=False,
                    follower_count=0,
                    items=[],
                    raw_metadata={"total_tracks": 42},
                    last_updated=datetime.now(UTC),
                )
            )
            await session.commit()

        response = await client.get("/api/v1/connectors/spotify/playlists")

        assert response.status_code == 200
        body = response.json()
        assert body["from_cache"] is True
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["name"] == "Cached Playlist"
        assert row["track_count"] == 42
        assert row["import_status"] == "not_imported"

    async def test_force_refresh_with_failing_connector_returns_error_envelope(
        self,
        client: httpx2.AsyncClient,
        mock_connector_provider: dict[str, object],
    ) -> None:
        """force_refresh=true with a stubbed-failing connector returns the error envelope.

        Proves the test seam blocks live API calls — the stub's
        ``fetch_user_playlists`` raises before any HTTP request would happen.
        Uses ``ValueError`` because it has a registered exception handler that
        produces the standard ``{error: {code, message}}`` envelope; arbitrary
        ``Exception`` subclasses don't surface cleanly through httpx2's
        ASGITransport in test mode.
        """
        spotify = AsyncMock(spec=UserPlaylistsConnector)
        spotify.fetch_user_playlists = AsyncMock(
            side_effect=ValueError("connector temporarily unavailable")
        )
        mock_connector_provider["spotify"] = spotify

        response = await client.get(
            "/api/v1/connectors/spotify/playlists?force_refresh=true"
        )

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        spotify.fetch_user_playlists.assert_called_once()


class TestSpotifyPlaylistImport:
    """POST /api/v1/connectors/spotify/playlists/import — route-level behavior."""

    async def test_single_playlist_returns_202_with_operation_id(
        self, client: httpx2.AsyncClient
    ) -> None:
        """One-element ids list returns the 202 OperationStartedResponse shape.

        Background work is stubbed by ``_noop_launch`` in conftest, so the
        route's contract (202 + operation_id) is exercised without spawning
        the real import task.
        """
        response = await client.post(
            "/api/v1/connectors/spotify/playlists/import",
            json={
                "connector_playlist_identifiers": ["sp_single"],
                "sync_direction": "pull",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert isinstance(body.get("operation_id"), str)
        assert body["operation_id"]  # non-empty

    async def test_empty_ids_returns_422_before_use_case(
        self, client: httpx2.AsyncClient
    ) -> None:
        """Empty connector_playlist_identifiers fails Pydantic validation (min_length=1)."""
        response = await client.post(
            "/api/v1/connectors/spotify/playlists/import",
            json={
                "connector_playlist_identifiers": [],
                "sync_direction": "pull",
            },
        )

        assert response.status_code == 422

    async def test_invalid_sync_direction_returns_422(
        self, client: httpx2.AsyncClient
    ) -> None:
        """sync_direction must be 'pull' or 'push' — anything else fails validation."""
        response = await client.post(
            "/api/v1/connectors/spotify/playlists/import",
            json={
                "connector_playlist_identifiers": ["sp_x"],
                "sync_direction": "mirror",
            },
        )

        assert response.status_code == 422

    async def test_force_flag_accepted_in_request_body(
        self, client: httpx2.AsyncClient
    ) -> None:
        """``force: true`` is accepted alongside the other fields and returns 202.

        End-to-end behavior (force actually re-fetches a cached playlist)
        is exercised at the use-case unit level; here we just verify the
        route schema accepts the field.
        """
        response = await client.post(
            "/api/v1/connectors/spotify/playlists/import",
            json={
                "connector_playlist_identifiers": ["sp_x"],
                "sync_direction": "pull",
                "force": True,
            },
        )

        assert response.status_code == 202
        assert isinstance(response.json().get("operation_id"), str)


class TestPlayPolling:
    """The reachable on/off switch for the adaptive play heartbeat.

    Its absence was a real gap: ``enable_play_polling`` ran only inside the OAuth
    callback, so anyone who consented before the feature shipped had a valid
    grant, no schedule row, and no surface able to create one — the generic
    schedule upsert rejects this target by design.
    """

    async def test_defaults_to_off_when_never_enabled(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/connectors/spotify/play-polling")

        assert response.status_code == 200
        assert response.json()["enabled"] is False

    async def test_enabling_creates_the_schedule_at_the_base_cadence(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.put(
            "/api/v1/connectors/spotify/play-polling", json={"enabled": True}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        assert body["interval_minutes"] == 30

        # And it survives the round trip — this is the state the heartbeat reads.
        after = await client.get("/api/v1/connectors/spotify/play-polling")
        assert after.json()["enabled"] is True

    async def test_disabling_preserves_the_row(
        self, client: httpx2.AsyncClient
    ) -> None:
        await client.put(
            "/api/v1/connectors/spotify/play-polling", json={"enabled": True}
        )
        response = await client.put(
            "/api/v1/connectors/spotify/play-polling", json={"enabled": False}
        )

        assert response.status_code == 200
        assert response.json()["enabled"] is False
        # Disabled, not deleted: the cadence it had learned is still there for a
        # later re-enable to resume from.
        assert response.json()["interval_minutes"] == 30

    async def test_re_enabling_is_idempotent(self, client: httpx2.AsyncClient) -> None:
        for _ in range(2):
            response = await client.put(
                "/api/v1/connectors/spotify/play-polling", json={"enabled": True}
            )
            assert response.status_code == 200
            assert response.json()["enabled"] is True

    async def test_other_connectors_are_rejected(
        self, client: httpx2.AsyncClient
    ) -> None:
        # Only Spotify declares supports_play_polling; a silent no-op would
        # imply the switch did something.
        response = await client.put(
            "/api/v1/connectors/lastfm/play-polling", json={"enabled": True}
        )
        assert response.status_code == 400

    async def test_apple_music_rejected_until_policy_generalizes(
        self, client: httpx2.AsyncClient
    ) -> None:
        # Apple declares history_import_api, but the poll policy still targets
        # spotify:plays — the supports_play_polling flag is the gate, not the
        # capability.
        response = await client.put(
            "/api/v1/connectors/apple_music/play-polling", json={"enabled": True}
        )
        assert response.status_code == 400

    async def test_connector_without_the_capability_gets_501(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.put(
            "/api/v1/connectors/musicbrainz/play-polling", json={"enabled": True}
        )
        assert response.status_code == 501

    async def test_unknown_connector_gets_404(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/v1/connectors/nope/play-polling")
        assert response.status_code == 404

    async def test_disconnect_stops_polling(self, client: httpx2.AsyncClient) -> None:
        # supports_play_polling drives the disconnect teardown too — the
        # heartbeat must not outlive the credential it polls with.
        await client.put(
            "/api/v1/connectors/spotify/play-polling", json={"enabled": True}
        )

        response = await client.delete("/api/v1/connectors/spotify/token")

        assert response.status_code == 204
        after = await client.get("/api/v1/connectors/spotify/play-polling")
        assert after.json()["enabled"] is False


class TestDisconnectPreservesSiblingData:
    """DELETE /connectors/{service}/token removes only the credential row.

    Disconnect is explicit-scope by design (v0.11.2 P S4): the token row is
    the only thing it deletes. Imported likes and plays are the user's
    records and must survive — account deletion (v0.6.4 purge) stays the
    only full-removal path.
    """

    async def test_disconnect_removes_token_but_keeps_likes_and_plays(
        self, client: httpx2.AsyncClient
    ) -> None:
        import sqlalchemy as sa

        from src.domain.entities import ConnectorTrackPlay
        from src.infrastructure.persistence.database.db_models import (
            DBConnectorPlay,
            DBTrackLike,
        )
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )
        from tests.fixtures import make_track

        storage = get_token_storage()
        await storage.save_token(
            "spotify",
            "default",
            StoredToken(
                access_token="tok",
                refresh_token="ref",
                expires_at=int(time.time()) + 3600,
                account_name="testuser",
            ),
        )
        # Sibling credential: the delete must be scoped to the named service,
        # not to the user's whole credential set.
        await storage.save_token(
            "lastfm",
            "default",
            StoredToken(session_key="lastfm-session", account_name="lastfmuser"),
        )

        batch_id = "TEST_DISCONNECT_BATCH"
        async with get_session() as session:
            uow = get_unit_of_work(session)
            saved_track = await uow.get_track_repository().save_track(
                make_track(
                    title="TEST_Disconnect_Track",
                    artist="TEST_Disconnect_Artist",
                    connector_track_identifiers={},
                )
            )
            await uow.get_like_repository().save_track_likes_batch(
                [(saved_track.id, "spotify", True, None, None)], user_id="default"
            )
            await uow.get_connector_play_repository().bulk_insert_connector_plays([
                ConnectorTrackPlay(
                    service="spotify",
                    artist_name="TEST_Disconnect_Artist",
                    track_name="TEST_Disconnect_Track",
                    played_at=datetime.now(UTC),
                    ms_played=None,
                    user_id="default",
                    import_timestamp=datetime.now(UTC),
                    import_source="spotify_api",
                    import_batch_id=batch_id,
                )
            ])

        assert await storage.load_token("spotify", "default") is not None

        response = await client.delete("/api/v1/connectors/spotify/token")
        assert response.status_code == 204

        assert await storage.load_token("spotify", "default") is None
        # The sibling service's credential survives the spotify disconnect.
        lastfm_token = await storage.load_token("lastfm", "default")
        assert lastfm_token is not None
        assert lastfm_token.get("session_key") == "lastfm-session"
        # Cleanup: don't leak the seeded lastfm credential into other tests.
        await storage.delete_token("lastfm", "default")

        status_response = await client.get("/api/v1/connectors")
        spotify_status = next(
            c for c in status_response.json() if c["name"] == "spotify"
        )
        assert spotify_status["connected"] is False
        assert spotify_status["status"] == "disconnected"

        async with get_session() as session:
            like_count = await session.scalar(
                sa
                .select(sa.func.count())
                .select_from(DBTrackLike)
                .where(DBTrackLike.track_id == saved_track.id)
            )
            play_count = await session.scalar(
                sa
                .select(sa.func.count())
                .select_from(DBConnectorPlay)
                .where(DBConnectorPlay.import_batch_id == batch_id)
            )
        assert like_count == 1
        assert play_count == 1


def _discogs_stored_token() -> StoredToken:
    return StoredToken(
        access_token="discogs-pat",
        token_type="personal_token",
        account_name="wash",
        extra_data={"collection_count": 3, "validated_at": 1_755_000_000},
    )


class TestConnectorToken:
    """PUT /api/v1/connectors/{service}/token — validate, rate-limit, store.

    Exercised through Discogs, the one connector declaring ``validate_token``.
    That hook is stubbed on the cached registry entry (no live Discogs probe);
    storage + status probe run against the real stack. Each test acts as a
    unique user because ``oauth_tokens`` is a preserved table — sharing
    ``default`` would leak credentials across tests, and the probe-rate budget
    is per user.
    """

    @pytest.fixture
    async def user_client(
        self,
        postgres_url: str,
        _init_test_schema: None,
    ) -> AsyncGenerator[tuple[httpx2.AsyncClient, str]]:
        """Client acting as a fresh unique user, plus that user's id."""
        with _test_db_env(postgres_url):
            app = create_app()
            uid = f"discogs-{uuid.uuid4().hex[:12]}"
            app.dependency_overrides[get_current_user_id] = lambda: uid
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(
                transport=transport, base_url="http://test"
            ) as c:
                yield c, uid

    @pytest.fixture(autouse=True)
    def _fresh_limiter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fresh sliding window per test — the limiter is module-global state."""
        monkeypatch.setattr(
            connectors_route,
            "_token_limiter",
            InMemoryRateLimiter(
                max_requests=5,
                window_seconds=60,
                message="Too many token attempts. Please wait a minute and try again.",
            ),
        )

    def _stub_validate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        stored: StoredToken | None = None,
        error: Exception | None = None,
    ) -> AsyncMock:
        mock = (
            AsyncMock(side_effect=error)
            if error is not None
            else AsyncMock(return_value=stored)
        )
        monkeypatch.setitem(discover_connectors()["discogs"], "validate_token", mock)
        return mock

    async def test_put_valid_token_stores_and_connects(
        self,
        user_client: tuple[httpx2.AsyncClient, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client, uid = user_client
        validate = self._stub_validate(monkeypatch, stored=_discogs_stored_token())

        resp = await client.put(
            "/api/v1/connectors/discogs/token", json={"token": "discogs-pat"}
        )

        assert resp.status_code == 204
        validate.assert_awaited_once_with("discogs-pat")
        with user_context(uid):
            row = await get_token_storage().load_token("discogs", uid)
        assert row is not None
        assert row.get("access_token") == "discogs-pat"

        status = await client.get("/api/v1/connectors")
        discogs = next(c for c in status.json() if c["name"] == "discogs")
        assert discogs["connected"] is True
        assert discogs["status"] == "connected"
        assert discogs["account_name"] == "wash"
        assert discogs["detail"] == "3 releases"

    async def test_put_invalid_token_is_enveloped_400(
        self,
        user_client: tuple[httpx2.AsyncClient, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The exact envelope the token form reads (`body.error.message`) —
        # a bare HTTPException `detail` body rendered as "unknown error".
        client, uid = user_client
        self._stub_validate(
            monkeypatch,
            error=DiscogsInvalidTokenError(
                "Discogs rejected that personal access token — check it was "
                "copied in full."
            ),
        )

        resp = await client.put(
            "/api/v1/connectors/discogs/token", json={"token": "bad"}
        )

        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "DISCOGS_INVALID_TOKEN"
        assert "rejected" in body["error"]["message"]
        with user_context(uid):
            assert await get_token_storage().load_token("discogs", uid) is None

    async def test_oversize_token_is_422_and_never_echoed(
        self,
        user_client: tuple[httpx2.AsyncClient, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # max_length=512 bounds hostile payloads, and the validation handler
        # strips FastAPI's default `input` echo — a rejected credential must
        # never come back in the response body.
        client, _uid = user_client
        validate = self._stub_validate(monkeypatch, stored=_discogs_stored_token())
        secret = "s3cret-" + "x" * 600

        resp = await client.put(
            "/api/v1/connectors/discogs/token", json={"token": secret}
        )

        assert resp.status_code == 422
        assert "s3cret" not in resp.text
        validate.assert_not_awaited()

    async def test_sixth_probe_in_window_is_rate_limited(
        self,
        user_client: tuple[httpx2.AsyncClient, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client, _uid = user_client
        self._stub_validate(monkeypatch, stored=_discogs_stored_token())

        for _ in range(5):
            ok = await client.put(
                "/api/v1/connectors/discogs/token", json={"token": "discogs-pat"}
            )
            assert ok.status_code == 204

        sixth = await client.put(
            "/api/v1/connectors/discogs/token", json={"token": "discogs-pat"}
        )
        assert sixth.status_code == 429
        # The throttle names what it limited — not the chat default.
        assert "token attempts" in sixth.json()["error"]["message"]

    async def test_delete_disconnects_and_removes_row(
        self,
        user_client: tuple[httpx2.AsyncClient, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client, uid = user_client
        self._stub_validate(monkeypatch, stored=_discogs_stored_token())
        put = await client.put(
            "/api/v1/connectors/discogs/token", json={"token": "discogs-pat"}
        )
        assert put.status_code == 204

        resp = await client.delete("/api/v1/connectors/discogs/token")

        assert resp.status_code == 204
        with user_context(uid):
            assert await get_token_storage().load_token("discogs", uid) is None
        status = await client.get("/api/v1/connectors")
        discogs = next(c for c in status.json() if c["name"] == "discogs")
        assert discogs["connected"] is False
        assert discogs["status"] == "disconnected"

    async def test_oauth_connector_rejects_a_pasted_token(
        self,
        user_client: tuple[httpx2.AsyncClient, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Spotify mints its credential through a redirect, so there is nothing
        # for a pasted secret to be: 400 before storage is touched.
        client, uid = user_client
        validate = self._stub_validate(monkeypatch, stored=_discogs_stored_token())

        resp = await client.put(
            "/api/v1/connectors/spotify/token", json={"token": "not-a-spotify-thing"}
        )

        assert resp.status_code == 400
        validate.assert_not_awaited()
        with user_context(uid):
            assert await get_token_storage().load_token("spotify", uid) is None

    async def test_unknown_connector_is_404(
        self, user_client: tuple[httpx2.AsyncClient, str]
    ) -> None:
        client, _uid = user_client

        resp = await client.put(
            "/api/v1/connectors/nope/token", json={"token": "whatever"}
        )

        assert resp.status_code == 404
