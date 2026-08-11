"""Tests for the deterministic play-ledger projection.

Conventional cases pin the grouping/survivorship rules (findings §3-§7);
the hypothesis properties state the convergence guarantee itself — any
permutation of the same observation set projects to identical canonical
plays — as executable laws. These are the repo's first property-based tests.
"""

from datetime import UTC, datetime, timedelta
import random
from uuid import UUID

from hypothesis import given, settings, strategies as st
import pytest

from src.domain.entities import ConnectorTrackPlay
from src.domain.matching.play_projection import (
    MAX_NORMALIZED_START_SHIFT,
    START_SHIFT_MS_KEY,
    UnknownChannelError,
    channel_for,
    group_ledger_entries,
    merge_group,
    normalized_start_time,
    project_ledger_entries,
)

_BASE = datetime(2024, 11, 5, 9, 0, 0, tzinfo=UTC)
_TRACK_A = UUID("00000000-0000-7000-8000-00000000000a")
_TRACK_B = UUID("00000000-0000-7000-8000-00000000000b")


def _lastfm_obs(
    *,
    played_at: datetime,
    track_id: UUID | None = _TRACK_A,
    artist: str = "Carwash",
    title: str = "Striptease",
    loved: bool = False,
) -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="lastfm",
        artist_name=artist,
        track_name=title,
        played_at=played_at,
        ms_played=None,
        service_metadata={"loved": loved, "lastfm_track_url": "https://last.fm/x"},
        resolved_track_id=track_id,
        import_source="lastfm_api",
        import_batch_id="batch-lastfm",
    )


def _export_obs(
    *,
    ended_at: datetime,
    ms_played: int | None = 201_000,
    track_id: UUID | None = _TRACK_A,
    artist: str = "Carwash",
    title: str = "Striptease",
) -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="spotify",
        artist_name=artist,
        track_name=title,
        played_at=ended_at,
        ms_played=ms_played,
        service_metadata={
            "track_uri": "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
            "platform": "ios",
            "skipped": False,
        },
        resolved_track_id=track_id,
        import_source="spotify_export",
        import_batch_id="batch-export",
    )


class TestChannelRegistry:
    def test_known_channels_resolve(self):
        assert channel_for(_lastfm_obs(played_at=_BASE)).name == "lastfm"
        assert channel_for(_export_obs(ended_at=_BASE)).name == "spotify_export"

    def test_unknown_channel_fails_loud(self):
        stray = ConnectorTrackPlay(
            service="tidal",
            artist_name="A",
            track_name="B",
            played_at=_BASE,
            resolved_track_id=_TRACK_A,
            import_source="tidal_api",
        )
        with pytest.raises(UnknownChannelError, match="tidal"):
            channel_for(stray)

    def test_normalized_start_subtracts_ms_for_end_time_channels(self):
        export = _export_obs(ended_at=_BASE, ms_played=201_000)
        assert normalized_start_time(export, channel_for(export)) == _BASE - timedelta(
            milliseconds=201_000
        )
        scrobble = _lastfm_obs(played_at=_BASE)
        assert normalized_start_time(scrobble, channel_for(scrobble)) == _BASE

    def test_normalized_start_shift_clamps_to_fetch_margin(self):
        # A multi-hour ms_played (8h sleep/ambient track) must not shift the
        # start beyond the projection fetch margin, or the group would be
        # owned by no chunk and silently never projected.
        marathon = _export_obs(ended_at=_BASE, ms_played=8 * 60 * 60 * 1000)
        assert (
            normalized_start_time(marathon, channel_for(marathon))
            == _BASE - MAX_NORMALIZED_START_SHIFT
        )


