"""Tests for bespoke command field validators.

Only covers logic that attrs's built-in validators cannot express.
We do not re-test attrs built-ins (instance_of, min_len, in_, ge, le, optional).
"""

from attrs import define, field
import pytest

from src.application.use_cases._shared.command_validators import (
    api_batch_size_validator,
    non_empty_string,
    validate_tracklist_has_tracks,
)
from src.domain.entities.track import Artist, Track, TrackList


class TestNonEmptyString:
    """Tests for non_empty_string validator (whitespace-stripping behavior)."""

    def test_rejects_empty_string(self):
        @define
        class TestCommand:
            name: str = field(validator=non_empty_string)

        with pytest.raises(ValueError, match="must be a non-empty string"):
            TestCommand(name="")

    def test_rejects_whitespace_only_string(self):
        """Differs from attrs.validators.min_len(1) — strips whitespace first."""

        @define
        class TestCommand:
            name: str = field(validator=non_empty_string)

        with pytest.raises(ValueError, match="must be a non-empty string"):
            TestCommand(name="   ")

    def test_accepts_string_with_leading_trailing_whitespace(self):
        """Should accept strings with content even if they have whitespace."""

        @define
        class TestCommand:
            name: str = field(validator=non_empty_string)

        cmd = TestCommand(name="  My Playlist  ")
        assert cmd.name == "  My Playlist  "


class TestValidateTracklistHasTracks:
    """Tests for validate_tracklist_has_tracks validator."""

    def test_rejects_empty_tracklist(self):
        @define
        class TestCommand:
            tracklist: TrackList = field(validator=validate_tracklist_has_tracks)

        with pytest.raises(ValueError, match="must contain tracks"):
            TestCommand(tracklist=TrackList())

    def test_accepts_tracklist_with_tracks(self):
        @define
        class TestCommand:
            tracklist: TrackList = field(validator=validate_tracklist_has_tracks)

        artist = Artist(name="Test Artist")
        track = Track(title="Test", artists=[artist])
        cmd = TestCommand(tracklist=TrackList(tracks=[track]))
        assert len(cmd.tracklist.tracks) == 1


class TestApiBatchSizeValidator:
    """Tests for api_batch_size_validator (dynamic settings-based bound)."""

    def test_rejects_oversized_batch(self):
        """Should reject batch sizes exceeding settings limit."""

        @define
        class TestCommand:
            batch_size: int = field(
                validator=api_batch_size_validator("api.spotify_large_batch_size")
            )

        with pytest.raises(ValueError, match="cannot exceed"):
            TestCommand(batch_size=999999)
