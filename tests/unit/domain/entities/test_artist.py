"""The artist aggregate: vocabulary, sentinels, and the tenant requirement.

Imports from ``src.domain.entities.artist`` directly rather than the package
``__init__`` while the credit rename lands alongside this module.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.domain.entities.artist import (
    ARTIST_KINDS,
    VARIOUS_ARTISTS_SENTINELS,
    Artist,
    ArtistAlias,
    ArtistFavorite,
    ArtistMapping,
    ConnectorArtist,
    is_artist_kind,
    is_various_artists,
)


class TestArtistKindVocabulary:
    def test_kinds_come_from_the_literal(self) -> None:
        assert {"person", "group", "other"} == ARTIST_KINDS

    def test_guard_accepts_a_member(self) -> None:
        assert is_artist_kind("group")

    def test_guard_rejects_a_non_member(self) -> None:
        assert not is_artist_kind("orchestra")


class TestVariousArtistsSentinels:
    def test_the_two_observed_spellings_are_members(self) -> None:
        assert {"various artists", "various"} <= VARIOUS_ARTISTS_SENTINELS

    @pytest.mark.parametrize(
        "name", ["Various Artists", "  various artists  ", "VA", "V/A"]
    )
    def test_match_is_case_insensitive_and_stripped(self, name: str) -> None:
        assert is_various_artists(name)

    @pytest.mark.parametrize("name", ["Various Cruelties", "Caribou", ""])
    def test_a_real_artist_is_not_a_sentinel(self, name: str) -> None:
        assert not is_various_artists(name)


class TestArtist:
    def test_defaults_leave_identity_unclaimed(self) -> None:
        artist = Artist(name="Caribou", user_id="u1")
        assert artist.mbid is None
        assert artist.kind is None
        assert artist.id is not None

    def test_name_must_be_a_string(self) -> None:
        with pytest.raises(TypeError):
            Artist(name=123, user_id="u1")  # pyright: ignore[reportArgumentType]

    def test_two_artists_may_share_a_name(self) -> None:
        # Same-name artists are real; identity lives in the mappings.
        first, second = (
            Artist(name="Jungle", user_id="u1"),
            Artist(name="Jungle", user_id="u1"),
        )
        assert first.id != second.id


class TestTenantIsRequired:
    def test_artist_without_user_id_raises(self) -> None:
        with pytest.raises(TypeError, match="user_id"):
            Artist(name="Four Tet")  # pyright: ignore[reportCallIssue]

    def test_favorite_without_user_id_raises(self) -> None:
        with pytest.raises(TypeError, match="user_id"):
            ArtistFavorite(artist_id=uuid7())  # pyright: ignore[reportCallIssue]

    def test_mapping_without_user_id_raises(self) -> None:
        with pytest.raises(TypeError, match="user_id"):
            ArtistMapping(match_method="mbid_match")  # pyright: ignore[reportCallIssue]


class TestArtistFavorite:
    def test_presence_row_carries_no_state_flag(self) -> None:
        favorite = ArtistFavorite(artist_id=uuid7(), user_id="u1")
        assert not hasattr(favorite, "is_favorited")
        assert not hasattr(favorite, "service")


class TestConnectorArtist:
    def test_raw_metadata_defaults_to_an_empty_dict(self) -> None:
        connector_artist = ConnectorArtist(
            connector_name="discogs",
            connector_artist_identifier="17004",
            name="Tycho",
        )
        assert connector_artist.raw_metadata == {}

    def test_each_instance_gets_its_own_metadata(self) -> None:
        one = ConnectorArtist(
            connector_name="spotify", connector_artist_identifier="a", name="A"
        )
        two = ConnectorArtist(
            connector_name="spotify", connector_artist_identifier="b", name="B"
        )
        one.raw_metadata["followers"] = 1
        assert two.raw_metadata == {}


class TestArtistMapping:
    def test_mapping_carries_no_supersession_fields(self) -> None:
        mapping = ArtistMapping(
            user_id="u1",
            artist_id=uuid7(),
            connector_artist_id=uuid7(),
            match_method="mbid_match",
        )
        for absent in (
            "superseded_by_id",
            "superseded_at",
            "supersession_reason",
            "supersession_scope",
            "next_verify_at",
        ):
            assert not hasattr(mapping, absent), absent

    def test_match_method_has_no_default(self) -> None:
        with pytest.raises(TypeError, match="match_method"):
            ArtistMapping(user_id="u1")  # pyright: ignore[reportCallIssue]

    def test_defaults_are_automatic_and_non_primary(self) -> None:
        mapping = ArtistMapping(
            user_id="u1",
            artist_id=uuid7(),
            connector_artist_id=uuid7(),
            match_method="direct",
        )
        assert mapping.origin == "automatic"
        assert not mapping.is_primary
        assert mapping.confidence == 0


class TestArtistAlias:
    def test_optional_source_fields_default_to_none(self) -> None:
        alias = ArtistAlias(connector_artist_id=uuid7(), name="Dan Snaith")
        assert (alias.sort_name, alias.alias_type, alias.locale) == (None, None, None)
        assert not alias.is_primary

    def test_fetched_at_is_carried_verbatim(self) -> None:
        moment = datetime(2026, 9, 19, tzinfo=UTC)
        alias = ArtistAlias(
            connector_artist_id=uuid7(), name="Manitoba", fetched_at=moment
        )
        assert alias.fetched_at == moment
