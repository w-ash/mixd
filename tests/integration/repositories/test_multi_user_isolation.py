"""Multi-user data isolation tests.

Proves that user-scoped repository methods enforce tenant isolation.
Pattern: create data as USER_A, query as USER_B, assert invisible.

These tests verify the WHERE clause filtering that is the primary
isolation mechanism. RLS (defense-in-depth) is not exercised here
because testcontainers connects as a superuser.
"""

from datetime import UTC, datetime
from uuid import uuid7

import attrs
import pytest

from src.domain.entities.operations import SyncCheckpoint, TrackPlay
from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track
from src.domain.entities.workflow import Workflow
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures.factories import make_workflow_def

USER_A = "isolation-user-a"
USER_B = "isolation-user-b"


def _new_track(user_id: str, **kwargs) -> Track:
    """Track with id=None for DB insertion (repo assigns the ID)."""
    kwargs.setdefault("title", f"Track_{uuid7()}")
    kwargs.setdefault("artists", [Artist(name="Test Artist")])
    return Track(id=None, user_id=user_id, **kwargs)


# ---------------------------------------------------------------------------
# Track isolation
# ---------------------------------------------------------------------------


class TestTrackIsolation:
    """Tracks belong to a user and are invisible to others."""

    async def test_list_tracks_sees_only_own(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_track_repository()

        track_a = await repo.save_track(_new_track(USER_A, title="A's Track"))
        await repo.save_track(_new_track(USER_B, title="B's Track"))

        result = await repo.list_tracks(user_id=USER_A, limit=50, offset=0)
        assert len(result["tracks"]) == 1
        assert result["tracks"][0].id == track_a.id

    async def test_get_track_by_id_returns_404_for_other_user(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_track_repository()

        track_a = await repo.save_track(_new_track(USER_A))

        with pytest.raises(NotFoundError):
            await repo.get_track_by_id(track_a.id, user_id=USER_B)

    async def test_find_by_isrc_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_track_repository()

        await repo.save_track(_new_track(USER_A, isrc="USRC10000001"))

        result = await repo.find_tracks_by_isrcs(["USRC10000001"], user_id=USER_B)
        assert result == {}

    async def test_both_users_can_have_same_isrc(self, db_session):
        """user_id is part of the unique constraint — no collision."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_track_repository()

        track_a = await repo.save_track(
            _new_track(USER_A, title="Same Song", isrc="USRC10000002")
        )
        track_b = await repo.save_track(
            _new_track(USER_B, title="Same Song", isrc="USRC10000002")
        )

        assert track_a.id != track_b.id

    async def test_find_by_title_artist_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_track_repository()

        await repo.save_track(
            _new_track(USER_A, title="Creep", artists=[Artist(name="Radiohead")])
        )

        result = await repo.find_tracks_by_title_artist(
            [("creep", "radiohead")], user_id=USER_B
        )
        assert result == {}


# ---------------------------------------------------------------------------
# Like isolation
# ---------------------------------------------------------------------------


class TestLikeIsolation:
    """Likes are per-user — one user's likes are invisible to another."""

    async def _create_liked_track(self, uow, user_id: str) -> Track:
        repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()
        track = await repo.save_track(_new_track(user_id))
        await like_repo.save_track_like(
            track.id, "spotify", user_id=user_id, is_liked=True
        )
        return track

    async def test_get_track_likes_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        track_a = await self._create_liked_track(uow, USER_A)

        like_repo = uow.get_like_repository()
        result = await like_repo.get_track_likes(track_a.id, user_id=USER_B)
        assert result == []

    async def test_count_liked_tracks_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        await self._create_liked_track(uow, USER_A)

        like_repo = uow.get_like_repository()
        assert await like_repo.count_liked_tracks("spotify", user_id=USER_A) == 1
        assert await like_repo.count_liked_tracks("spotify", user_id=USER_B) == 0

    async def test_get_liked_status_batch_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        track_a = await self._create_liked_track(uow, USER_A)

        like_repo = uow.get_like_repository()
        result = await like_repo.get_liked_status_batch(
            [track_a.id], ["spotify"], user_id=USER_B
        )
        assert result == {}


# ---------------------------------------------------------------------------
# Play isolation
# ---------------------------------------------------------------------------


class TestPlayIsolation:
    """Play history is per-user."""

    async def _insert_play(self, uow, user_id: str) -> Track:
        repo = uow.get_track_repository()
        plays_repo = uow.get_plays_repository()
        track = await repo.save_track(_new_track(user_id))
        await plays_repo.bulk_insert_plays([
            TrackPlay(
                track_id=track.id,
                service="spotify",
                played_at=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
                user_id=user_id,
                ms_played=200000,
            ),
        ])
        return track

    async def test_get_recent_plays_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        await self._insert_play(uow, USER_A)

        plays_repo = uow.get_plays_repository()
        result = await plays_repo.get_recent_plays(user_id=USER_B, limit=50)
        assert result == []

    async def test_play_aggregations_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        track_a = await self._insert_play(uow, USER_A)

        plays_repo = uow.get_plays_repository()
        result = await plays_repo.get_play_aggregations(
            [track_a.id], ["total_plays"], user_id=USER_B
        )
        assert result.get("total_plays", {}).get(track_a.id, 0) == 0


# ---------------------------------------------------------------------------
# Playlist isolation
# ---------------------------------------------------------------------------


class TestPlaylistIsolation:
    """Playlists are per-user."""

    async def _save_playlist(self, uow, user_id: str, name: str = "Test Playlist"):
        """Create a playlist with a persisted track (FK requirement)."""
        track_repo = uow.get_track_repository()
        track = await track_repo.save_track(_new_track(user_id))

        playlist = Playlist.from_tracklist(name=name, tracklist=[track])
        playlist = attrs.evolve(playlist, user_id=user_id)
        return await uow.get_playlist_repository().save_playlist(playlist)

    async def test_list_playlists_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        await self._save_playlist(uow, USER_A, "A's Playlist")

        result = await uow.get_playlist_repository().list_all_playlists(user_id=USER_B)
        assert result == []

    async def test_get_playlist_by_id_404(self, db_session):
        uow = get_unit_of_work(db_session)
        saved = await self._save_playlist(uow, USER_A)

        with pytest.raises(NotFoundError):
            await uow.get_playlist_repository().get_playlist_by_id(
                saved.id, user_id=USER_B
            )

    async def test_delete_playlist_fails_for_wrong_user(self, db_session):
        uow = get_unit_of_work(db_session)
        saved = await self._save_playlist(uow, USER_A)

        result = await uow.get_playlist_repository().delete_playlist(
            saved.id, user_id=USER_B
        )
        assert result is False


# ---------------------------------------------------------------------------
# Workflow isolation
# ---------------------------------------------------------------------------


class TestWorkflowIsolation:
    """User workflows are private; templates are shared."""

    async def test_list_workflows_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_workflow_repository()

        await repo.save_workflow(
            Workflow(
                id=uuid7(),
                definition=make_workflow_def(id="wf-a", name="Workflow wf-a"),
                user_id=USER_A,
            )
        )

        result = await repo.list_workflows(user_id=USER_B, include_templates=False)
        assert result == []

    async def test_get_workflow_by_id_404(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_workflow_repository()

        wf = await repo.save_workflow(
            Workflow(id=uuid7(), definition=make_workflow_def(), user_id=USER_A)
        )

        with pytest.raises(NotFoundError):
            await repo.get_workflow_by_id(wf.id, user_id=USER_B)

    async def test_delete_workflow_fails_for_wrong_user(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_workflow_repository()

        wf = await repo.save_workflow(
            Workflow(id=uuid7(), definition=make_workflow_def(), user_id=USER_A)
        )

        result = await repo.delete_workflow(wf.id, user_id=USER_B)
        assert result is False

    async def test_template_not_visible_across_users(self, db_session):
        """Templates currently use user_id='default' (NOT NULL), so they are
        scoped like any other workflow. Making templates truly shared (via
        nullable user_id) is a v0.7+ concern."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_workflow_repository()

        await repo.save_workflow(
            Workflow(
                id=uuid7(),
                definition=make_workflow_def(id="tmpl-a", name="Template A"),
                is_template=True,
                user_id=USER_A,
            )
        )

        result = await repo.list_workflows(user_id=USER_B, include_templates=True)
        assert result == []


# ---------------------------------------------------------------------------
# Checkpoint isolation
# ---------------------------------------------------------------------------


class TestCheckpointIsolation:
    """Sync checkpoints are per-user per-service."""

    async def test_checkpoint_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_checkpoint_repository()

        checkpoint = SyncCheckpoint(
            user_id=USER_A,
            service="spotify",
            entity_type="likes",
            last_timestamp=datetime(2024, 6, 1, tzinfo=UTC),
        )
        await repo.save_sync_checkpoint(checkpoint)

        result = await repo.get_sync_checkpoint(USER_B, "spotify", "likes")
        assert result is None

        result_a = await repo.get_sync_checkpoint(USER_A, "spotify", "likes")
        assert result_a is not None


# ---------------------------------------------------------------------------
# Stats isolation
# ---------------------------------------------------------------------------


class TestStatsIsolation:
    """Dashboard aggregates are per-user."""

    async def test_dashboard_aggregates_scoped(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        stats_repo = uow.get_stats_repository()

        for i in range(3):
            await track_repo.save_track(_new_track(USER_A, title=f"Song {i}"))

        agg_a = await stats_repo.get_dashboard_aggregates(user_id=USER_A)
        agg_b = await stats_repo.get_dashboard_aggregates(user_id=USER_B)

        assert agg_a["total_tracks"] == 3
        assert agg_b["total_tracks"] == 0


# ---------------------------------------------------------------------------
# OAuth token isolation
# ---------------------------------------------------------------------------


class TestOAuthTokenIsolation:
    """OAuth tokens are per-user — tested via direct DB model insertion.

    DatabaseTokenStorage creates its own session (not the test db_session),
    so we test via the DB model directly for isolation within the test
    transaction.
    """

    async def test_load_token_scoped(self, db_session):
        from sqlalchemy import select

        from src.infrastructure.persistence.database.db_models import DBOAuthToken
        from src.infrastructure.persistence.repositories.token_encryption import (
            encrypt_field,
        )

        now = datetime.now(UTC)
        db_session.add(
            DBOAuthToken(
                service="spotify",
                user_id=USER_A,
                access_token=encrypt_field("secret-access-token"),
                refresh_token=encrypt_field("secret-refresh-token"),
                token_type="Bearer",
                expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                created_at=now,
                updated_at=now,
            )
        )
        await db_session.flush()

        result_a = (
            await db_session.execute(
                select(DBOAuthToken).where(
                    DBOAuthToken.service == "spotify",
                    DBOAuthToken.user_id == USER_A,
                )
            )
        ).scalar_one_or_none()
        assert result_a is not None

        result_b = (
            await db_session.execute(
                select(DBOAuthToken).where(
                    DBOAuthToken.service == "spotify",
                    DBOAuthToken.user_id == USER_B,
                )
            )
        ).scalar_one_or_none()
        assert result_b is None
