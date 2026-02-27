"""Tests for command field validators.

Tests attrs validators used for command class field validation, ensuring
fail-fast behavior at construction time.
"""

from attrs import define, field
import pytest

from src.application.use_cases._shared.command_validators import (
    and_,
    api_batch_size_validator,
    instance_of,
    non_empty_list,
    non_empty_string,
    optional,
    optional_in_choices,
    optional_positive_int,
    positive_int_in_range,
    tracklist_or_connector_playlist,
)
from src.domain.entities.track import Artist, Track, TrackList


class TestNonEmptyString:
    """Tests for non_empty_string validator."""

    def test_rejects_empty_string(self):
        """Should reject empty strings."""

        @define
        class TestCommand:
            name: str = field(validator=non_empty_string)

        with pytest.raises(ValueError, match="must be a non-empty string"):
            TestCommand(name="")

    def test_rejects_whitespace_only_string(self):
        """Should reject strings with only whitespace."""

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


class TestNonEmptyList:
    """Tests for non_empty_list validator."""

    def test_rejects_empty_list(self):
        """Should reject empty lists."""

        @define
        class TestCommand:
            items: list[str] = field(validator=non_empty_list)

        with pytest.raises(ValueError, match="must be a non-empty list"):
            TestCommand(items=[])


class TestPositiveIntInRange:
    """Tests for positive_int_in_range validator."""

    def test_accepts_min_boundary(self):
        """Should accept minimum boundary value."""

        @define
        class TestCommand:
            limit: int = field(validator=positive_int_in_range(1, 100))

        cmd = TestCommand(limit=1)
        assert cmd.limit == 1

    def test_accepts_max_boundary(self):
        """Should accept maximum boundary value."""

        @define
        class TestCommand:
            limit: int = field(validator=positive_int_in_range(1, 100))

        cmd = TestCommand(limit=100)
        assert cmd.limit == 100

    def test_rejects_below_min(self):
        """Should reject values below minimum."""

        @define
        class TestCommand:
            limit: int = field(validator=positive_int_in_range(1, 100))

        with pytest.raises(ValueError, match="must be between 1 and 100"):
            TestCommand(limit=0)

    def test_rejects_above_max(self):
        """Should reject values above maximum."""

        @define
        class TestCommand:
            limit: int = field(validator=positive_int_in_range(1, 100))

        with pytest.raises(ValueError, match="must be between 1 and 100"):
            TestCommand(limit=101)

    def test_rejects_non_integer(self):
        """Should reject non-integer types."""

        @define
        class TestCommand:
            limit: int = field(validator=positive_int_in_range(1, 100))

        with pytest.raises(TypeError, match="must be an integer"):
            TestCommand(limit="50")  # type: ignore


class TestOptionalPositiveInt:
    """Tests for optional_positive_int validator."""

    def test_rejects_zero(self):
        """Should reject zero."""

        @define
        class TestCommand:
            days_back: int | None = field(validator=optional_positive_int)

        with pytest.raises(ValueError, match="must be positive"):
            TestCommand(days_back=0)

    def test_rejects_negative(self):
        """Should reject negative numbers."""

        @define
        class TestCommand:
            days_back: int | None = field(validator=optional_positive_int)

        with pytest.raises(ValueError, match="must be positive"):
            TestCommand(days_back=-1)


class TestOptionalInChoices:
    """Tests for optional_in_choices validator."""

    def test_rejects_invalid_choice(self):
        """Should reject values not in choices list."""

        @define
        class TestCommand:
            sort_by: str | None = field(
                validator=optional_in_choices(["asc", "desc", "random"])
            )

        with pytest.raises(ValueError, match="must be one of"):
            TestCommand(sort_by="invalid")