class TestCrossChannelGrouping:
    def test_export_and_scrobble_of_same_listen_merge(self):
        start = _BASE
        scrobble = _lastfm_obs(played_at=start + timedelta(seconds=2))
        export = _export_obs(ended_at=start + timedelta(milliseconds=201_000))

        groups, stats = group_ledger_entries([scrobble, export])

        assert len(groups) == 1
        assert stats["resolution_divergence"] == 0
        merged = merge_group(groups[0])
        # Survivorship: export wins identity/provenance; lastfm (higher
        # timestamp_quality — true start) wins played_at; ms from export.
        assert merged.service == "spotify"
        assert merged.import_source == "spotify_export"
        assert merged.played_at == scrobble.played_at
        assert merged.ms_played == 201_000
        assert merged.source_services == ("spotify", "lastfm")
        assert merged.context is not None
        assert merged.context["platform"] == "ios"
        assert merged.context["merged_from_lastfm"]["loved"] is False

    def test_same_channel_skips_twenty_seconds_apart_never_merge(self):
        first = _lastfm_obs(played_at=_BASE)
        second = _lastfm_obs(played_at=_BASE + timedelta(seconds=20))

        groups, _ = group_ledger_entries([first, second])
        assert len(groups) == 2

    def test_one_to_one_nearest_assignment_keeps_skip_restarts_distinct(self):
        # Two scrobbles 25s apart, two exports whose normalized starts land
        # 1s after each scrobble — nearest-first one-to-one pairs them
        # (s1,e1) and (s2,e2), never both scrobbles onto one export.
        s1 = _lastfm_obs(played_at=_BASE)
        s2 = _lastfm_obs(played_at=_BASE + timedelta(seconds=25))
        e1 = _export_obs(ended_at=_BASE + timedelta(seconds=21), ms_played=20_000)
        e2 = _export_obs(ended_at=_BASE + timedelta(seconds=46), ms_played=20_000)

        groups, _ = group_ledger_entries([s1, s2, e1, e2])

        assert len(groups) == 2
        pairs = {frozenset(m.id for m in g.members) for g in groups}
        assert pairs == {frozenset({s1.id, e1.id}), frozenset({s2.id, e2.id})}

    def test_same_track_outside_tight_tolerance_stays_distinct(self):
        # ms_played present on the end-time member → the tight 30s tolerance
        # applies; normalized starts 60s apart must remain two events (a
        # restart after a minute is a second listen, not the same one).
        scrobble = _lastfm_obs(played_at=_BASE)
        export = _export_obs(
            ended_at=_BASE + timedelta(seconds=60) + timedelta(milliseconds=201_000),
            ms_played=201_000,
        )

        groups, _ = group_ledger_entries([scrobble, export])
        assert len(groups) == 2

    def test_fallback_tolerance_when_end_time_channel_lacks_ms(self):
        scrobble = _lastfm_obs(played_at=_BASE)
        # No ms_played: raw end time stands, 100s off — inside the 180s
        # fallback window, outside the 30s tight one.
        export = _export_obs(ended_at=_BASE + timedelta(seconds=100), ms_played=None)

        groups, _ = group_ledger_entries([scrobble, export])
        assert len(groups) == 1
        assert merge_group(groups[0]).ms_played is None


