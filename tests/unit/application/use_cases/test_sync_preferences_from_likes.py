"""Unit tests for SyncPreferencesFromLikesUseCase.

Tests the likes→preferences sync logic: service mapping (Spotify→yah,
Last.fm→star), source priority, idempotency, timestamp preservation,
and batch-write semantics.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

from src.application.use_cases.sync_preferences_from_likes import (
    SyncPreferencesFromLikesCommand,
    SyncPreferencesFromLikesUseCase,
)
from src.domain.entities.track import TrackLike
from tests.fixtures import make_mock_uow, make_track_preference


def _cmd(user_id: str = "default") -> SyncPreferencesFromLikesCommand:
    return SyncPreferencesFromLikesCommand(user_id=user_id)


def _like(track_id=None, service="spotify", liked_at=None) -> TrackLike:
    return TrackLike(
        track_id=track_id or uuid7(),
        service=service,
        user_id="default",
        is_liked=True,
        liked_at=liked_at or datetime(2024, 6, 15, tzinfo=UTC),
    )


def _uow_with_likes(
    spotify: list[TrackLike], lastfm: list[TrackLike], existing: dict | None = None
):
    """Build a mock UoW with prewired likes + existing preferences."""
    uow = make_mock_uow()
    uow.get_like_repository().get_all_liked_tracks.side_effect = [spotify, lastfm]
    uow.get_preference_repository().get_preferences.return_value = existing or {}
    return uow


def _written_prefs(uow):
    """Return the list of preferences passed to set_preferences (or [])."""
    call = uow.get_preference_repository().set_preferences.call_args
    return list(call[0][0]) if call else []


def _written_pref_by_track(uow) -> dict[UUID, object]:
    return {p.track_id: p for p in _written_prefs(uow)}


class TestNewPreferences:
    """Tracks with likes but no existing preference."""

    async def test_spotify_like_creates_yah(self) -> None:
        tid = uuid7()
        uow = _uow_with_likes([_like(tid, "spotify")], [])

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.created == 1
        pref = _written_pref_by_track(uow)[tid]
        assert pref.state == "yah"
        assert pref.source == "service_import"

    async def test_lastfm_love_creates_star(self) -> None:
        tid = uuid7()
        uow = _uow_with_likes([], [_like(tid, "lastfm")])

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.created == 1
        assert _written_pref_by_track(uow)[tid].state == "star"

    async def test_both_services_same_track_star_wins(self) -> None:
        tid = uuid7()
        uow = _uow_with_likes([_like(tid, "spotify")], [_like(tid, "lastfm")])

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.created == 1
        assert _written_pref_by_track(uow)[tid].state == "star"


class TestSourcePriority:
    """Existing preferences with different sources."""

    async def test_manual_not_overwritten(self) -> None:
        tid = uuid7()
        existing = make_track_preference(track_id=tid, state="nah", source="manual")
        uow = _uow_with_likes([_like(tid, "spotify")], [], {tid: existing})

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.skipped == 1
        uow.get_preference_repository().set_preferences.assert_not_called()

    async def test_playlist_mapping_not_overwritten(self) -> None:
        tid = uuid7()
        existing = make_track_preference(
            track_id=tid, state="yah", source="playlist_mapping"
        )
        uow = _uow_with_likes([_like(tid, "spotify")], [], {tid: existing})

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.skipped == 1


class TestSameSourceUpgrade:
    """Existing service_import preference → upgrade but not downgrade."""

    async def test_yah_upgraded_to_star(self) -> None:
        tid = uuid7()
        existing = make_track_preference(
            track_id=tid, state="yah", source="service_import"
        )
        uow = _uow_with_likes([], [_like(tid, "lastfm")], {tid: existing})

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.upgraded == 1
        assert _written_pref_by_track(uow)[tid].state == "star"

    async def test_star_not_downgraded_to_yah(self) -> None:
        tid = uuid7()
        existing = make_track_preference(
            track_id=tid, state="star", source="service_import"
        )
        uow = _uow_with_likes([_like(tid, "spotify")], [], {tid: existing})

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.skipped == 1
        uow.get_preference_repository().set_preferences.assert_not_called()


class TestIdempotency:
    """Re-running sync with unchanged data is a no-op."""

    async def test_same_state_same_source_skips(self) -> None:
        tid = uuid7()
        existing = make_track_preference(
            track_id=tid, state="yah", source="service_import"
        )
        uow = _uow_with_likes([_like(tid, "spotify")], [], {tid: existing})

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.skipped == 1
        uow.get_preference_repository().set_preferences.assert_not_called()
        uow.get_preference_repository().add_events.assert_not_called()


class TestTimestampPreservation:
    """preferred_at must come from the source liked_at, not datetime.now."""

    async def test_preferred_at_equals_source_liked_at(self) -> None:
        tid = uuid7()
        source_time = datetime(2019, 3, 22, tzinfo=UTC)
        uow = _uow_with_likes([_like(tid, "spotify", liked_at=source_time)], [])

        await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        pref = _written_pref_by_track(uow)[tid]
        assert pref.preferred_at == source_time

        event = uow.get_preference_repository().add_events.call_args[0][0][0]
        assert event.preferred_at == source_time


class TestBatchWrites:
    """Multiple changed tracks produce exactly one set_preferences + one add_events call."""

    async def test_many_tracks_one_write_each(self) -> None:
        likes = [_like(uuid7(), "spotify") for _ in range(20)]
        uow = _uow_with_likes(likes, [])

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.created == 20
        pref_repo = uow.get_preference_repository()
        assert pref_repo.set_preferences.call_count == 1
        assert pref_repo.add_events.call_count == 1
        assert len(_written_prefs(uow)) == 20
        events_call = pref_repo.add_events.call_args
        assert len(events_call[0][0]) == 20


class TestNoLikes:
    """No likes to sync."""

    async def test_empty_likes_returns_zeros(self) -> None:
        uow = _uow_with_likes([], [])

        result = await SyncPreferencesFromLikesUseCase().execute(_cmd(), uow)

        assert result.created == 0
        assert result.upgraded == 0
        assert result.skipped == 0
        uow.get_preference_repository().set_preferences.assert_not_called()