class TestTracklistOrConnectorPlaylist:
    """Tests for tracklist_or_connector_playlist validator."""

    def test_accepts_tracklist_with_tracks(self):
        """Should accept TrackList with tracks even without connector_playlist."""

        @define
        class TestCommand:
            tracklist: TrackList = field(validator=tracklist_or_connector_playlist)
            connector_playlist: object | None = None

        artist = Artist(name="Test Artist")
        track = Track(title="Test", artists=[artist])
        cmd = TestCommand(tracklist=TrackList(tracks=[track]))
        assert len(cmd.tracklist.tracks) == 1

    def test_accepts_empty_tracklist_with_connector_playlist(self):
        """Should accept empty TrackList when connector_playlist field is set."""

        @define
        class TestCommand:
            tracklist: TrackList = field(validator=tracklist_or_connector_playlist)
            connector_playlist: object | None = None

        cmd = TestCommand(
            tracklist=TrackList(),
            connector_playlist=object(),  # Any truthy object
        )
        assert len(cmd.tracklist.tracks) == 0

    def test_rejects_empty_tracklist_without_connector_playlist(self):
        """Should reject empty TrackList when connector_playlist is None."""

        @define
        class TestCommand:
            tracklist: TrackList = field(validator=tracklist_or_connector_playlist)
            connector_playlist: object | None = None

        with pytest.raises(
            ValueError, match="must have tracks or command must have connector_playlist"
        ):
            TestCommand(tracklist=TrackList())

    def test_rejects_non_tracklist_type(self):
        """Should reject non-TrackList types."""

        @define
        class TestCommand:
            tracklist: TrackList = field(validator=tracklist_or_connector_playlist)
            connector_playlist: object | None = None

        with pytest.raises(TypeError, match="must be a TrackList"):
            TestCommand(tracklist=[])  # type: ignore


class TestApiBatchSizeValidator:
    """Tests for api_batch_size_validator."""

    def test_rejects_oversized_batch(self):
        """Should reject batch sizes exceeding settings limit."""

        @define
        class TestCommand:
            batch_size: int = field(
                validator=api_batch_size_validator("api.spotify_large_batch_size")
            )

        with pytest.raises(ValueError, match="cannot exceed"):
            TestCommand(batch_size=999999)


class TestValidatorCombiners:
    """Tests for validator combiner utilities."""

    def test_and_combines_validators(self):
        """Should apply multiple validators with AND logic."""

        @define
        class TestCommand:
            name: str = field(
                validator=and_(
                    instance_of(str),
                    non_empty_string,
                )
            )

        cmd = TestCommand(name="Valid Name")
        assert cmd.name == "Valid Name"

        with pytest.raises(ValueError):
            TestCommand(name="")

    def test_optional_allows_none(self):
        """Should allow None when validator is wrapped with optional()."""

        @define
        class TestCommand:
            description: str | None = field(validator=optional(non_empty_string))

        cmd = TestCommand(description=None)
        assert cmd.description is None

        cmd2 = TestCommand(description="Has description")
        assert cmd2.description == "Has description"

        with pytest.raises(ValueError):
            TestCommand(description="")

    def test_instance_of_validates_type(self):
        """Should validate instance type."""

        @define
        class TestCommand:
            tracklist: TrackList = field(validator=instance_of(TrackList))

        tracklist = TrackList(tracks=[])
        cmd = TestCommand(tracklist=tracklist)
        assert isinstance(cmd.tracklist, TrackList)

        with pytest.raises(TypeError):
            TestCommand(tracklist=[])  # type: ignore


class TestIntegrationScenarios:
    """Integration tests combining multiple validators."""

    def test_command_with_multiple_validators(self):
        """Should handle command with multiple field validators."""

        @define(frozen=True, slots=True)
        class GetLikedTracksCommand:
            limit: int = field(validator=positive_int_in_range(1, 10000))
            connector_filter: str | None = field(default=None)
            sort_by: str | None = field(
                default=None,
                validator=optional_in_choices([
                    "liked_at_desc",
                    "liked_at_asc",
                    "title_asc",
                ]),
            )

        # Valid command
        cmd = GetLikedTracksCommand(limit=100, sort_by="liked_at_desc")
        assert cmd.limit == 100
        assert cmd.sort_by == "liked_at_desc"

        # Invalid limit
        with pytest.raises(ValueError, match="must be between"):
            GetLikedTracksCommand(limit=0)

        # Invalid sort option
        with pytest.raises(ValueError, match="must be one of"):
            GetLikedTracksCommand(limit=100, sort_by="invalid")
