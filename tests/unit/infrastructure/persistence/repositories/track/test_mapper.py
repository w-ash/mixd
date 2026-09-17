"""Unit tests for the track mapper's display walk.

The mapper is a pure function of the loaded row: it never writes. These pin
the walk order (live mappings before the denormalized column — v0.8.18 FM4b)
and the fallback selection (highest confidence, then lowest id — the same
total order ``ensure_primary_for_connector`` elects by, FM4c), plus the one
signal a read emits when it meets a pair with no primary.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.track.mapper import (
    MissingPrimaryMappingWarning,
    TrackMapper,
)


def _transient_track(*, spotify_id: str | None) -> DBTrack:
    """Build a transient DBTrack (no session) for mapper-level tests."""
    track = DBTrack(
        id=uuid7(),
        user_id="default",
        version=1,
        title="Gold Rush",
        artists={"names": ["Neon Priest"]},
        spotify_id=spotify_id,
    )
    track.mappings = []
    track.likes = []
    return track


def _transient_mapping(
    track: DBTrack, *, identifier: str, confidence: int, is_primary: bool = False
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
        match_method="direct",
        confidence=confidence,
        is_primary=is_primary,
        origin="automatic",
    )
    mapping.connector_track = connector_track
    return mapping


class TestWalkWinsOverDenormColumn:
    """The mapping walk runs first; the denormalized column is a post-walk
    fallback only (FM4b)."""

    async def test_primary_mapping_beats_stale_column(self):
        track = _transient_track(spotify_id="sp_dead_col")
        track.mappings = [
            _transient_mapping(
                track, identifier="sp_live_row", confidence=95, is_primary=True
            )
        ]

        domain_track = await TrackMapper.to_domain(track)

        assert domain_track.connector_track_identifiers["spotify"] == "sp_live_row"

    async def test_live_secondary_beats_stale_column_and_warns(self):
        track = _transient_track(spotify_id="sp_dead_col")
        track.mappings = [
            _transient_mapping(track, identifier="sp_live_row", confidence=95)
        ]

        with pytest.warns(MissingPrimaryMappingWarning, match=str(track.id)):
            domain_track = await TrackMapper.to_domain(track)

        # The live non-primary mapping still wins over the stale column; the
        # vacancy is reported, not repaired.
        assert domain_track.connector_track_identifiers["spotify"] == "sp_live_row"
        assert not any(m.is_primary for m in track.mappings)

    async def test_column_serves_as_fallback_without_mappings(self):
        """Fast path preserved: with no mappings, the column id is returned."""
        track = _transient_track(spotify_id="sp_col_only")

        domain_track = await TrackMapper.to_domain(track)

        assert domain_track.connector_track_identifiers["spotify"] == "sp_col_only"


class TestFallbackSelectsHighestConfidence:
    """Display's fallback picks the row ``ensure_primary_for_connector`` would
    elect — (confidence desc, id asc) — so the two can never diverge (FM4c)."""

    async def test_highest_confidence_wins_regardless_of_order(self):
        track = _transient_track(spotify_id=None)
        low = _transient_mapping(track, identifier="sp_low", confidence=40)
        high = _transient_mapping(track, identifier="sp_high", confidence=95)
        track.mappings = [low, high]  # low first in iteration order

        with pytest.warns(MissingPrimaryMappingWarning):
            domain_track = await TrackMapper.to_domain(track)

        assert domain_track.connector_track_identifiers["spotify"] == "sp_high"

    async def test_equal_confidence_breaks_tie_on_lowest_id(self):
        track = _transient_track(spotify_id=None)
        first = _transient_mapping(track, identifier="sp_first", confidence=80)
        second = _transient_mapping(track, identifier="sp_second", confidence=80)
        # uuid7 is monotonic, so `first` has the lower id. Put it LAST in
        # iteration order to prove selection is by id, not by list position.
        assert first.id < second.id
        track.mappings = [second, first]

        with pytest.warns(MissingPrimaryMappingWarning):
            domain_track = await TrackMapper.to_domain(track)

        assert domain_track.connector_track_identifiers["spotify"] == "sp_first"
