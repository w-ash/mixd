"""Spotify differential-push outcome accounting.

Regression coverage for the ``PlaylistOpsOutcome.fully_applied`` verdict that the
update-connector-playlist use case relies on to decide SYNCED vs ERROR. The
Spotify client suppresses HTTP/network errors (``_api_call`` returns ``None`` for
``_SUPPRESS_ERRORS``) instead of raising, so each executor MUST treat a falsy
return as a failure — otherwise a silently-dropped add is reported as a clean
sync. Remove/move already guarded on the return; these tests pin the add path.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.domain.playlist import PlaylistOperation, PlaylistOperationType
from src.infrastructure.connectors.spotify.playlist_sync_operations import (
    SpotifyPlaylistSyncOperations,
)
from tests.fixtures import make_track


def _add_op(position: int, uri: str) -> PlaylistOperation:
    """Build a valid ADD operation with a Spotify URI already resolved."""
    return PlaylistOperation(
        operation_type=PlaylistOperationType.ADD,
        track=make_track(id=position + 1),
        position=position,
        spotify_uri=uri,
    )


@pytest.fixture
def no_sleep():
    """Skip the inter-request delay so the executor runs instantly."""
    with patch("asyncio.sleep", new_callable=AsyncMock):
        yield


class TestAddOperationOutcomeAccounting:
    """A suppressed add error must surface as failed, not as a phantom success."""

    async def test_suppressed_add_error_counts_as_failed(self, no_sleep):
        """One add succeeds, one returns None (suppressed error) → not fully applied.

        This is the false-SYNCED scenario: with ``successful > 0`` the group-level
        "all failed" guard does not fire, so the only signal that the second add
        was lost is the ``failed`` count flowing into ``fully_applied``.
        """
        client = AsyncMock()
        # First add returns a snapshot (success); second returns None (the client
        # swallowed an httpx error and returned None rather than raising).
        client.playlist_add_items = AsyncMock(side_effect=[AsyncMock(), None])
        client.get_playlist = AsyncMock(return_value=None)
        ops = SpotifyPlaylistSyncOperations(client=client)

        outcome = await ops.execute_playlist_operations(
            "playlist123",
            [_add_op(0, "spotify:track:aaa"), _add_op(1, "spotify:track:bbb")],
        )

        assert client.playlist_add_items.call_count == 2
        assert outcome.failed == 1
        assert outcome.fully_applied is False

    async def test_all_adds_succeed_is_fully_applied(self, no_sleep):
        """Every add returns a snapshot → failed == 0, fully applied."""
        client = AsyncMock()
        client.playlist_add_items = AsyncMock(return_value=AsyncMock())
        client.get_playlist = AsyncMock(return_value=None)
        ops = SpotifyPlaylistSyncOperations(client=client)

        outcome = await ops.execute_playlist_operations(
            "playlist123",
            [_add_op(0, "spotify:track:aaa"), _add_op(1, "spotify:track:bbb")],
        )

        assert outcome.failed == 0
        assert outcome.fully_applied is True


class TestDroppedOperationAccounting:
    """Ops requested but never submitted are reported as dropped, not failed."""

    async def test_validation_filtered_op_counts_as_dropped(self, no_sleep):
        """An add with an unresolved URI is filtered out → dropped, still SYNCED.

        ``fully_applied`` stays True (no submitted op failed), but ``dropped``
        surfaces the silently-skipped track so the caller can report it.
        """
        client = AsyncMock()
        client.playlist_add_items = AsyncMock(return_value=AsyncMock())
        client.get_playlist = AsyncMock(return_value=None)
        ops = SpotifyPlaylistSyncOperations(client=client)

        outcome = await ops.execute_playlist_operations(
            "playlist123",
            # Second op has no spotify: URI → _validate_operations drops it.
            [_add_op(0, "spotify:track:aaa"), _add_op(1, "not-a-spotify-uri")],
        )

        assert client.playlist_add_items.call_count == 1
        assert outcome.requested == 2
        assert outcome.dropped == 1
        assert outcome.failed == 0
        assert outcome.fully_applied is True