class TestSameChannelCollapse:
    def test_identical_ts_identifier_differing_ms_collapse_to_max(self):
        ended = _BASE
        keep = _export_obs(ended_at=ended, ms_played=201_000)
        drop = _export_obs(ended_at=ended, ms_played=150_000)

        result = project_ledger_entries([keep, drop])

        assert result.stats["same_channel_collapsed"] == 1
        assert len(result.plays) == 1
        play = result.plays[0]
        assert play.ms_played == 201_000
        # The absorbed sibling still contributes ledger membership.
        assert set(play.member_ids) == {keep.id, drop.id}

    @pytest.mark.parametrize("jitter_seconds", [1, 2, 3])
    def test_jittered_export_twin_with_identical_ms_collapses(
        self, jitter_seconds: int
    ):
        """C2: Spotify's export writes one play twice, a second or two apart.

        Byte-identical ms_played on the same track is the tell — two genuine
        plays cannot end that close together and have listened for exactly the
        same number of milliseconds.
        """
        first = _export_obs(ended_at=_BASE, ms_played=216_468)
        twin = _export_obs(
            ended_at=_BASE + timedelta(seconds=jitter_seconds), ms_played=216_468
        )

        result = project_ledger_entries([first, twin])

        assert result.stats["same_channel_collapsed"] == 1
        assert len(result.plays) == 1
        play = result.plays[0]
        assert play.ms_played == 216_468
        # The earliest stamp represents, so the canonical start does not drift.
        assert play.played_at == _BASE - timedelta(milliseconds=216_468)
        assert set(play.member_ids) == {first.id, twin.id}

    def test_repeat_play_of_the_same_track_stays_two_plays(self):
        """The bound that makes the jitter rule safe: two real plays run back
        to back, so their end stamps sit a whole listened duration apart."""
        first = _export_obs(ended_at=_BASE, ms_played=201_000)
        again = _export_obs(
            ended_at=_BASE + timedelta(milliseconds=201_000), ms_played=201_000
        )

        result = project_ledger_entries([first, again])

        assert result.stats["same_channel_collapsed"] == 0
        assert len(result.plays) == 2

    def test_differing_ms_played_seconds_apart_stays_two_plays(self):
        """Segments of an interrupted listen differ in ms_played (C9) — the
        duplicate rule must not swallow them into one."""
        first = _export_obs(ended_at=_BASE, ms_played=60_000)
        second = _export_obs(ended_at=_BASE + timedelta(seconds=2), ms_played=61_000)

        result = project_ledger_entries([first, second])

        assert result.stats["same_channel_collapsed"] == 0
        assert len(result.plays) == 2

    def test_tiny_plays_a_second_apart_stay_distinct(self):
        """A skip storm: identical sub-second ms_played, one second apart.

        The window is bounded by ms_played itself, so plays shorter than the
        jitter band are only ever collapsed on an exact instant.
        """
        first = _export_obs(ended_at=_BASE, ms_played=900)
        second = _export_obs(ended_at=_BASE + timedelta(seconds=1), ms_played=900)

        result = project_ledger_entries([first, second])

        assert result.stats["same_channel_collapsed"] == 0
        assert len(result.plays) == 2

    def test_jittered_collapse_is_permutation_invariant(self):
        """Three copies of one play, arriving in any order, are one play."""
        copies = [
            _export_obs(ended_at=_BASE + timedelta(seconds=offset), ms_played=216_468)
            for offset in (0, 1, 2)
        ]

        for permutation in ([2, 0, 1], [1, 2, 0], [0, 1, 2]):
            result = project_ledger_entries([copies[i] for i in permutation])
            assert result.stats["same_channel_collapsed"] == 2
            assert len(result.plays) == 1
            assert set(result.plays[0].member_ids) == {c.id for c in copies}

    def test_a_scrobble_pairs_with_the_surviving_representative(self):
        """The collapse happens before pairing, so the duplicate cannot steal
        the cross-channel match and leave a second canonical play behind."""
        ended = _BASE + timedelta(milliseconds=201_000)
        first = _export_obs(ended_at=ended, ms_played=201_000)
        twin = _export_obs(ended_at=ended + timedelta(seconds=1), ms_played=201_000)
        scrobble = _lastfm_obs(played_at=_BASE + timedelta(seconds=2))

        result = project_ledger_entries([first, twin, scrobble])

        assert len(result.plays) == 1
        assert set(result.plays[0].member_ids) == {first.id, twin.id, scrobble.id}


