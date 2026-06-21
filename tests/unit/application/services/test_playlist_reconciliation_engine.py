"""Unit tests for PlaylistReconciliationEngine.

Locks in the behaviours the old push/pull/preview paths got wrong:
- safety + diff are computed against the FRESHLY FETCHED remote (not the
  canonical-self the old code diffed, which made the guard dead and push a no-op),
- an unchanged remote is a no-op (idempotent re-runs),
- a connector failure fails loud (ConnectorSyncError → never a silent SYNCED),
- pull overwrites the canonical; push sends ops to the connector.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid7

import pytest

from src.application.services.playlist_reconciliation_engine import (
    PlaylistReconciliationEngine,
)
from src.domain.entities.playlist import (
    ConnectorPlaylist,
    ConnectorTrackRef,
    Playlist,
    PlaylistEntry,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.exceptions import ConfirmationRequiredError, ConnectorSyncError
from src.domain.playlist.diff_engine import PlaylistOpsOutcome
from tests.fixtures import (
    make_connector_playlist,
    make_connector_playlist_item,
    make_mock_metric_config,
    make_mock_uow,
    make_track,
)

_ENGINE_MOD = "src.application.services.playlist_reconciliation_engine"
_PUSH_MOD = "src.application.services.connector_push"


def _link(direction: SyncDirection) -> PlaylistLink:
    return PlaylistLink(
        id=uuid7(),
        playlist_id=uuid7(),
        connector_name="spotify",
        connector_playlist_identifier="ext1",
        sync_direction=direction,
    )


def _tracks(count: int, start: int = 0):
    """Tracks each carrying a distinct spotify connector id (s0, s1, …)."""
    return [
        make_track(title=f"T{i}", connector_track_identifiers={"spotify": f"s{i}"})
        for i in range(start, start + count)
    ]


def _canonical(tracks) -> Playlist:
    return Playlist(name="Canon", entries=[PlaylistEntry(track=t) for t in tracks])


def _remote(spotify_ids: list[str], *, snapshot: str = "snap") -> ConnectorPlaylist:
    return make_connector_playlist(
        items=[
            make_connector_playlist_item(sid, i) for i, sid in enumerate(spotify_ids)
        ],
        snapshot_id=snapshot,
    )


def _engine() -> PlaylistReconciliationEngine:
    return PlaylistReconciliationEngine(metric_config=make_mock_metric_config())


def _uow_with(canonical: Playlist, resolve_map: dict | None = None, *, base=None):
    uow = make_mock_uow()
    uow.get_playlist_repository().get_playlist_by_id = AsyncMock(return_value=canonical)
    uow.get_connector_repository().find_tracks_by_connectors = AsyncMock(
        return_value=resolve_map or {}
    )
    uow.get_playlist_sync_base_repository().get_for_link = AsyncMock(return_value=base)
    return uow


class TestSafetyAgainstFreshRemote:
    async def test_destructive_push_flags_against_fetched_remote(self):
        # Canonical has 5 tracks; the FRESH remote has 150. Pushing canonical
        # would remove 145 from the remote → flagged. (The old code diffed
        # canonical-vs-itself and never flagged this.)
        canonical = _canonical(_tracks(5))
        remote = _remote([f"s{i}" for i in range(150)])
        uow = _uow_with(canonical)

        with patch(
            f"{_ENGINE_MOD}.sync_connector_playlist", AsyncMock(return_value=remote)
        ):
            with pytest.raises(ConfirmationRequiredError) as exc:
                await _engine().apply(
                    _link(SyncDirection.PUSH),
                    SyncDirection.PUSH,
                    uow,
                    user_id="u",
                    confirmed=False,
                )
        assert exc.value.removals == 145

    async def test_confirmed_destructive_push_proceeds(self):
        all_tracks = _tracks(150)
        canonical = _canonical(all_tracks[:5])
        remote = _remote([t.connector_track_identifiers["spotify"] for t in all_tracks])
        resolve_map = {
            ("spotify", t.connector_track_identifiers["spotify"]): t for t in all_tracks
        }
        uow = _uow_with(canonical, resolve_map)
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(snapshot_id="new", requested=145, failed=0)
        )

        with (
            patch(
                f"{_ENGINE_MOD}.sync_connector_playlist", AsyncMock(return_value=remote)
            ),
            patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector),
        ):
            result = await _engine().apply(
                _link(SyncDirection.PUSH),
                SyncDirection.PUSH,
                uow,
                user_id="u",
                confirmed=True,
            )
        assert result.skipped is False
        connector.execute_playlist_operations.assert_awaited_once()


class TestPushTargetResolvedOnly:
    """A PUSH gate counts removals against RESOLVED canonical tracks only — matching
    what the executor can place. An unresolved canonical entry's source id must not
    mask a removal the push would perform (the gate/execution divergence fix)."""

    async def test_unresolved_entries_do_not_mask_push_removals(self):
        # Canonical: 1 resolved track (s0) + 12 UNRESOLVED entries (x0..x11), all on
        # the remote. A push can only place the resolved track, so it would remove
        # the other 12 — the gate must flag that, not treat the unresolved source
        # ids as "kept" (which would yield 0 removals and a silent destructive push).
        resolved = _tracks(1)[0]
        unresolved = [
            PlaylistEntry(
                track=None,
                connector_track_ref=ConnectorTrackRef(
                    connector_name="spotify", connector_track_identifier=f"x{i}"
                ),
            )
            for i in range(12)
        ]
        canonical = Playlist(
            name="Canon", entries=[PlaylistEntry(track=resolved), *unresolved]
        )
        remote = _remote(["s0", *(f"x{i}" for i in range(12))])
        uow = _uow_with(canonical)

        with patch(
            f"{_ENGINE_MOD}.sync_connector_playlist", AsyncMock(return_value=remote)
        ):
            with pytest.raises(ConfirmationRequiredError) as exc:
                await _engine().apply(
                    _link(SyncDirection.PUSH),
                    SyncDirection.PUSH,
                    uow,
                    user_id="u",
                    confirmed=False,
                )
        assert exc.value.removals == 12


class TestIdempotency:
    async def test_unchanged_remote_is_noop(self):
        tracks = _tracks(3)
        canonical = _canonical(tracks)
        remote = _remote([t.connector_track_identifiers["spotify"] for t in tracks])
        uow = _uow_with(canonical)

        with patch(
            f"{_ENGINE_MOD}.sync_connector_playlist", AsyncMock(return_value=remote)
        ):
            result = await _engine().apply(
                _link(SyncDirection.PULL), SyncDirection.PULL, uow, user_id="u"
            )
        assert result.skipped is True


class TestFailLoud:
    async def test_partial_push_raises_connector_sync_error(self):
        canonical = _canonical(_tracks(3))
        remote = _remote([])  # remote empty → push would add 3
        uow = _uow_with(canonical)
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(snapshot_id=None, requested=3, failed=2)
        )

        with (
            patch(
                f"{_ENGINE_MOD}.sync_connector_playlist", AsyncMock(return_value=remote)
            ),
            patch(f"{_PUSH_MOD}.resolve_playlist_connector", return_value=connector),
        ):
            with pytest.raises(ConnectorSyncError):
                await _engine().apply(
                    _link(SyncDirection.PUSH),
                    SyncDirection.PUSH,
                    uow,
                    user_id="u",
                    confirmed=True,
                )


class TestPullApply:
    async def test_pull_overwrites_canonical(self):
        remote_tracks = _tracks(3)
        canonical = _canonical(remote_tracks[:1])  # subset → non-destructive add
        remote = _remote([
            t.connector_track_identifiers["spotify"] for t in remote_tracks
        ])
        uow = _uow_with(canonical)

        with (
            patch(
                f"{_ENGINE_MOD}.sync_connector_playlist", AsyncMock(return_value=remote)
            ),
            patch(f"{_ENGINE_MOD}.upsert_canonical_playlist", AsyncMock()) as upsert,
        ):
            result = await _engine().apply(
                _link(SyncDirection.PULL), SyncDirection.PULL, uow, user_id="u"
            )
        upsert.assert_awaited_once()
        assert result.skipped is False
        assert result.direction == SyncDirection.PULL
        assert result.tracks_added == 2
