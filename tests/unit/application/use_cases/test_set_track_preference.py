"""Unit tests for SetTrackPreferenceUseCase.

Tests use mock UoW to verify use case logic: idempotency guards, source
priority enforcement, event logging, and removal semantics.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.application.use_cases.set_track_preference import (
    SetTrackPreferenceCommand,
    SetTrackPreferenceUseCase,
)
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_track, make_track_preference


def _cmd(
    track_id=None,
    state="star",
    source="manual",
    user_id="default",
    preferred_at=None,
) -> SetTrackPreferenceCommand:
    return SetTrackPreferenceCommand(
        user_id=user_id,
        track_id=track_id or uuid7(),
        state=state,
        source=source,
        preferred_at=preferred_at or datetime.now(UTC),
    )


def _uow_with_existing(track, existing=None):
    """Build a mock UoW with track repo + preference repo pre-wired."""
    uow = make_mock_uow()
    uow.get_track_repository().get_track_by_id.return_value = track
    uow.get_preference_repository().get_preferences.return_value = (
        {track.id: existing} if existing else {}
    )
    return uow


def _written_pref(uow):
    """Extract the single preference passed to set_preferences."""
    return uow.get_preference_repository().set_preferences.call_args[0][0][0]


def _written_event(uow):
    """Extract the single event passed to add_events."""
    return uow.get_preference_repository().add_events.call_args[0][0][0]


class TestNewPreference:
    """Setting a preference on a track with no existing preference."""

    async def test_creates_preference_and_event(self) -> None:
        track = make_track()
        uow = _uow_with_existing(track)

        cmd = _cmd(track_id=track.id, state="star")
        result = await SetTrackPreferenceUseCase().execute(cmd, uow)

        assert result.changed is True
        assert result.state == "star"

        pref_repo = uow.get_preference_repository()
        pref_repo.set_preferences.assert_called_once()
        pref_repo.add_events.assert_called_once()

        event = _written_event(uow)
        assert event.old_state is None
        assert event.new_state == "star"

    async def test_nonexistent_track_raises(self) -> None:
        uow = make_mock_uow()
        uow.get_track_repository().get_track_by_id.side_effect = NotFoundError(
            "Track not found"
        )

        with pytest.raises(NotFoundError):
            await SetTrackPreferenceUseCase().execute(_cmd(state="yah"), uow)


class TestIdempotency:
    """Same state + same source = no-op."""

    async def test_same_state_and_source_skips(self) -> None:
        track = make_track()
        existing = make_track_preference(
            track_id=track.id, state="star", source="manual"
        )
        uow = _uow_with_existing(track, existing)

        cmd = _cmd(track_id=track.id, state="star", source="manual")
        result = await SetTrackPreferenceUseCase().execute(cmd, uow)

        assert result.changed is False
        uow.get_preference_repository().set_preferences.assert_not_called()
        uow.get_preference_repository().add_events.assert_not_called()


class TestSourcePriority:
    """Source priority enforcement: manual > playlist_assignment > service_import."""

    async def test_service_import_cannot_override_manual(self) -> None:
        track = make_track()
        existing = make_track_preference(
            track_id=track.id, state="nah", source="manual"
        )
        uow = _uow_with_existing(track, existing)

        cmd = _cmd(track_id=track.id, state="star", source="service_import")
        result = await SetTrackPreferenceUseCase().execute(cmd, uow)

        assert result.changed is False
        assert result.state == "nah"

    async def test_manual_overrides_service_import(self) -> None:
        track = make_track()
        existing = make_track_preference(
            track_id=track.id, state="yah", source="service_import"
        )
        uow = _uow_with_existing(track, existing)

        cmd = _cmd(track_id=track.id, state="star", source="manual")
        result = await SetTrackPreferenceUseCase().execute(cmd, uow)

        assert result.changed is True
        assert result.state == "star"

    async def test_same_source_upgrades_to_higher_state(self) -> None:
        """service_import yah → service_import star should succeed."""
        track = make_track()
        existing = make_track_preference(
            track_id=track.id, state="yah", source="service_import"
        )
        uow = _uow_with_existing(track, existing)

        cmd = _cmd(track_id=track.id, state="star", source="service_import")
        result = await SetTrackPreferenceUseCase().execute(cmd, uow)

        assert result.changed is True
        assert result.state == "star"

    async def test_same_source_does_not_downgrade(self) -> None:
        """service_import star → service_import yah should be rejected."""
        track = make_track()
        existing = make_track_preference(
            track_id=track.id, state="star", source="service_import"
        )
        uow = _uow_with_existing(track, existing)

        cmd = _cmd(track_id=track.id, state="yah", source="service_import")
        result = await SetTrackPreferenceUseCase().execute(cmd, uow)

        assert result.changed is False
        assert result.state == "star"


class TestRemovePreference:
    """Removing a preference (state=None)."""

    async def test_remove_existing(self) -> None:
        track = make_track()
        existing = make_track_preference(track_id=track.id, state="yah")
        uow = _uow_with_existing(track, existing)

        cmd = _cmd(track_id=track.id, state=None)
        result = await SetTrackPreferenceUseCase().execute(cmd, uow)

        assert result.changed is True
        assert result.state is None
        uow.get_preference_repository().remove_preferences.assert_called_once()

        event = _written_event(uow)
        assert event.old_state == "yah"
        assert event.new_state is None

    async def test_remove_nonexistent_is_noop(self) -> None:
        track = make_track()
        uow = _uow_with_existing(track)

        cmd = _cmd(track_id=track.id, state=None)
        result = await SetTrackPreferenceUseCase().execute(cmd, uow)

        assert result.changed is False
        assert result.state is None


class TestEventLogging:
    """Event captures old_state correctly."""

    async def test_state_change_logs_old_and_new(self) -> None:
        track = make_track()
        existing = make_track_preference(
            track_id=track.id, state="hmm", source="manual"
        )
        uow = _uow_with_existing(track, existing)

        cmd = _cmd(track_id=track.id, state="yah", source="manual")
        await SetTrackPreferenceUseCase().execute(cmd, uow)

        event = _written_event(uow)
        assert event.old_state == "hmm"
        assert event.new_state == "yah"

    async def test_preferred_at_comes_from_command(self) -> None:
        """preferred_at on the event must match the command, not datetime.now."""
        track = make_track()
        source_time = datetime(2024, 10, 1, tzinfo=UTC)
        uow = _uow_with_existing(track)

        cmd = _cmd(track_id=track.id, state="yah", preferred_at=source_time)
        await SetTrackPreferenceUseCase().execute(cmd, uow)

        event = _written_event(uow)
        assert event.preferred_at == source_time