class TestResolutionDivergenceBridge:
    def test_bridge_unions_equal_normalized_identity_across_track_ids(self):
        scrobble = _lastfm_obs(
            played_at=_BASE, track_id=_TRACK_B, artist="carwash", title="striptease"
        )
        export = _export_obs(
            ended_at=_BASE + timedelta(milliseconds=201_000), track_id=_TRACK_A
        )

        result = project_ledger_entries([scrobble, export])

        assert result.stats["resolution_divergence"] == 1
        assert len(result.plays) == 1
        play = result.plays[0]
        assert play.divergent
        # Winner (export, priority 0) supplies the canonical track id.
        assert play.track_id == _TRACK_A

    def test_bridge_requires_exact_normalized_equality(self):
        scrobble = _lastfm_obs(
            played_at=_BASE, track_id=_TRACK_B, title="Striptease (Live)"
        )
        export = _export_obs(
            ended_at=_BASE + timedelta(milliseconds=201_000), track_id=_TRACK_A
        )

        groups, stats = group_ledger_entries([scrobble, export])
        assert len(groups) == 2
        assert stats["resolution_divergence"] == 0


class TestMergeDeterminism:
    def test_winner_tie_breaks_on_lowest_id(self):
        a = _lastfm_obs(played_at=_BASE)
        b = _lastfm_obs(played_at=_BASE + timedelta(seconds=40))
        groups, _ = group_ledger_entries([a, b])
        winners = sorted(g.members[0].id for g in groups)
        assert winners == sorted([a.id, b.id])


# --------------------------------------------------------------------------- #
# Convergence laws (hypothesis).                                              #
# --------------------------------------------------------------------------- #


@st.composite
def _observation_sets(draw: st.DrawFn) -> tuple[list[ConnectorTrackPlay], int]:
    """Listening events observed by a random subset of channels with jitter.

    Events are spaced far enough apart (≥ 8 min > 2x fallback tolerance) that
    the ground truth is unambiguous; within an event, observations jitter
    ±5s around the true start (findings §3's alignment band). An export
    observation may also arrive twice, a second or two apart with identical
    ms_played — the upstream duplicate class (v0.10.3 C2).

    Returns the observations paired with the number of events they describe.
    """
    entries: list[ConnectorTrackPlay] = []
    event_count = draw(st.integers(min_value=1, max_value=6))
    for event_index in range(event_count):
        start = _BASE + timedelta(seconds=event_index * 480)
        track_id = UUID(int=(0x8000 | event_index), version=7)
        channels = draw(
            st.sets(
                st.sampled_from(["lastfm", "spotify_export", "spotify_api"]),
                min_size=1,
                max_size=3,
            )
        )
        jitter = draw(st.integers(min_value=-5, max_value=5))
        ms_played = draw(st.integers(min_value=30_000, max_value=300_000))
        duplicate_jitter = draw(st.integers(min_value=0, max_value=3))
        if "lastfm" in channels:
            entries.append(
                _lastfm_obs(
                    played_at=start + timedelta(seconds=jitter),
                    track_id=track_id,
                    title=f"Song {event_index}",
                )
            )
        if "spotify_export" in channels:
            ended_at = start + timedelta(milliseconds=ms_played)
            entries.append(
                _export_obs(
                    ended_at=ended_at,
                    ms_played=ms_played,
                    track_id=track_id,
                    title=f"Song {event_index}",
                )
            )
            if duplicate_jitter:
                entries.append(
                    _export_obs(
                        ended_at=ended_at + timedelta(seconds=duplicate_jitter),
                        ms_played=ms_played,
                        track_id=track_id,
                        title=f"Song {event_index}",
                    )
                )
        if "spotify_api" in channels:
            entries.append(
                _api_obs(
                    ended_at=start + timedelta(milliseconds=ms_played),
                    start_shift_ms=ms_played,
                    track_id=track_id,
                    title=f"Song {event_index}",
                )
            )
    return entries, event_count


