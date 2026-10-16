"""Tests for preference domain entities.

Validates PREFERENCE_ORDER ranking, the intentional absence of a preferred_at
default, and the shared preference-change conflict rule.
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

    def test_full_ordering(self) -> None:
        sorted_states = sorted(PREFERENCE_ORDER, key=PREFERENCE_ORDER.__getitem__)
        assert sorted_states == ["nah", "hmm", "yah", "star"]


class TestTrackPreference:
    """TrackPreference construction and constraints."""

    def test_preferred_at_is_required(self) -> None:
        """preferred_at has no default — omitting it must raise TypeError."""
        with pytest.raises(TypeError):
            TrackPreference(  # type: ignore[call-arg]
                user_id="user1",
                track_id=uuid7(),
                state="yah",
                source="manual",
            )

    def test_updated_at_defaults_to_now_in_utc(self) -> None:
        before = datetime.now(UTC)
        pref = TrackPreference(
            user_id="user1",
            track_id=uuid7(),
            state="nah",
            source="service_import",
            preferred_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        after = datetime.now(UTC)

        assert before <= pref.updated_at <= after
        assert pref.updated_at.tzinfo == UTC


class TestPreferenceEvent:
    """PreferenceEvent construction for append-only event log."""

    def test_preferred_at_is_required(self) -> None:
        with pytest.raises(TypeError):
            PreferenceEvent(  # type: ignore[call-arg]
                user_id="user1",
                track_id=uuid7(),
                old_state=None,
                new_state="hmm",
                source="service_import",
            )


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
