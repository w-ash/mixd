"""Integration tests for the read-only play-integrity audit harness (v0.10.1).

The audit's value is that its queries are correct, and a query is only correct
against the *real* stored shape — `connector_plays` keeps artist/track/duration
inside `raw_metadata` JSONB, not as columns, so a plausible-looking SELECT can
be silently wrong. These tests seed through the production repository and then
run the check, so the harness is pinned to what the importers actually write.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from scripts.audit_play_integrity import check_spotify_api_lastfm_delta
from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

_PLAYED_AT = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
_ARTIST = "TEST_AuditArtist"
_TITLE = "TEST_AuditTrack"


def _api_play(*, played_at: datetime = _PLAYED_AT) -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="spotify",
        artist_name=_ARTIST,
        track_name=_TITLE,
        played_at=played_at,
        ms_played=None,
        service_metadata={
            "track_uri": "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
            "duration_ms": 201_000,
        },
        import_timestamp=datetime.now(UTC),
        import_source="spotify_api",
        import_batch_id=f"TEST_BATCH_{uuid4()}",
    )


def _scrobble(*, played_at: datetime) -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="lastfm",
        artist_name=_ARTIST,
        track_name=_TITLE,
        played_at=played_at,
        ms_played=None,
        import_timestamp=datetime.now(UTC),
        import_source="lastfm_api",
        import_batch_id=f"TEST_BATCH_{uuid4()}",
    )


class TestSpotifyApiLastfmDelta:
    async def test_reports_no_data_before_the_first_poll(self, db_session):
        """The query must execute against the real schema even with nothing to read."""
        result = await check_spotify_api_lastfm_delta(db_session)

        assert result.verdict == "NO DATA"
        assert result.count == 0

    async def test_pairs_an_api_play_with_its_nearest_scrobble(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_play_repository()
        inserted, _ = await repo.bulk_insert_connector_plays([
            _api_play(),
            # +3s: inside the |d| <= 5s agreement band — what a START-aligned
            # channel looks like.
            _scrobble(played_at=_PLAYED_AT + timedelta(seconds=3)),
            # Far outside the 10-min window — must not be chosen as the pair.
            _scrobble(played_at=_PLAYED_AT + timedelta(hours=3)),
        ])
        assert inserted == 3

        result = await check_spotify_api_lastfm_delta(db_session)

        assert result.count == 1
        exemplar = result.exemplars[0]
        assert exemplar["delta_seconds"] == 3.0
        assert exemplar["duration_seconds"] == 201.0
        assert any("agree (|d| <= 5s): 1" in note for note in result.notes)
        # Median is within the agreement band, so the check should say the
        # channel looks START-aligned — the signal that calibration can drop
        # spotify_api's tolerance_override.
        assert any("START confirmed" in note for note in result.notes)

    async def test_unpaired_api_play_is_counted_not_dropped(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_play_repository()
        _ = await repo.bulk_insert_connector_plays([_api_play()])

        result = await check_spotify_api_lastfm_delta(db_session)

        assert result.count == 0
        assert any("unpaired: 1" in note for note in result.notes)
