"""Tests for tag domain entities.

Validates normalize_tag and parse_tag rules (the only non-trivial logic
in this module) and TrackTag.create factory behavior. TrackTag / TagEvent
are otherwise attrs-level data holders and are not re-tested as attrs
features. Construction uses the shared ``make_track_tag`` /
``make_tag_event`` factories from ``tests.fixtures``.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.domain.entities.tag import (
    MAX_TAG_LENGTH,
    TagEvent,
    TrackTag,
    normalize_tag,
    parse_tag,
)
from tests.fixtures import make_tag_event, make_track_tag


class TestNormalizeTagHappyPath:
    """normalize_tag lowercases, trims, and collapses whitespace."""

    def test_lowercases_namespace_and_value(self) -> None:
        assert normalize_tag("Mood:Chill") == "mood:chill"

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        assert normalize_tag("  mood:chill  ") == "mood:chill"

    def test_collapses_internal_whitespace_runs(self) -> None:
        assert normalize_tag("  hello   world  ") == "hello world"

    def test_allows_underscore_hyphen_and_digits(self) -> None:
        assert normalize_tag("Energy_9-Hi") == "energy_9-hi"

    def test_preserves_single_colon_for_namespace(self) -> None:
        assert normalize_tag("context:workout") == "context:workout"

    def test_preserves_nested_colons_in_value(self) -> None:
        """normalize does not touch internal colons — parse_tag handles them."""
        assert normalize_tag("mood:chill:vibes") == "mood:chill:vibes"

    def test_tab_and_newline_treated_as_whitespace(self) -> None:
        assert normalize_tag("mood\tchill\nvibes") == "mood chill vibes"


class TestNormalizeTagRejects:
    """normalize_tag raises ValueError on invalid input."""

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            normalize_tag("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            normalize_tag("   ")

    def test_leading_colon(self) -> None:
        with pytest.raises(ValueError, match="start or end with"):
            normalize_tag(":value")

    def test_trailing_colon(self) -> None:
        with pytest.raises(ValueError, match="start or end with"):
            normalize_tag("namespace:")

    def test_single_colon(self) -> None:
        with pytest.raises(ValueError, match="start or end with"):
            normalize_tag(":")

    def test_over_max_length(self) -> None:
        with pytest.raises(ValueError, match="characters or fewer"):
            normalize_tag("a" * (MAX_TAG_LENGTH + 1))

    def test_exactly_max_length_is_accepted(self) -> None:
        """Boundary: MAX_TAG_LENGTH chars is the largest valid length."""
        assert normalize_tag("a" * MAX_TAG_LENGTH) == "a" * MAX_TAG_LENGTH

    def test_special_character(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            normalize_tag("cafe!")

    def test_unicode_rejected(self) -> None:
        """Normalization is ASCII-only — keeps autocomplete index simple."""
        with pytest.raises(ValueError, match="invalid characters"):
            normalize_tag("café")

    def test_slash_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            normalize_tag("genre/rock")


class TestParseTag:
    """parse_tag splits a normalized tag on the first colon."""

    def test_namespace_and_value(self) -> None:
        assert parse_tag("mood:chill") == ("mood", "chill")

    def test_nested_colons_stay_in_value(self) -> None:
        """Only the first colon splits — everything after is the value."""
        assert parse_tag("mood:chill:vibes") == ("mood", "chill:vibes")

    def test_no_colon_returns_none_namespace(self) -> None:
        assert parse_tag("banger") == (None, "banger")


class TestTrackTagCreate:
    """TrackTag.create normalizes, parses, and assembles in one step."""

    def test_builds_normalized_entity_from_raw(self) -> None:
        tag = make_track_tag(tag="Mood:Chill")
        assert tag.tag == "mood:chill"
        assert tag.namespace == "mood"
        assert tag.value == "chill"

    def test_unnamespaced_tag_has_none_namespace(self) -> None:
        tag = make_track_tag(tag="banger")
        assert tag.namespace is None
        assert tag.value == "banger"

    def test_invalid_raw_tag_raises(self) -> None:
        with pytest.raises(ValueError):
            make_track_tag(tag="cafe!")

    def test_namespace_and_value_cannot_be_overridden(self) -> None:
        """Derived fields reject constructor kwargs — no inconsistent triples."""
        with pytest.raises(TypeError):
            TrackTag(  # type: ignore[call-arg]
                user_id="u1",
                track_id=uuid7(),
                tag="mood:chill",
                namespace="energy",
                value="wrong",
                tagged_at=datetime.now(UTC),
                source="manual",
            )


class TestTrackTag:
    """Direct TrackTag construction invariants."""

    def test_tagged_at_is_required(self) -> None:
        """tagged_at has no default — callers must provide explicitly."""
        with pytest.raises(TypeError):
            TrackTag(  # type: ignore[call-arg]
                user_id="u1",
                track_id=uuid7(),
                tag="mood:chill",
                source="manual",
            )

    def test_id_auto_generated(self) -> None:
        tag = make_track_tag()
        assert tag.id is not None


class TestTagEvent:
    """TagEvent construction for the append-only event log."""

    def test_add_event(self) -> None:
        assert make_tag_event(action="add").action == "add"

    def test_remove_event(self) -> None:
        assert make_tag_event(action="remove").action == "remove"

    def test_tagged_at_is_required(self) -> None:
        with pytest.raises(TypeError):
            TagEvent(  # type: ignore[call-arg]
                user_id="u1",
                track_id=uuid7(),
                tag="mood:chill",
                action="add",
                source="manual",
            )
