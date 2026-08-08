"""Unit tests for PlayProjectionService diff-apply claim tracking and chunking.

Pins the chunk-scoped claim registry: two groups may never materialize the
same dedup tuple (a conflict-skipped insert would leave membership edges
referencing a phantom id — an FK violation), a canonical play claimed by one
group is not reusable by a later group (the 1→N split arm), and legacy
end-time rows are healed in place by the shifted adoption probe.

Also pins which days a batch chunks: only the ones its plays fall in (or near),
never the whole span between its oldest and newest play.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from itertools import pairwise
from random import Random
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from src.application.services.play_projection_service import (
    MAX_ANCHOR_PULL_BACK,
    PROJECTION_FETCH_MARGIN,
    PlayProjectionService,
    _contiguous_chunk_cores,
    observed_day_cores,
)
from src.domain.entities import ConnectorTrackPlay, PlaySource, TrackPlay
from src.domain.matching.play_projection import MAX_NORMALIZED_START_SHIFT
from tests.fixtures.mocks import make_mock_uow

_BASE = datetime(2024, 11, 5, 9, 0, 0, tzinfo=UTC)
_TRACK = UUID("00000000-0000-7000-8000-00000000000a")
_USER = "default"


def _scrobble(
    *,
    played_at: datetime,
    title: str = "Striptease",
) -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="lastfm",
        artist_name="Carwash",
        track_name=title,
        played_at=played_at,
        ms_played=None,
        service_metadata={"loved": False},
        resolved_track_id=_TRACK,
        import_source="lastfm_api",
        import_batch_id="batch-lastfm",
        user_id=_USER,
    )


def _export(
    *,
    ended_at: datetime,
    ms_played: int = 201_000,
) -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="spotify",
        artist_name="Carwash",
        track_name="Striptease",
        played_at=ended_at,
        ms_played=ms_played,
        service_metadata={"track_uri": "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"},
        resolved_track_id=_TRACK,
        import_source="spotify_export",
        import_batch_id="batch-export",
        user_id=_USER,
    )


def _wire_uow(
    *,
    entries: list[ConnectorTrackPlay],
    sources: list[PlaySource] | None = None,
    plays: list[TrackPlay] | None = None,
    adoptable: list[TrackPlay] | None = None,
):
    connector_repo = AsyncMock()
    connector_repo.find_resolved_in_window.return_value = entries
    plays_repo = AsyncMock()
    plays_repo.get_play_sources_for_connector_plays.return_value = sources or []
    plays_repo.get_plays_by_ids.return_value = plays or []
    plays_repo.find_plays_in_window.return_value = adoptable or []
    plays_repo.get_play_sources_for_plays.return_value = []
    plays_repo.bulk_insert_plays.return_value = (1, 0)
    plays_repo.delete_plays_without_sources.return_value = 0
    uow = make_mock_uow(connector_play_repo=connector_repo, plays_repo=plays_repo)
    return uow, plays_repo


def _wire_windowed_uow(entries: list[ConnectorTrackPlay]):
    """Like :func:`_wire_uow`, but the ledger read honours the asked-for window.

    The fixed-return wiring cannot see an ownership bug: it hands every chunk
    every entry, so a chunk that should never have been generated still finds
    the group and applies it. Filtering on raw ``played_at`` is what makes a
    missing core show up as a missing projection.
    """
    connector_repo = AsyncMock()

    async def _find_resolved_in_window(
        start: datetime, end: datetime, *, user_id: str
    ) -> list[ConnectorTrackPlay]:
        _ = user_id
        return [entry for entry in entries if start <= entry.played_at < end]

    connector_repo.find_resolved_in_window.side_effect = _find_resolved_in_window
    plays_repo = AsyncMock()
    plays_repo.get_play_sources_for_connector_plays.return_value = []
    plays_repo.get_plays_by_ids.return_value = []
    plays_repo.find_plays_in_window.return_value = []
    plays_repo.get_play_sources_for_plays.return_value = []
    plays_repo.bulk_insert_plays.return_value = (1, 0)
    plays_repo.delete_plays_without_sources.return_value = 0
    uow = make_mock_uow(connector_play_repo=connector_repo, plays_repo=plays_repo)
    return uow, plays_repo


async def _project_day(service: PlayProjectionService, uow) -> dict[str, int]:
    return await service.project_range(
        uow,
        user_id=_USER,
        start=_BASE - timedelta(hours=6),
        end=_BASE + timedelta(hours=18),
    )


class TestIdenticalTupleClaim:
    @pytest.mark.asyncio
    async def test_two_groups_with_identical_tuples_share_one_insert(self):
        # Same channel, same played_at, different identifiers (casing twins),
        # same resolved track: two groups whose projections carry identical
        # (track, service, played_at, ms) — only ONE row may be inserted, and
        # both membership edges must reference it (not a phantom id).
        twin_a = _scrobble(played_at=_BASE, title="Striptease")
        twin_b = _scrobble(played_at=_BASE, title="striptease")
        uow, plays_repo = _wire_uow(entries=[twin_a, twin_b])

        stats = await PlayProjectionService().project_range(
            uow,
            user_id=_USER,
            start=_BASE - timedelta(hours=6),
            end=_BASE + timedelta(hours=18),
        )

        inserted = plays_repo.bulk_insert_plays.await_args.args[0]
        assert len(inserted) == 1
        memberships = plays_repo.bulk_upsert_play_sources.await_args.args[0]
        assert {m.connector_play_id for m in memberships} == {twin_a.id, twin_b.id}
        assert {m.track_play_id for m in memberships} == {inserted[0].id}
        assert stats["groups_created"] == 1
        assert stats["groups_unchanged"] == 1


class TestSplitClaim:
    @pytest.mark.asyncio
    async def test_second_group_linked_to_claimed_play_gets_its_own_row(self):
        # Two distinct listening events whose observations both currently
        # back ONE canonical play (a previous over-merge): the first group
        # keeps the row, the second must create its own instead of silently
        # overwriting — otherwise one listen vanishes forever.
        first = _scrobble(played_at=_BASE)
        second = _scrobble(played_at=_BASE + timedelta(seconds=600))
        shared = TrackPlay(
            track_id=_TRACK,
            service="lastfm",
            played_at=_BASE,
            user_id=_USER,
            ms_played=None,
            import_source="lastfm_api",
        )
        sources = [
            PlaySource(
                user_id=_USER, track_play_id=shared.id, connector_play_id=first.id
            ),
            PlaySource(
                user_id=_USER, track_play_id=shared.id, connector_play_id=second.id
            ),
        ]
        uow, plays_repo = _wire_uow(
            entries=[first, second], sources=sources, plays=[shared]
        )

        stats = await _project_day(PlayProjectionService(), uow)

        inserted = plays_repo.bulk_insert_plays.await_args.args[0]
        assert len(inserted) == 1
        assert inserted[0].played_at == second.played_at
        memberships = plays_repo.bulk_upsert_play_sources.await_args.args[0]
        assert {(m.connector_play_id, m.track_play_id) for m in memberships} == {
            (second.id, inserted[0].id)
        }
        assert stats["groups_created"] == 1


class TestLegacyEndTimeAdoption:
    @pytest.mark.asyncio
    async def test_reimport_heals_legacy_end_stamped_row_in_place(self):
        # Pre-v0.10 canonical Spotify rows store the raw END timestamp; the
        # projection emits the normalized START. The shifted adoption probe
        # must claim the legacy row instead of inserting a duplicate.
        export = _export(ended_at=_BASE, ms_played=201_000)
        legacy = TrackPlay(
            track_id=_TRACK,
            service="spotify",
            played_at=_BASE,  # end-stamped, as the old pipeline wrote it
            user_id=_USER,
            ms_played=201_000,
            import_source="spotify_export",
        )
        uow, plays_repo = _wire_uow(entries=[export], adoptable=[legacy])

        stats = await _project_day(PlayProjectionService(), uow)

        plays_repo.bulk_insert_plays.assert_not_awaited()
        updates = plays_repo.bulk_update_plays.await_args.args[0]
        assert [pid for pid, _ in updates] == [legacy.id]
        assert updates[0][1]["played_at"] == _BASE - timedelta(milliseconds=201_000)
        assert stats["groups_updated"] == 1
        assert stats["groups_created"] == 0


class TestObservedDayCores:
    """Which calendar days a batch's plays put in scope.

    A day-chunk costs several round trips whether or not it holds anything, so
    the cores must follow the observations, not the span they straddle.
    """

    def test_sparse_dates_chunk_only_their_own_days(self):
        # One stray 2011 scrobble in an otherwise recent import: the 14 years
        # in between hold nothing this batch touched.
        cores = observed_day_cores([
            datetime(2011, 3, 5, 14, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
        ])

        assert [start.date() for start, _ in cores] == [
            date(2011, 3, 5),
            date(2026, 7, 20),
        ]

    def test_the_span_between_sparse_dates_is_thousands_of_chunks(self):
        """What tiling the span (the pre-fix orchestrator) actually asked for."""
        span = _contiguous_chunk_cores(
            datetime(2011, 3, 5, 14, 0, tzinfo=UTC) - PROJECTION_FETCH_MARGIN,
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC) + PROJECTION_FETCH_MARGIN,
        )

        assert len(span) > 5_000

    def test_contiguous_dates_still_tile_every_day(self):
        cores = observed_day_cores([
            datetime(2024, 1, day, 12, 0, tzinfo=UTC) for day in range(1, 6)
        ])

        assert [start.date() for start, _ in cores] == [
            date(2024, 1, day) for day in range(1, 6)
        ]
        assert all(end - start == timedelta(days=1) for start, end in cores)

    def test_repeated_plays_in_a_day_share_one_chunk(self):
        cores = observed_day_cores([
            datetime(2024, 1, 9, hour, 0, tzinfo=UTC) for hour in (7, 12, 23)
        ])

        assert [start.date() for start, _ in cores] == [date(2024, 1, 9)]

    def test_play_inside_the_margin_of_midnight_also_claims_the_day_before(self):
        """A 00:30 play's group can be anchored the previous evening: its
        normalized start shifts back by up to the fetch margin."""
        cores = observed_day_cores([datetime(2024, 3, 10, 0, 30, tzinfo=UTC)])

        assert [start.date() for start, _ in cores] == [
            date(2024, 3, 9),
            date(2024, 3, 10),
        ]

    def test_a_play_just_past_the_margin_still_claims_the_day_before(self):
        """The exact off-by-tolerance loss: normalization alone is not the reach.

        A GDPR row at 06:00:10 with a clamped six-hour ``ms_played`` normalizes
        to 00:00:10 — inside the day — but a stored scrobble 20s earlier holds
        the group's anchor on the previous day. Generating only the 10th's core
        leaves that group owned by nobody.
        """
        cores = observed_day_cores([datetime(2024, 3, 10, 6, 0, 10, tzinfo=UTC)])

        assert [start.date() for start, _ in cores] == [
            date(2024, 3, 9),
            date(2024, 3, 10),
        ]

    def test_play_past_the_whole_reach_back_stays_one_day(self):
        """Beyond normalization AND the pairing pull-back, one day suffices."""
        cores = observed_day_cores([datetime(2024, 3, 10, 6, 30, tzinfo=UTC)])

        assert [start.date() for start, _ in cores] == [date(2024, 3, 10)]

    def test_non_utc_timestamp_generates_its_utc_core(self):
        """Cores are UTC midnights, so the date must be read in UTC.

        ``validate_timezone_aware`` accepts any offset and the GDPR parser
        preserves it: 2024-06-15T20:30-05:00 is the 16th in UTC, and reading
        the local date would leave the 16th's core ungenerated.
        """
        cores = observed_day_cores([
            datetime(2024, 6, 15, 20, 30, tzinfo=timezone(timedelta(hours=-5)))
        ])

        assert [start.date() for start, _ in cores] == [
            date(2024, 6, 15),
            date(2024, 6, 16),
        ]

    def test_every_reachable_anchor_lands_in_exactly_one_generated_core(self):
        """THE ownership invariant, swept across the clamp and midnight.

        For any observation, the anchor of the group it joins can sit anywhere
        from its own normalized start back to ``MAX_ANCHOR_PULL_BACK`` earlier.
        Every one of those anchors must be owned by exactly one of the cores
        the observation alone generates — "at least one" or the group silently
        never projects, "at most one" or two chunks apply it.
        """
        rng = Random(20241105)
        clamp_ms = int(MAX_NORMALIZED_START_SHIFT.total_seconds() * 1000)
        midnight = datetime(2024, 3, 10, tzinfo=UTC)

        for _ in range(500):
            # Straddle midnight and the clamp: the two boundaries whose
            # interaction produced the loss.
            raw = midnight + timedelta(seconds=rng.uniform(-3_600, 8 * 3_600))
            ms_played = clamp_ms + rng.randint(-60_000, 60_000)
            normalized = raw - min(
                timedelta(milliseconds=ms_played), MAX_NORMALIZED_START_SHIFT
            )
            anchor = normalized - MAX_ANCHOR_PULL_BACK * rng.random()

            owners = [
                core
                for core in observed_day_cores([raw])
                if core[0] <= anchor < core[1]
            ]

            assert len(owners) == 1, (
                f"raw={raw.isoformat()} ms_played={ms_played} "
                f"anchor={anchor.isoformat()} owned by {len(owners)} cores"
            )

    def test_cores_are_ordered_and_never_overlap(self):
        """Overlapping cores would let two chunks own — and apply — one group."""
        cores = observed_day_cores([
            datetime(2024, 3, 10, 0, 30, tzinfo=UTC),
            datetime(2024, 3, 9, 22, 0, tzinfo=UTC),
            datetime(2024, 3, 12, 5, 0, tzinfo=UTC),
        ])

        assert all(earlier[1] <= later[0] for earlier, later in pairwise(cores))

    def test_no_observations_means_no_chunks(self):
        assert observed_day_cores([]) == []


class TestAnchorPulledAcrossMidnight:
    """The constructed silent-loss scenario, projected end to end.

    A GDPR row whose normalized start lands just inside a day, unioned with an
    already-stored scrobble from just before midnight: the group anchors the
    PREVIOUS day. Both halves of the fix are exercised — the previous day's
    core has to be generated at all, and its fetch window has to reach far
    enough forward (past the plain six-hour margin) to see the GDPR row.
    """

    _MIDNIGHT = datetime(2024, 3, 10, tzinfo=UTC)
    _SIX_HOURS_MS = 6 * 60 * 60 * 1000

    @pytest.mark.asyncio
    async def test_the_pulled_group_is_owned_and_projected(self):
        neighbour = _scrobble(played_at=self._MIDNIGHT - timedelta(seconds=10))
        gdpr = _export(
            ended_at=self._MIDNIGHT + timedelta(hours=6, seconds=10),
            ms_played=self._SIX_HOURS_MS,
        )
        uow, plays_repo = _wire_windowed_uow([neighbour, gdpr])

        stats = await PlayProjectionService().project_observed_days(
            uow, user_id=_USER, played_at=[gdpr.played_at]
        )

        assert stats["groups_created"] == 1
        inserted = plays_repo.bulk_insert_plays.await_args.args[0]
        assert len(inserted) == 1
        # Last.fm outranks the export on timestamp quality, so the surviving
        # played_at is the scrobble's — the previous day, as the anchor says.
        assert inserted[0].played_at == neighbour.played_at
        memberships = plays_repo.bulk_upsert_play_sources.await_args.args[0]
        assert {m.connector_play_id for m in memberships} == {neighbour.id, gdpr.id}

    @pytest.mark.asyncio
    async def test_no_second_chunk_also_claims_it(self):
        """Ownership is exactly-one: the day the GDPR row falls in must not
        apply the group a second time (a duplicate canonical play)."""
        neighbour = _scrobble(played_at=self._MIDNIGHT - timedelta(seconds=10))
        gdpr = _export(
            ended_at=self._MIDNIGHT + timedelta(hours=6, seconds=10),
            ms_played=self._SIX_HOURS_MS,
        )
        uow, plays_repo = _wire_windowed_uow([neighbour, gdpr])

        stats = await PlayProjectionService().project_observed_days(
            uow, user_id=_USER, played_at=[gdpr.played_at]
        )

        # Two cores are visited (the 9th and the 10th) but only one owns.
        assert (
            uow.get_connector_play_repository().find_resolved_in_window.await_count == 2
        )
        assert plays_repo.bulk_insert_plays.await_count == 1
        assert stats["groups_created"] + stats["groups_updated"] == 1


class TestProjectObservedDays:
    """The batch entry point visits exactly those days, applying them as usual."""

    @pytest.mark.asyncio
    async def test_only_the_observed_days_are_fetched(self):
        uow, _ = _wire_uow(entries=[])

        _ = await PlayProjectionService().project_observed_days(
            uow,
            user_id=_USER,
            played_at=[
                datetime(2011, 3, 5, 14, 0, tzinfo=UTC),
                datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            ],
        )

        connector_repo = uow.get_connector_play_repository()
        assert connector_repo.find_resolved_in_window.await_count == 2

    @pytest.mark.asyncio
    async def test_diff_apply_matches_the_equivalent_range_projection(self):
        """Same day, same observation, same outcome — only the chunking differs."""
        scrobble = _scrobble(played_at=_BASE)
        uow, plays_repo = _wire_uow(entries=[scrobble])

        stats = await PlayProjectionService().project_observed_days(
            uow, user_id=_USER, played_at=[scrobble.played_at]
        )

        assert stats["groups_created"] == 1
        inserted = plays_repo.bulk_insert_plays.await_args.args[0]
        assert [play.played_at for play in inserted] == [_BASE]
