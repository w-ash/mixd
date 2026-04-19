"""Unit tests for PlaylistMetadataMapping domain entity + validator.

Pure-function tests: constructor validation, ``create()`` normalization,
invalid input rejection. No I/O, no mocks.
"""

from uuid import uuid7

import pytest

from src.domain.entities.playlist_metadata_mapping import (
    PlaylistMappingMember,
    PlaylistMetadataMapping,
    validate_action_value,
)


class TestValidateActionValue:
    def test_valid_preference_states(self) -> None:
        for state in ("hmm", "nah", "yah", "star"):
            assert validate_action_value("set_preference", state) == state

    def test_invalid_preference_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be one of"):
            validate_action_value("set_preference", "love")

    def test_tag_normalized(self) -> None:
        assert validate_action_value("add_tag", "Mood:Chill") == "mood:chill"
        assert (
            validate_action_value("add_tag", "  CONTEXT:Workout  ") == "context:workout"
        )

    def test_invalid_tag_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_action_value("add_tag", "mood/chill")


class TestConstructor:
    def test_canonical_preference_accepted(self) -> None:
        mapping = PlaylistMetadataMapping(
            user_id="u",
            connector_playlist_id=uuid7(),
            action_type="set_preference",
            action_value="star",
        )
        assert mapping.action_value == "star"

    def test_canonical_tag_accepted(self) -> None:
        mapping = PlaylistMetadataMapping(
            user_id="u",
            connector_playlist_id=uuid7(),
            action_type="add_tag",
            action_value="mood:chill",
        )
        assert mapping.action_value == "mood:chill"

    def test_non_canonical_tag_normalized_on_construct(self) -> None:
        """Direct construction normalizes the same way ``create()`` does, so
        CLI / migration callers that bypass ``create()`` still produce a
        valid entity rather than persisting un-normalized input."""
        mapping = PlaylistMetadataMapping(
            user_id="u",
            connector_playlist_id=uuid7(),
            action_type="add_tag",
            action_value="Mood:Chill",
        )
        assert mapping.action_value == "mood:chill"

    def test_invalid_preference_via_construct_raises(self) -> None:
        with pytest.raises(ValueError, match="must be one of"):
            PlaylistMetadataMapping(
                user_id="u",
                connector_playlist_id=uuid7(),
                action_type="set_preference",
                action_value="love",
            )


class TestCreateClassmethod:
    def test_normalizes_tag_from_raw(self) -> None:
        cpid = uuid7()
        mapping = PlaylistMetadataMapping.create(
            user_id="u",
            connector_playlist_id=cpid,
            action_type="add_tag",
            raw_action_value="Mood:Chill",
        )
        assert mapping.action_value == "mood:chill"
        assert mapping.connector_playlist_id == cpid

    def test_validates_preference_from_raw(self) -> None:
        mapping = PlaylistMetadataMapping.create(
            user_id="u",
            connector_playlist_id=uuid7(),
            action_type="set_preference",
            raw_action_value="star",
        )
        assert mapping.action_value == "star"

    def test_invalid_preference_raises(self) -> None:
        with pytest.raises(ValueError):
            PlaylistMetadataMapping.create(
                user_id="u",
                connector_playlist_id=uuid7(),
                action_type="set_preference",
                raw_action_value="superfan",
            )


class TestPlaylistMappingMember:
    def test_default_synced_at(self) -> None:
        member = PlaylistMappingMember(
            user_id="u", mapping_id=uuid7(), track_id=uuid7()
        )
        assert member.synced_at is not None

    def test_explicit_synced_at_preserved(self) -> None:
        from datetime import UTC, datetime

        ts = datetime(2025, 6, 1, tzinfo=UTC)
        member = PlaylistMappingMember(
            user_id="u", mapping_id=uuid7(), track_id=uuid7(), synced_at=ts
        )
        assert member.synced_at == ts
