"""Classification and survivor rules of the duplicate-canonical report.

The script is a read-only prod report and is never executed here. What these
tests pin is the two things a reviewer acts on: which pairs are presented as
mergeable, and which side survives.

The survivor rule *differs between the two populations by design* — same-user
groups keep the newer ISRC release, cross-tenant pairs always keep the row the
target user owns — and a silent convergence of the two is the failure mode
worth a test. Merging a cross-tenant pair into the other tenant's track because
its ISRC looks newer would leave the user's history on a row they do not own,
which is the defect the merge exists to repair.
"""

from datetime import UTC, datetime
from uuid import uuid4

from scripts.find_duplicate_canonicals import (
    _COLLISION_LABELS,
    _Candidate,
    _CrossTenantPair,
    _Group,
    collisions_elsewhere,
    collisions_for,
    pick_survivor,
    same_recording,
)

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _candidate(
    *,
    isrc: str | None = None,
    duration_ms: int | None = 200_000,
    created_at: datetime = _EPOCH,
    title: str = "Michicant",
    artist: str = "Bon Iver",
    plays: int = 0,
) -> _Candidate:
    return _Candidate(
        track_id=uuid4(),
        title=title,
        artist=artist,
        primary_artist=artist,
        isrc=isrc,
        duration_ms=duration_ms,
        created_at=created_at,
        play_count=plays,
    )


class TestSameRecordingPredicate:
    def test_equal_titles_within_the_duration_tolerance_are_one_recording(self):
        assert same_recording(
            _candidate(duration_ms=200_000), _candidate(duration_ms=209_000)
        )

    def test_a_wider_duration_gap_is_two_recordings(self):
        """A live take and a radio edit share an artist and a title."""
        assert not same_recording(
            _candidate(duration_ms=200_000), _candidate(duration_ms=260_000)
        )

    def test_a_missing_duration_refuses_rather_than_assuming(self):
        assert not same_recording(
            _candidate(duration_ms=None), _candidate(duration_ms=200_000)
        )

    def test_only_the_primary_artist_is_compared(self):
        """Featured-artist billing differs between services for one recording.

        The display string carries every credit; the predicate must not refuse
        a pair over a difference of billing.
        """
        a = _Candidate(
            track_id=uuid4(),
            title="All I Need",
            artist="Vegyn",
            primary_artist="Vegyn",
            isrc=None,
            duration_ms=312_000,
            created_at=_EPOCH,
            play_count=1,
        )
        b = _Candidate(
            track_id=uuid4(),
            title="All I Need",
            artist="Vegyn, Air, Beth Hirsch",
            primary_artist="Vegyn",
            isrc=None,
            duration_ms=312_000,
            created_at=_EPOCH,
            play_count=1,
        )

        assert same_recording(a, b)

    def test_a_remix_is_not_its_original(self):
        """Normalized *equality*, not similarity — a parenthetical is a title."""
        assert not same_recording(
            _candidate(title="Ice Ice Baby", duration_ms=200_000),
            _candidate(title="Ice Ice Baby (Wunderbros Remix)", duration_ms=200_000),
        )


class TestGroupClassification:
    def test_a_duration_compatible_group_is_mergeable(self):
        group = _Group(
            members=[_candidate(duration_ms=200_000), _candidate(duration_ms=201_000)]
        )

        assert group.is_mergeable

    def test_one_incompatible_member_disqualifies_the_whole_group(self):
        """The group is merged as a unit, so every pair has to hold."""
        group = _Group(
            members=[
                _candidate(duration_ms=200_000),
                _candidate(duration_ms=201_000),
                _candidate(duration_ms=400_000),
            ]
        )

        assert not group.is_mergeable
        assert "different recordings" in group.skip_reason

    def test_a_missing_duration_is_skipped_with_its_own_reason(self):
        group = _Group(
            members=[_candidate(duration_ms=None), _candidate(duration_ms=200_000)]
        )

        assert not group.is_mergeable
        assert "duration is missing" in group.skip_reason


