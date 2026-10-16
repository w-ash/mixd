"""Unit tests for the track mapper's display walk.

The mapper is a pure function of the loaded row: it never writes. These pin
the fallback selection (highest confidence, then lowest id — the same total
order ``ensure_primary_for_connector`` elects by, FM4c), plus the one signal
a read emits when it meets a pair with no primary. They also pin the credit
source: the ``track_artists`` rows when the read loaded them, else the JSONB
names.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest
from sqlalchemy.orm.attributes import set_committed_value

from src.infrastructure.persistence.database.models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackArtist,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.track.connector import (
    TrackMappingMapper,
)
from src.infrastructure.persistence.repositories.track.mapper import (
    MissingPrimaryMappingWarning,
    TrackMapper,
)


def _transient_track() -> DBTrack:
    """Build a transient DBTrack (no session) for mapper-level tests."""
    track = DBTrack(
        id=uuid7(),
        user_id="default",
        version=1,
        title="Gold Rush",
        artists={"names": ["Neon Priest"]},
    )
    track.mappings = []
    track.likes = []
    return track


def _transient_mapping(
    track: DBTrack,
    *,
    identifier: str,
    confidence: int,
    is_primary: bool = False,
    match_method: str = "direct",
) -> DBTrackMapping:
    """Build a transient non-persisted mapping with its connector track wired."""
    connector_track = DBConnectorTrack(
        id=uuid7(),
        connector_name="spotify",
        connector_track_identifier=identifier,
        title="Gold Rush",
        artists={"names": ["Neon Priest"]},
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    mapping = DBTrackMapping(
        id=uuid7(),
        user_id="default",
        track_id=track.id,
        connector_track_id=connector_track.id,
        connector_name="spotify",
        match_method=match_method,
        confidence=confidence,
        is_primary=is_primary,
        origin="automatic",
    )
    mapping.connector_track = connector_track
    return mapping


def _load_credits(track: DBTrack, *rows: DBTrackArtist) -> None:
    """Set ``artist_credits`` as a loader does, so it reads as loaded."""
    set_committed_value(track, "artist_credits", list(rows))


def _credit_row(
    track: DBTrack,
    position: int,
    name: str,
    *,
    artist_id: UUID | None = None,
    join_phrase: str | None = None,
    role: str | None = None,
) -> DBTrackArtist:
    """Build a transient ``track_artists`` row for ``track``."""
    return DBTrackArtist(
        user_id="default",
        track_id=track.id,
        position=position,
        credited_name=name,
        artist_id=artist_id,
        join_phrase=join_phrase,
        role=role,
    )


class TestCreditSource:
    """Loaded ``track_artists`` rows carry ``artist_id``; the JSONB names do not."""

    async def test_loaded_rows_give_the_credits_in_row_order(self):
        track = _transient_track()
        artist_id = uuid7()
        _load_credits(
            track,
            _credit_row(
                track,
                0,
                "Neon Priest",
                artist_id=artist_id,
                join_phrase=" & ",
                role="main",
            ),
            _credit_row(track, 1, "Velvet Choir", role="featured"),
        )

        domain_track = await TrackMapper.to_domain(track)

        assert [
            (c.credited_name, c.artist_id, c.join_phrase, c.role)
            for c in domain_track.artists
        ] == [
            ("Neon Priest", artist_id, " & ", "main"),
            ("Velvet Choir", None, None, "featured"),
        ]

    async def test_unloaded_rows_give_the_stored_names(self):
        track = _transient_track()

        domain_track = await TrackMapper.to_domain(track)

        assert [(c.credited_name, c.artist_id) for c in domain_track.artists] == [
            ("Neon Priest", None)
        ]


class TestFallbackSelectsHighestConfidence:
    """Display's fallback picks the row ``ensure_primary_for_connector`` would
    elect — (confidence desc, id asc) — so the two can never diverge (FM4c)."""

    async def test_highest_confidence_wins_regardless_of_order(self):
        track = _transient_track()
        low = _transient_mapping(track, identifier="sp_low", confidence=40)
        high = _transient_mapping(track, identifier="sp_high", confidence=95)
        track.mappings = [low, high]  # low first in iteration order

        with pytest.warns(MissingPrimaryMappingWarning):
            domain_track = await TrackMapper.to_domain(track)

        assert domain_track.connector_track_identifiers["spotify"] == "sp_high"

    async def test_equal_confidence_breaks_tie_on_lowest_id(self):
        track = _transient_track()
        first = _transient_mapping(track, identifier="sp_first", confidence=80)
        second = _transient_mapping(track, identifier="sp_second", confidence=80)
        # uuid7 is monotonic, so `first` has the lower id. Put it LAST in
        # iteration order to prove selection is by id, not by list position.
        assert first.id < second.id
        track.mappings = [second, first]

        with pytest.warns(MissingPrimaryMappingWarning):
            domain_track = await TrackMapper.to_domain(track)

        assert domain_track.connector_track_identifiers["spotify"] == "sp_first"

    async def test_a_stale_id_row_is_never_the_display_fallback(self):
        """Display and election agree: no election promotes a stale-id cache
        row, so display never shows its dead id either."""
        track = _transient_track()
        stale = _transient_mapping(
            track,
            identifier="sp_dead",
            confidence=100,
            match_method="direct_import_stale_id",
        )
        live = _transient_mapping(track, identifier="sp_live", confidence=60)
        track.mappings = [stale, live]

        with pytest.warns(MissingPrimaryMappingWarning):
            domain_track = await TrackMapper.to_domain(track)

        assert domain_track.connector_track_identifiers["spotify"] == "sp_live"

    async def test_only_stale_id_rows_show_no_identifier_and_no_warning(self):
        track = _transient_track()
        track.mappings = [
            _transient_mapping(
                track,
                identifier="sp_dead",
                confidence=100,
                match_method="direct_import_stale_id",
            )
        ]

        domain_track = await TrackMapper.to_domain(track)

        assert "spotify" not in domain_track.connector_track_identifiers


class TestMappingRowsOutsideTheVocabulary:
    """A persisted value the domain vocabulary does not name is a schema fact
    nobody designed; the mapper says so instead of narrowing silently."""

    async def test_unknown_match_method_raises(self):
        track = _transient_track()
        mapping = _transient_mapping(track, identifier="sp_x", confidence=50)
        mapping.match_method = "search"

        with pytest.raises(ValueError, match="match_method"):
            _ = await TrackMappingMapper.to_domain(mapping)

    async def test_unknown_origin_raises(self):
        track = _transient_track()
        mapping = _transient_mapping(track, identifier="sp_x", confidence=50)
        mapping.origin = "manual"

        with pytest.raises(ValueError, match="origin"):
            _ = await TrackMappingMapper.to_domain(mapping)