class TestConvergenceLaws:
    @settings(max_examples=60, deadline=None)
    @given(observations=_observation_sets(), seed=st.integers())
    def test_permutation_invariance(
        self, observations: tuple[list[ConnectorTrackPlay], int], seed: int
    ):
        """Any arrival order of the same observation set → identical plays."""
        entries, _ = observations
        shuffled = list(entries)
        random.Random(seed).shuffle(shuffled)

        assert (
            project_ledger_entries(shuffled).plays
            == project_ledger_entries(entries).plays
        )

    @settings(max_examples=60, deadline=None)
    @given(observations=_observation_sets())
    def test_projection_is_deterministic(
        self, observations: tuple[list[ConnectorTrackPlay], int]
    ):
        entries, _ = observations
        first = project_ledger_entries(entries)
        second = project_ledger_entries(entries)
        assert first.plays == second.plays
        assert first.stats == second.stats

    @settings(max_examples=60, deadline=None)
    @given(observations=_observation_sets())
    def test_groups_never_mix_same_channel_observations(
        self, observations: tuple[list[ConnectorTrackPlay], int]
    ):
        """One observation per channel per event — skip-restarts stay distinct."""
        entries, _ = observations
        groups, _stats = group_ledger_entries(entries)
        for group in groups:
            names = [channel_for(member).name for member in group.members]
            assert len(names) == len(set(names))

    @settings(max_examples=60, deadline=None)
    @given(observations=_observation_sets())
    def test_one_canonical_play_per_listening_event(
        self, observations: tuple[list[ConnectorTrackPlay], int]
    ):
        """However many channels saw it, and however often a channel wrote it
        down, one listen is one play."""
        entries, event_count = observations
        assert len(project_ledger_entries(entries).plays) == event_count

    @settings(max_examples=60, deadline=None)
    @given(observations=_observation_sets())
    def test_every_observation_lands_in_exactly_one_group(
        self, observations: tuple[list[ConnectorTrackPlay], int]
    ):
        entries, _ = observations
        groups, _stats = group_ledger_entries(entries)
        covered = [
            pid
            for g in groups
            for pid in (*(m.id for m in g.members), *(a.id for a in g.absorbed))
        ]
        assert sorted(covered) == sorted(e.id for e in entries)


def _api_obs(
    *,
    ended_at: datetime,
    track_id: UUID | None = _TRACK_A,
    artist: str = "Carwash",
    title: str = "Striptease",
    start_shift_ms: int | None = 201_000,
) -> ConnectorTrackPlay:
    """A spotify_api observation — strong identity, no ms_played (v0.10.1).

    ``played_at`` stamps the END of the play (v0.10.3 C1). The importer stamps
    the derived END→START shift only for plays it can show ran in full;
    ``start_shift_ms=None`` is the other case (a truncated play, or a row
    written before calibration).
    """
    shift = {START_SHIFT_MS_KEY: start_shift_ms} if start_shift_ms is not None else {}
    return ConnectorTrackPlay(
        service="spotify",
        artist_name=artist,
        track_name=title,
        played_at=ended_at,
        ms_played=None,
        service_metadata={
            "track_uri": "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
            "duration_ms": 201_000,
            "context_type": "playlist",
            "context_uri": "spotify:playlist:p1",
            **shift,
        },
        resolved_track_id=track_id,
        import_source="spotify_api",
        import_batch_id="batch-api",
    )


