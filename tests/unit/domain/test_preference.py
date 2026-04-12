"""Tests for preference domain entities.

Validates TrackPreference and PreferenceEvent construction, PREFERENCE_ORDER
ranking, and the intentional absence of a preferred_at default.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.domain.entities.preference import (
    PREFERENCE_ORDER,
    PreferenceEvent,
    TrackPreference,
    resolve_preference_change,
)


class TestPreferenceOrder:
    """PREFERENCE_ORDER: star > yah > hmm > nah."""

    def test_star_is_highest(self) -> None:
        assert PREFERENCE_ORDER["star"] > PREFERENCE_ORDER["yah"]

    def test_yah_above_hmm(self) -> None:
        assert PREFERENCE_ORDER["yah"] > PREFERENCE_ORDER["hmm"]

    def test_hmm_above_nah(self) -> None:
        assert PREFERENCE_ORDER["hmm"] > PREFERENCE_ORDER["nah"]

    def test_full_ordering(self) -> None:
        sorted_states = sorted(PREFERENCE_ORDER, key=PREFERENCE_ORDER.__getitem__)
        assert sorted_states == ["nah", "hmm", "yah", "star"]


class TestTrackPreference:
    """TrackPreference construction and constraints."""

    def test_valid_construction(self) -> None:
        pref = TrackPreference(
            user_id="user1",
            track_id=uuid7(),
            state="star",
            source="manual",
            preferred_at=datetime.now(UTC),
        )
        assert pref.state == "star"
        assert pref.source == "manual"

    def test_preferred_at_is_required(self) -> None:
        """preferred_at has no default — omitting it must raise TypeError."""
        with pytest.raises(TypeError):
            TrackPreference(  # type: ignore[call-arg]
                user_id="user1",
                track_id=uuid7(),
                state="yah",
                source="manual",
            )

    def test_id_auto_generated(self) -> None:
        pref = TrackPreference(
            user_id="user1",
            track_id=uuid7(),
            state="hmm",
            source="service_import",
            preferred_at=datetime.now(UTC),
        )
        assert pref.id is not None

    def test_updated_at_auto_generated(self) -> None:
        pref = TrackPreference(
            user_id="user1",
            track_id=uuid7(),
            state="nah",
            source="service_import",
            preferred_at=datetime.now(UTC),
        )
        assert pref.updated_at is not None


class TestPreferenceEvent:
    """PreferenceEvent construction for append-only event log."""

    def test_first_preference_old_state_none(self) -> None:
        event = PreferenceEvent(
            user_id="user1",
            track_id=uuid7(),
            old_state=None,
            new_state="yah",
            source="manual",
            preferred_at=datetime.now(UTC),
        )
        assert event.old_state is None
        assert event.new_state == "yah"

    def test_state_change_event(self) -> None:
        event = PreferenceEvent(
            user_id="user1",
            track_id=uuid7(),
            old_state="yah",
            new_state="star",
            source="manual",
            preferred_at=datetime.now(UTC),
        )
        assert event.old_state == "yah"
        assert event.new_state == "star"

    def test_preferred_at_is_required(self) -> None:
        with pytest.raises(TypeError):
            PreferenceEvent(  # type: ignore[call-arg]
                user_id="user1",
                track_id=uuid7(),
                old_state=None,
                new_state="hmm",
                source="service_import",
            )

    def test_new_state_nullable_for_removal(self) -> None:
        """new_state=None represents preference removal."""
        event = PreferenceEvent(
            user_id="user1",
            track_id=uuid7(),
            old_state="yah",
            new_state=None,
            source="manual",
            preferred_at=datetime.now(UTC),
        )
        assert event.new_state is None


class TestResolvePreferenceChange:
    """Shared conflict resolution used by set_preference and sync use cases."""

    def _pref(self, state: str = "yah", source: str = "manual") -> TrackPreference:
        return TrackPreference(
            user_id="u",
            track_id=uuid7(),
            state=state,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            preferred_at=datetime.now(UTC),
        )

    def test_no_existing_applies(self) -> None:
        assert resolve_preference_change(None, "yah", "manual") is True

    def test_same_state_same_source_skips(self) -> None:
        existing = self._pref("star", "manual")
        assert resolve_preference_change(existing, "star", "manual") is False

    def test_service_import_cannot_override_manual(self) -> None:
        existing = self._pref("nah", "manual")
        assert resolve_preference_change(existing, "star", "service_import") is False

    def test_manual_overrides_service_import(self) -> None:
        existing = self._pref("yah", "service_import")
        assert resolve_preference_change(existing, "nah", "manual") is True

    def test_same_source_upgrade_allowed(self) -> None:
        existing = self._pref("yah", "service_import")
        assert resolve_preference_change(existing, "star", "service_import") is True

    def test_same_source_downgrade_rejected(self) -> None:
        existing = self._pref("star", "service_import")
        assert resolve_preference_change(existing, "yah", "service_import") is False