class TestSameUserSurvivor:
    def test_the_newer_isrc_release_year_wins(self):
        older = _candidate(isrc="FR71S9900008")
        newer = _candidate(isrc="FRU522500008")

        survivor, rule = pick_survivor(_Group(members=[older, newer]))

        assert survivor is newer
        assert rule == "isrc_year (2025)"

    def test_the_newer_isrc_wins_even_when_it_is_the_older_row(self):
        """Recency of the *release*, not of the row — the rule's whole point."""
        newer_isrc = _candidate(isrc="USA2B2056087", created_at=_EPOCH)
        older_isrc = _candidate(
            isrc="GBEXH1900012", created_at=datetime(2026, 6, 1, tzinfo=UTC)
        )

        survivor, _ = pick_survivor(_Group(members=[newer_isrc, older_isrc]))

        assert survivor is newer_isrc

    def test_a_tied_year_falls_back_to_created_at(self):
        old = _candidate(isrc="GBUM72305257", created_at=_EPOCH)
        new = _candidate(
            isrc="GBUM72305468", created_at=datetime(2026, 6, 1, tzinfo=UTC)
        )

        survivor, rule = pick_survivor(_Group(members=[old, new]))

        assert survivor is new
        assert rule.startswith("created_at")

    def test_an_unparseable_or_absent_isrc_falls_back_to_created_at(self):
        old = _candidate(isrc=None, created_at=_EPOCH)
        new = _candidate(
            isrc="not-an-isrc", created_at=datetime(2026, 6, 1, tzinfo=UTC)
        )

        survivor, rule = pick_survivor(_Group(members=[old, new]))

        assert survivor is new
        assert rule.startswith("created_at")


def _pair(
    *,
    owned: _Candidate | None = None,
    foreign: _Candidate | None = None,
) -> _CrossTenantPair:
    return _CrossTenantPair(
        owned=owned or _candidate(isrc="GBEXH1900012"),
        foreign=foreign or _candidate(isrc="GBEXH1900012"),
        foreign_owner="default",
        dep_track_plays=1,
        dep_connector_plays=1,
        collisions=("uq_tracks_user_isrc",),
    )


class TestCrossTenantSurvivorIsOwnership:
    def test_the_owned_track_survives_even_with_the_older_isrc(self):
        """Ownership decides, not the ISRC.

        The ISRC rule is right for same-user groups and wrong here: a merge into
        the other tenant's row would leave this user's play history hanging off
        a track they do not own — the very defect being repaired.
        """
        owned = _candidate(isrc="GBEXH1900012")  # 2019
        foreign = _candidate(isrc="USA2B2056087")  # 2020

        pair = _pair(owned=owned, foreign=foreign)

        assert pair.owned is owned
        assert pair.owned.isrc_year == 2019
        assert pair.foreign.isrc_year == 2020
        # The same two rows handed to the same-user rule pick the other side —
        # which is exactly why that rule is not used for this population.
        by_isrc, _ = pick_survivor(_Group(members=[owned, foreign]))
        assert by_isrc is foreign

    def test_a_duration_compatible_pair_is_mergeable(self):
        assert _pair(
            owned=_candidate(duration_ms=218_700),
            foreign=_candidate(duration_ms=218_700),
        ).is_mergeable

    def test_a_pair_that_is_not_one_recording_is_a_conflict_not_a_merge(self):
        """Two different recordings colliding on a unique constraint.

        Reported, never presented as mergeable: a merge would destroy one of
        two genuinely different tracks.
        """
        pair = _pair(
            owned=_candidate(duration_ms=200_000),
            foreign=_candidate(duration_ms=280_000),
        )

        assert not pair.is_mergeable
        assert "identity conflict" in pair.conflict_reason

    def test_a_missing_duration_is_a_conflict_with_its_own_reason(self):
        pair = _pair(
            owned=_candidate(duration_ms=None), foreign=_candidate(duration_ms=200_000)
        )

        assert not pair.is_mergeable
        assert "duration is missing" in pair.conflict_reason

    def test_dependent_plays_sum_both_play_tables(self):
        assert _pair().dependent_plays == 2


class TestCollisionLabelling:
    def test_every_clash_column_naming_the_survivor_is_reported(self):
        owned_id = uuid4()
        row: dict[str, object] = {column: owned_id for column, _ in _COLLISION_LABELS}

        assert collisions_for(row, owned_id) == tuple(
            label for _, label in _COLLISION_LABELS
        )
        assert collisions_elsewhere(row, owned_id) == ()

    def test_a_clash_naming_a_third_track_is_reported_separately(self):
        """One merge does not clear a foreign track colliding with two rows."""
        owned_id = uuid4()
        other_id = uuid4()
        row: dict[str, object] = {
            "clash_isrc": owned_id,
            "clash_spotify_id": other_id,
        }

        assert collisions_for(row, owned_id) == ("uq_tracks_user_isrc",)
        assert collisions_elsewhere(row, owned_id) == (
            ("uq_tracks_user_spotify_id", other_id),
        )

    def test_absent_columns_produce_no_collisions(self):
        assert collisions_for({}, uuid4()) == ()
        assert collisions_elsewhere({}, uuid4()) == ()