class TestSpotifyApiChannel:
    """v0.10.1's acceptance of v0.10.0's zero-new-dedup-code claim.

    The recently-played channel was registered in v0.10.0 and never fed. These
    pin that feeding it needs no merge code of its own.
    """

    def test_one_listen_observed_by_all_three_channels_is_one_play(self):
        """Export + API + Last.fm of the same listen collapse to a single play.

        This is the user-visible promise: connecting a live poller and later
        uploading a GDPR export deepens the play, it does not triple-count it.
        """
        start = _BASE
        ended = start + timedelta(milliseconds=201_000)
        entries = [
            _export_obs(ended_at=ended),
            _api_obs(ended_at=ended),
            _lastfm_obs(played_at=start + timedelta(seconds=3)),
        ]

        groups, _stats = group_ledger_entries(entries)

        assert len(groups) == 1
        merged = merge_group(groups[0])
        assert set(merged.source_services) == {"spotify", "lastfm"}
        # The export wins ms_played (it is the only channel that observes it);
        # the API contributes identity, Last.fm the true start.
        assert merged.ms_played == 201_000
        assert "merged_from_spotify_api" in merged.context
        assert merged.context["merged_from_spotify_api"]["context_type"] == "playlist"

    def test_stamped_shift_normalizes_the_end_stamp_back_to_the_start(self):
        """C1: the correction is ledger data, read through the existing seam."""
        ended = _BASE + timedelta(milliseconds=201_000)
        api = _api_obs(ended_at=ended)

        assert normalized_start_time(api, channel_for(api)) == _BASE

    def test_api_play_alone_projects_without_ms_played(self):
        """Before any export arrives, the API observation stands on its own."""
        ended = _BASE + timedelta(milliseconds=201_000)
        groups, _stats = group_ledger_entries([_api_obs(ended_at=ended)])

        merged = merge_group(groups[0])
        assert merged.played_at == _BASE
        assert merged.ms_played is None
        assert merged.source_services == ("spotify",)

    def test_unstamped_api_play_keeps_its_raw_timestamp(self):
        """A play the importer could not show ran in full is left uncorrected.

        Shifting it by the track's length would move it further from the truth
        than leaving it alone; the wide pairing tolerance covers the residual.
        """
        api = _api_obs(ended_at=_BASE, start_shift_ms=None)

        assert normalized_start_time(api, channel_for(api)) == _BASE
        assert merge_group(group_ledger_entries([api])[0][0]).played_at == _BASE

    def test_reprojecting_an_api_poll_overlap_is_idempotent(self):
        """Re-polling the boundary play must not create a second canonical play."""
        entries = [_api_obs(ended_at=_BASE)]

        first, _ = group_ledger_entries(entries)
        second, _ = group_ledger_entries([*entries])

        assert merge_group(first[0]) == merge_group(second[0])


class TestSpotifyApiToleranceBoundary:
    """The calibrated channel's pairing window (v0.10.3 C1).

    With the END→START shift on the ledger the channel pairs at the tight 30s
    tolerance like any other normalizable observation; only an observation that
    could not be normalized still falls back to the wide window.
    """

    def test_normalized_api_play_pairs_with_a_scrobble_seconds_away(self):
        ended = _BASE + timedelta(milliseconds=201_000)
        entries = [
            _api_obs(ended_at=ended),
            _lastfm_obs(played_at=_BASE + timedelta(seconds=4)),
        ]

        groups, _stats = group_ledger_entries(entries)

        assert len(groups) == 1

    @pytest.mark.parametrize("gap_seconds", [31, 90, 179])
    def test_gap_beyond_the_tight_tolerance_stays_two_plays(self, gap_seconds: int):
        """The 31-179s band: inside the old fallback window, outside the real one.

        Before calibration these merged on a 180s override — a scrobble up to
        three minutes from an API play was assumed to be the same listen. Now
        they are what they look like: two listens.
        """
        ended = _BASE + timedelta(milliseconds=201_000)
        entries = [
            _api_obs(ended_at=ended),
            _lastfm_obs(played_at=_BASE + timedelta(seconds=gap_seconds)),
        ]

        groups, _stats = group_ledger_entries(entries)

        assert len(groups) == 2

    def test_unnormalizable_api_play_still_pairs_at_the_wide_tolerance(self):
        """Legacy and truncated rows keep the pre-calibration behaviour."""
        entries = [
            _api_obs(ended_at=_BASE, start_shift_ms=None),
            _lastfm_obs(played_at=_BASE + timedelta(seconds=90)),
        ]

        groups, _stats = group_ledger_entries(entries)

        assert len(groups) == 1
