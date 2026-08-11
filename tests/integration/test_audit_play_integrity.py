"""Integration tests for the read-only play-integrity audit harness (v0.10.1).

The audit's value is that its queries are correct, and a query is only correct
against the *real* stored shape — `connector_plays` keeps artist/track/duration
inside `raw_metadata` JSONB, not as columns, so a plausible-looking SELECT can
be silently wrong. These tests seed through the production repository and then
run the check, so the harness is pinned to what the importers actually write.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from scripts.audit_play_integrity import (
    check_discard_plausibility,
    check_short_track_blind_spot,
    check_spotify_api_lastfm_delta,
)
from src.domain.entities import Artist, ConnectorTrack, ConnectorTrackPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

_PLAYED_AT = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
_ARTIST = "TEST_AuditArtist"
_TITLE = "TEST_AuditTrack"

# Well inside Last.fm's 30s scrobble floor, and long enough to clear it.
_INTERLUDE_MS = 12_000
_FULL_LENGTH_MS = 201_000


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


async def _seed_canonical_track(
    db_session: AsyncSession,
    *,
    connector_id: str,
    title: str,
    duration_ms: int,
) -> UUID:
    """Create connector track → canonical track → live mapping, as an import does.

    Both new checks join the ledger to ``connector_tracks`` for a duration and
    (for discards) require a live mapping, so seeding raw ledger rows alone would
    exercise neither query's join.
    """
    uow = get_unit_of_work(db_session)
    tracks = await uow.get_connector_repository().ingest_external_tracks_bulk(
        "spotify",
        [
            ConnectorTrack(
                connector_name="spotify",
                connector_track_identifier=connector_id,
                title=title,
                artists=[Artist(name=_ARTIST)],
                duration_ms=duration_ms,
                raw_metadata={},
                last_updated=datetime.now(UTC),
            )
        ],
        user_id="default",
    )
    await db_session.flush()
    track_id = tracks[0].id
    assert track_id is not None
    return track_id


def _export_play(
    *,
    connector_id: str,
    title: str,
    played_at: datetime,
    ms_played: int,
    resolved_track_id: UUID | None = None,
    reason_start: str = "clickrow",
    incognito: bool = False,
) -> ConnectorTrackPlay:
    """One GDPR-export ledger row; ``played_at`` marks the END of the play."""
    return ConnectorTrackPlay(
        service="spotify",
        artist_name=_ARTIST,
        track_name=title,
        played_at=played_at,
        ms_played=ms_played,
        service_metadata={
            "track_uri": f"spotify:track:{connector_id}",
            "reason_start": reason_start,
            "reason_end": "endplay",
            "incognito_mode": incognito,
        },
        import_timestamp=datetime.now(UTC),
        import_source="spotify_export",
        import_batch_id=f"TEST_BATCH_{uuid4()}",
        resolved_track_id=resolved_track_id,
        resolved_at=datetime.now(UTC) if resolved_track_id else None,
    )


class TestShortTrackBlindSpot:
    async def test_reports_an_empty_bucket_against_the_real_schema(self, db_session):
        result = await check_short_track_blind_spot(db_session)

        assert result.count == 0
        assert any("excluded as structural blind spot: 0" in n for n in result.notes)

    async def test_short_track_play_leaves_the_pairing_denominator(self, db_session):
        """A 12s track cannot be scrobbled, so it must not count as a miss."""
        interlude_id = await _seed_canonical_track(
            db_session,
            connector_id="TEST_short_001",
            title="TEST_Interlude",
            duration_ms=_INTERLUDE_MS,
        )
        full_id = await _seed_canonical_track(
            db_session,
            connector_id="TEST_full_001",
            title=_TITLE,
            duration_ms=_FULL_LENGTH_MS,
        )
        started_at = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        inserted, _ = await repo.bulk_insert_connector_plays([
            _export_play(
                connector_id="TEST_full_001",
                title=_TITLE,
                played_at=started_at + timedelta(milliseconds=_FULL_LENGTH_MS),
                ms_played=_FULL_LENGTH_MS,
                resolved_track_id=full_id,
            ),
            _export_play(
                connector_id="TEST_short_001",
                title="TEST_Interlude",
                played_at=started_at + timedelta(hours=1, milliseconds=_INTERLUDE_MS),
                ms_played=_INTERLUDE_MS,
                resolved_track_id=interlude_id,
            ),
            # Same day, so both export plays sit on a scrobble-covered day.
            _scrobble(played_at=started_at + timedelta(seconds=3)),
        ])
        assert inserted == 3

        result = await check_short_track_blind_spot(db_session)

        assert result.count == 1
        assert result.verdict == "INFO"
        assert any("excluded as structural blind spot: 1" in n for n in result.notes)
        assert any("covered days: 2" in n for n in result.notes)
        # 1 of 1, not 1 of 2: the unpairable play left the denominator.
        assert any("paired within 30s: 1 of 1" in n for n in result.notes)

    async def test_a_scrobbled_short_track_is_flagged_not_absorbed(self, db_session):
        """If a sub-30s track ever pairs, the floor assumption is wrong."""
        interlude_id = await _seed_canonical_track(
            db_session,
            connector_id="TEST_short_002",
            title=_TITLE,
            duration_ms=_INTERLUDE_MS,
        )
        played_at = datetime(2026, 7, 21, 9, 0, 0, tzinfo=UTC)
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        _ = await repo.bulk_insert_connector_plays([
            _export_play(
                connector_id="TEST_short_002",
                title=_TITLE,
                played_at=played_at,
                ms_played=_INTERLUDE_MS,
                resolved_track_id=interlude_id,
            ),
            _scrobble(played_at=played_at),
        ])

        result = await check_short_track_blind_spot(db_session)

        assert result.count == 1
        assert result.verdict == "ANOMALY"
        assert any("ANOMALY" in n for n in result.notes)


class TestDiscardPlausibility:
    async def test_reports_no_data_on_an_empty_ledger(self, db_session):
        result = await check_discard_plausibility(db_session)

        assert result.verdict == "NO DATA"
        assert result.count == 0

    async def test_clicked_through_discards_read_as_one_burst(self, db_session):
        _ = await _seed_canonical_track(
            db_session,
            connector_id="TEST_full_002",
            title=_TITLE,
            duration_ms=_FULL_LENGTH_MS,
        )
        first_at = datetime(2026, 7, 22, 20, 0, 0, tzinfo=UTC)
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        _ = await repo.bulk_insert_connector_plays([
            _export_play(
                connector_id="TEST_full_002",
                title=_TITLE,
                played_at=first_at + timedelta(seconds=30 * step),
                ms_played=_INTERLUDE_MS,
            )
            for step in range(3)
        ])

        result = await check_discard_plausibility(db_session)

        assert result.count == 3
        assert any("over 1 active days" in n for n in result.notes)
        assert any("per active day: median 3" in n for n in result.notes)
        # Both gaps are 30s — the whole run is one sitting, not spread listening.
        assert any("100.0% of discards land within 60s" in n for n in result.notes)
        assert any("clickrow: 3 (100.0%" in n for n in result.notes)

    async def test_incognito_discards_are_not_threshold_discards(self, db_session):
        """A private session is out regardless of length — it says nothing here."""
        _ = await _seed_canonical_track(
            db_session,
            connector_id="TEST_full_003",
            title=_TITLE,
            duration_ms=_FULL_LENGTH_MS,
        )
        played_at = datetime(2026, 7, 23, 20, 0, 0, tzinfo=UTC)
        repo = get_unit_of_work(db_session).get_connector_play_repository()
        _ = await repo.bulk_insert_connector_plays([
            _export_play(
                connector_id="TEST_full_003",
                title=_TITLE,
                played_at=played_at,
                ms_played=_INTERLUDE_MS,
                incognito=True,
            )
        ])

        result = await check_discard_plausibility(db_session)

        assert result.verdict == "NO DATA"
        assert result.count == 0


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
        # Median inside the agreement band is what a correctly-stamped channel
        # looks like. Calibration settled the semantics as END (2026-08-09), so
        # this now reads as "the correction is landing", not as evidence the
        # channel was start-stamped all along.
        assert any("start-aligned" in note for note in result.notes)

    async def test_unpaired_api_play_is_counted_not_dropped(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_play_repository()
        _ = await repo.bulk_insert_connector_plays([_api_play()])

        result = await check_spotify_api_lastfm_delta(db_session)

        assert result.count == 0
        assert any("unpaired: 1" in note for note in result.notes)
