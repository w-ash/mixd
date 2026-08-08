"""Unit tests for the Spotify recently-played API importer (v0.10.1).

Covers the cursor/checkpoint math that makes polling resumable, the auth
precheck that turns a stale grant into an actionable error instead of an empty
import, and the field mapping that lets these rows reuse the existing Spotify
resolver with no new resolution code.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.operations import SyncCheckpoint
from src.domain.exceptions import SpotifyAuthRequiredError
from src.domain.repositories.play import (
    LastfmImportParams,
    SpotifyRecentImportParams,
)
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyPlayContext,
    SpotifyPlayHistoryItem,
    SpotifyRecentlyPlayedResponse,
    SpotifyTrack,
)
from src.infrastructure.connectors.spotify.recently_played_importer import (
    SpotifyRecentlyPlayedImporter,
)
from tests.fixtures.mocks import make_mock_uow

_TOKEN_STORAGE_PATH = (
    "src.infrastructure.connectors.spotify.recently_played_importer.get_token_storage"
)
_ALL_SCOPES = (
    "playlist-modify-public playlist-modify-private playlist-read-private "
    "playlist-read-collaborative user-library-read user-read-recently-played"
)
# Batch identity the pipeline threads into the fetch as well as the conversion.
# This importer takes a single page, so it ignores both.
_RUN = {
    "batch_id": "batch-1",
    "import_timestamp": datetime(2026, 7, 20, 13, tzinfo=UTC),
}


def _item(
    *,
    track_id: str = "4iV5W9uYEdYUVa79Axb7Rh",
    name: str = "Creep",
    artist: str = "Radiohead",
    minute: int = 0,
    context: SpotifyPlayContext | None = None,
) -> SpotifyPlayHistoryItem:
    """Build one recently-played entry."""
    return SpotifyPlayHistoryItem(
        track=SpotifyTrack(
            id=track_id,
            name=name,
            artists=[SpotifyArtist(id="a1", name=artist)],
            album=SpotifyAlbum(id="al1", name="Pablo Honey"),
            duration_ms=238640,
        ),
        played_at=datetime(2026, 7, 20, 12, minute, tzinfo=UTC),
        context=context,
    )


def _client(items: list[SpotifyPlayHistoryItem] | None = None) -> MagicMock:
    """A client double whose get_recently_played returns the given items."""
    client = MagicMock()
    client.get_recently_played = AsyncMock(
        return_value=SpotifyRecentlyPlayedResponse(items=items or [])
    )
    return client


def _uow_with_checkpoint(checkpoint: SyncCheckpoint | None):
    """A mock UoW whose checkpoint repo returns `checkpoint` on read."""
    uow = make_mock_uow()
    # (inserted, duplicates) — the ledger write's contract, passed straight
    # through to the run's reported counts.
    uow.get_connector_play_repository().bulk_insert_connector_plays = AsyncMock(
        side_effect=lambda plays: (len(list(plays)), 0)
    )
    repo = uow.get_checkpoint_repository()
    repo.get_sync_checkpoint = AsyncMock(return_value=checkpoint)
    repo.get_or_create_sync_checkpoint = AsyncMock(
        return_value=checkpoint
        or SyncCheckpoint(user_id="user-1", service="spotify", entity_type="plays")
    )
    repo.save_sync_checkpoint = AsyncMock(side_effect=lambda cp: cp)
    return uow, repo


class TestAuthPrecheck:
    """The grant is verified before any API call or pipeline work."""

    @staticmethod
    def _importer_with_token(token: dict[str, str] | None):
        storage = AsyncMock()
        storage.load_token = AsyncMock(return_value=token)
        return SpotifyRecentlyPlayedImporter(client=_client()), storage

    async def test_no_token_raises_auth_required(self):
        importer, storage = self._importer_with_token(None)
        uow, _ = _uow_with_checkpoint(None)

        with patch(_TOKEN_STORAGE_PATH, return_value=storage):
            with pytest.raises(SpotifyAuthRequiredError, match="not connected"):
                await importer.import_plays(
                    uow, SpotifyRecentImportParams(), user_id="user-1"
                )

    async def test_grant_without_recently_played_scope_raises(self):
        """A pre-v0.10.1 grant still works for likes, so it must fail loudly here."""
        importer, storage = self._importer_with_token({
            "scope": "playlist-read-private user-library-read"
        })
        uow, _ = _uow_with_checkpoint(None)

        with patch(_TOKEN_STORAGE_PATH, return_value=storage):
            with pytest.raises(SpotifyAuthRequiredError, match="re-connecting"):
                await importer.import_plays(
                    uow, SpotifyRecentImportParams(), user_id="user-1"
                )

    async def test_wrong_params_type_raises_before_any_token_read(self):
        importer, storage = self._importer_with_token({"scope": _ALL_SCOPES})
        uow, _ = _uow_with_checkpoint(None)

        with patch(_TOKEN_STORAGE_PATH, return_value=storage):
            with pytest.raises(TypeError, match="requires SpotifyRecentImportParams"):
                await importer.import_plays(uow, LastfmImportParams(), user_id="user-1")
        storage.load_token.assert_not_awaited()


class TestFetchData:
    """Cursor resolution — the resume position that makes polling incremental."""

    async def test_no_checkpoint_requests_whole_window(self):
        client = _client([_item()])
        importer = SpotifyRecentlyPlayedImporter(client=client)
        uow, _ = _uow_with_checkpoint(None)

        await importer._fetch_data(
            SpotifyRecentImportParams(), uow=uow, user_id="user-1", **_RUN
        )

        assert client.get_recently_played.await_args.kwargs["after_ms"] is None

    async def test_stored_cursor_becomes_after_param(self):
        client = _client([_item()])
        importer = SpotifyRecentlyPlayedImporter(client=client)
        uow, _ = _uow_with_checkpoint(
            SyncCheckpoint(
                user_id="user-1",
                service="spotify",
                entity_type="plays",
                cursor="1753012800000",
            )
        )

        await importer._fetch_data(
            SpotifyRecentImportParams(), uow=uow, user_id="user-1", **_RUN
        )

        assert client.get_recently_played.await_args.kwargs["after_ms"] == 1753012800000

    async def test_checkpoint_is_read_under_the_mixd_user_id(self):
        """Not the Spotify account name — sync_checkpoints is RLS-scoped on user_id."""
        importer = SpotifyRecentlyPlayedImporter(client=_client())
        uow, repo = _uow_with_checkpoint(None)

        await importer._fetch_data(
            SpotifyRecentImportParams(), uow=uow, user_id="mixd-user-42", **_RUN
        )

        repo.get_sync_checkpoint.assert_awaited_once_with(
            user_id="mixd-user-42", service="spotify", entity_type="plays"
        )

    async def test_unparseable_cursor_degrades_to_whole_window(self):
        client = _client([_item()])
        importer = SpotifyRecentlyPlayedImporter(client=client)
        uow, _ = _uow_with_checkpoint(
            SyncCheckpoint(
                user_id="user-1",
                service="spotify",
                entity_type="plays",
                cursor="2026-07-20T12:00:00Z",  # a Last.fm-style ISO cursor
            )
        )

        await importer._fetch_data(
            SpotifyRecentImportParams(), uow=uow, user_id="user-1", **_RUN
        )

        assert client.get_recently_played.await_args.kwargs["after_ms"] is None

    async def test_force_ignores_the_stored_cursor(self):
        """``force`` is what ``import_data``'s force flag promises the user.

        Dropping it would let the assistant confirm a from-scratch re-import on
        the card and then quietly resume from the checkpoint instead.
        """
        client = _client([_item()])
        importer = SpotifyRecentlyPlayedImporter(client=client)
        uow, repo = _uow_with_checkpoint(
            SyncCheckpoint(
                user_id="user-1",
                service="spotify",
                entity_type="plays",
                cursor="1753012800000",
            )
        )

        await importer._fetch_data(
            SpotifyRecentImportParams(force=True), uow=uow, user_id="user-1", **_RUN
        )

        assert client.get_recently_played.await_args.kwargs["after_ms"] is None
        # Not merely overridden after the fact — the read is skipped entirely.
        repo.get_sync_checkpoint.assert_not_awaited()

    async def test_none_response_raises_rather_than_reading_as_empty(self):
        """A suppressed transport failure must not look like 'nothing new'.

        The ~50-play window makes a skipped poll unrecoverable, so a silent
        empty success is the worst possible outcome here.
        """
        client = MagicMock()
        client.get_recently_played = AsyncMock(return_value=None)
        importer = SpotifyRecentlyPlayedImporter(client=client)
        uow, _ = _uow_with_checkpoint(None)

        with pytest.raises(RuntimeError, match="no recently-played response"):
            await importer._fetch_data(
                SpotifyRecentImportParams(), uow=uow, user_id="user-1", **_RUN
            )


class TestProcessData:
    """Field mapping onto the spotify_api ledger channel."""

    async def test_maps_history_item_to_connector_play(self):
        importer = SpotifyRecentlyPlayedImporter(client=_client())
        import_ts = datetime(2026, 7, 20, 13, tzinfo=UTC)

        plays = await importer._process_data(
            [
                _item(
                    context=SpotifyPlayContext(
                        type="playlist", uri="spotify:playlist:p1"
                    )
                )
            ],
            user_id="user-1",
            batch_id="batch-1",
            import_timestamp=import_ts,
        )

        play = plays[0]
        assert play.service == "spotify"
        assert play.import_source == "spotify_api"
        assert play.track_name == "Creep"
        assert play.artist_name == "Radiohead"
        assert play.album_name == "Pablo Honey"
        assert play.played_at == datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
        assert play.import_batch_id == "batch-1"
        # Tenancy at construction — the pipeline no longer re-stamps it.
        assert play.user_id == "user-1"

    async def test_ms_played_stays_null(self):
        """Survivorship takes the first non-null ms_played across channels.

        A synthetic stand-in (e.g. track duration) would win merges against the
        GDPR export and corrupt listening-time stats.
        """
        importer = SpotifyRecentlyPlayedImporter(client=_client())

        plays = await importer._process_data(
            [_item()],
            user_id="user-1",
            batch_id="b",
            import_timestamp=datetime.now(UTC),
        )

        assert plays[0].ms_played is None
        assert plays[0].service_metadata["duration_ms"] == 238640

    async def test_track_uri_drives_the_connector_identifier(self):
        """This is what lets the existing Spotify resolver handle these rows."""
        importer = SpotifyRecentlyPlayedImporter(client=_client())

        plays = await importer._process_data(
            [_item()],
            user_id="user-1",
            batch_id="b",
            import_timestamp=datetime.now(UTC),
        )

        play = plays[0]
        assert (
            play.service_metadata["track_uri"] == "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
        )
        assert play.connector_track_identifier == "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"

    async def test_playback_context_captured_and_null_safe(self):
        importer = SpotifyRecentlyPlayedImporter(client=_client())

        with_ctx, without_ctx = await importer._process_data(
            [
                _item(context=SpotifyPlayContext(type="album", uri="spotify:album:x")),
                _item(minute=5),
            ],
            user_id="user-1",
            batch_id="b",
            import_timestamp=datetime.now(UTC),
        )

        assert with_ctx.service_metadata["context_type"] == "album"
        assert with_ctx.service_metadata["context_uri"] == "spotify:album:x"
        assert without_ctx.service_metadata["context_type"] is None


class TestHandleCheckpoints:
    """Cursor advance — deterministic and never backwards."""

    async def test_empty_poll_leaves_checkpoint_untouched(self):
        importer = SpotifyRecentlyPlayedImporter(client=_client())
        uow, repo = _uow_with_checkpoint(None)

        await importer._handle_checkpoints(
            [], SpotifyRecentImportParams(), uow, user_id="user-1"
        )

        repo.save_sync_checkpoint.assert_not_awaited()

    async def test_cursor_advances_to_newest_play_as_ms_epoch(self):
        importer = SpotifyRecentlyPlayedImporter(client=_client())
        uow, repo = _uow_with_checkpoint(None)
        newest = datetime(2026, 7, 20, 12, 9, tzinfo=UTC)

        await importer._handle_checkpoints(
            [_item(minute=0), _item(minute=9), _item(minute=4)],
            SpotifyRecentImportParams(),
            uow,
            user_id="user-1",
        )

        saved = repo.save_sync_checkpoint.await_args.args[0]
        assert saved.last_timestamp == newest
        assert saved.cursor == str(int(newest.timestamp() * 1000))

    async def test_checkpoint_is_written_under_the_mixd_user_id(self):
        importer = SpotifyRecentlyPlayedImporter(client=_client())
        uow, repo = _uow_with_checkpoint(None)

        await importer._handle_checkpoints(
            [_item()], SpotifyRecentImportParams(), uow, user_id="mixd-user-42"
        )

        repo.get_or_create_sync_checkpoint.assert_awaited_once_with(
            user_id="mixd-user-42", service="spotify", entity_type="plays"
        )


class TestTwoPollFlow:
    """The resume contract: poll, advance, resume from where it stopped."""

    async def test_second_poll_resumes_from_first_polls_cursor(self):
        first = _item(minute=0)
        second = _item(
            minute=30, track_id="1AbCdEfGhIjKlMnOpQrStU", name="No Surprises"
        )

        client = _client([first])
        importer = SpotifyRecentlyPlayedImporter(client=client)
        uow, repo = _uow_with_checkpoint(None)
        storage = AsyncMock()
        storage.load_token = AsyncMock(return_value={"scope": _ALL_SCOPES})

        with patch(_TOKEN_STORAGE_PATH, return_value=storage):
            await importer.import_plays(
                uow, SpotifyRecentImportParams(), user_id="user-1"
            )

            saved = repo.save_sync_checkpoint.await_args.args[0]
            assert client.get_recently_played.await_args.kwargs["after_ms"] is None

            # Second poll sees the checkpoint the first one wrote.
            repo.get_sync_checkpoint = AsyncMock(return_value=saved)
            client.get_recently_played = AsyncMock(
                return_value=SpotifyRecentlyPlayedResponse(items=[second])
            )
            await importer.import_plays(
                uow, SpotifyRecentImportParams(), user_id="user-1"
            )

        assert client.get_recently_played.await_args.kwargs["after_ms"] == int(
            first.played_at.timestamp() * 1000
        )
        assert repo.save_sync_checkpoint.await_args.args[0].cursor == str(
            int(second.played_at.timestamp() * 1000)
        )
