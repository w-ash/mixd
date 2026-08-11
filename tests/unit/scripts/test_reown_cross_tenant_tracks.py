"""Refusal logic of the cross-tenant re-own script (finding C3).

The script is a one-shot prod repair and is never executed here. What these
tests pin is the one thing that decides whether it writes at all: ``blockers_for``,
which turns a discovery row into the list of reasons a re-own would really be a
merge. Every non-NULL clash column must produce a blocker — a missed one is a
``uq_tracks_user_isrc`` violation at best and a silent duplicate canonical at
worst — and a clean row must produce none, or the script refuses work it should
do.

Merge mode adds a second decision of the same kind: ``MergePair`` refuses to
exist unless the winner is the track the target user owns, and ``refusals``
turns the pair's counts into the reasons a merge must not run. Both are pure,
and both are the last thing standing between a repair and a merge that destroys
the history it was meant to rescue.

The moves themselves (the re-own's travelling companions, and the merge's
retire/delete sequence) are pinned in
``tests/integration/test_mistenancy_repair_scripts.py``, because what travels is
a property of real constraints.
"""

from uuid import UUID, uuid4, uuid7

import pytest

from scripts.reown_cross_tenant_tracks import (
    _CLASH_REASONS,
    _CLASH_REFUSALS,
    _LEFT_REASONS,
    _UNSAFE_REASONS,
    MergePair,
    Side,
    blockers_for,
    to_pair,
)
from src.domain.matching.isrc_validation import SUSPECT_DURATION_DIFF_MS

_USER = "cfe9d062-577f-4f51-8813-47db89650793"
_DURATION_MS = 156_100


def _side(
    *,
    owner: str,
    track_id: UUID | None = None,
    title: str = "Kindness",
    duration_ms: int | None = _DURATION_MS,
) -> Side:
    return Side(
        track_id=track_id or uuid7(),
        owner=owner,
        title=title,
        artists="HNNY",
        primary_artist="HNNY",
        isrc="QMFME1984859",
        spotify_id="7mY3gSbmqBfMqKv0cCyXJl",
        duration_ms=duration_ms,
        track_plays=1,
        connector_plays=1,
    )


def _pair(
    *,
    winner_owner: str = _USER,
    loser_owner: str = "default",
    winner_id: UUID | None = None,
    loser_id: UUID | None = None,
    loser_title: str = "Kindness",
    loser_duration_ms: int | None = _DURATION_MS,
    **counts: int,
) -> MergePair:
    """A mergeable pair by default; every keyword makes it worse."""
    winner = _side(owner=winner_owner, track_id=winner_id)
    return MergePair(
        user=_USER,
        winner=winner,
        loser=_side(
            owner=loser_owner,
            track_id=loser_id,
            title=loser_title,
            duration_ms=loser_duration_ms,
        ),
        counts=counts,
        clash_ids={"clash_isrc": winner.track_id},
    )


def _row(**overrides: object) -> dict[str, object]:
    """A discovery row of the shape ``_MERGE_PAIRS_SQL`` returns."""
    winner_id = uuid7()
    row: dict[str, object] = {
        "winner_owner": _USER,
        "winner_id": winner_id,
        "winner_title": "Kindness",
        "winner_artists_text": "HNNY",
        "winner_primary_artist": "HNNY",
        "winner_isrc": "QMFME1984859",
        "winner_spotify_id": "7mY3gSbmqBfMqKv0cCyXJl",
        "winner_duration_ms": _DURATION_MS,
        "winner_track_plays": 3,
        "winner_connector_plays": 3,
        "loser_owner": "default",
        "loser_id": uuid7(),
        "loser_title": "Kindness",
        "loser_artists_text": "HNNY",
        "loser_primary_artist": "HNNY",
        "loser_isrc": "QMFME1984859",
        "loser_spotify_id": "7mY3gSbmqBfMqKv0cCyXJl",
        "loser_duration_ms": _DURATION_MS,
        "loser_track_plays": 1,
        "loser_connector_plays": 1,
        "clash_isrc": winner_id,
        "clash_spotify_id": None,
        "clash_mbid": None,
        "clash_normalized": None,
        "carry_mappings": 1,
    }
    return row | overrides


def _clean_row() -> dict[str, object]:
    """A discovery row with every clash column NULL."""
    return {
        "id": uuid4(),
        "owner": "default",
        "title": "Michicant",
        **{column: None for column, _ in _CLASH_REASONS},
    }


class TestNoBlockers:
    def test_a_row_with_no_clashes_is_re_ownable(self):
        assert blockers_for(_clean_row()) == ()

    def test_a_row_missing_the_clash_columns_entirely_is_not_assumed_blocked(self):
        """``.get`` semantics: an absent column means "not checked", not "clash".

        Pinned because the opposite default would make every track unmovable the
        moment a column is renamed, and the script would silently do nothing.
        """
        assert blockers_for({"id": uuid4()}) == ()


class TestEveryClashColumnBlocks:
    def test_each_clash_column_alone_produces_its_blocker(self):
        for column, reason in _CLASH_REASONS:
            row = _clean_row()
            other_id = uuid4()
            row[column] = other_id

            assert blockers_for(row) == ((reason, other_id),), column

    def test_multiple_clashes_are_all_reported(self):
        """The operator needs every reason, not the first one found — an ISRC
        clash and a live-mapping clash have different remedies."""
        row = _clean_row()
        row["clash_isrc"] = uuid4()
        row["clash_live_mapping"] = uuid4()

        assert len(blockers_for(row)) == 2

    def test_normalization_equality_blocks_even_with_no_shared_identifier(self):
        """The case no unique constraint would catch: same recording, no ISRC
        or Spotify id in common. Re-owning it would create a duplicate
        canonical rather than raise."""
        row = _clean_row()
        winner = uuid4()
        row["clash_normalized"] = winner

        blockers = blockers_for(row)

        assert len(blockers) == 1
        assert blockers[0][1] == winner
        assert "normalization-equal" in blockers[0][0]


class TestMergePairWinnerIsAlwaysTheOwnedTrack:
    """The invariant merge mode turns on, enforced at construction.

    Merging into the foreign row would leave the user's history on a row they
    do not own — the defect being repaired, with the duplicate tidied up around
    it. Pinned here because the plan, the refusal check and the mutation all
    take a ``MergePair``: if one cannot be built pointing the wrong way, none of
    the three can be handed one.
    """

    def test_a_pair_whose_winner_is_owned_elsewhere_cannot_be_built(self):
        with pytest.raises(ValueError, match="the survivor must be the track"):
            _ = _pair(winner_owner="someone_else")

    def test_a_pair_whose_loser_the_user_already_owns_cannot_be_built(self):
        """That is a same-user duplicate — find_duplicate_canonicals' first
        population, and a different repair with a different survivor rule."""
        with pytest.raises(ValueError, match="already owned by"):
            _ = _pair(loser_owner=_USER)

    def test_a_track_cannot_be_merged_with_itself(self):
        track_id = uuid7()
        with pytest.raises(ValueError, match="merge a track with itself"):
            _ = _pair(winner_id=track_id, loser_id=track_id)

    def test_the_discovery_row_builds_the_owned_side_as_the_winner(self):
        """``to_pair`` reads the query's aliases, so a swapped SELECT is caught
        by the constructor rather than producing a backwards merge."""
        pair = to_pair(_row(), user=_USER)

        assert pair.winner.owner == _USER
        assert pair.loser.owner == "default"


class TestMergeRefusals:
    def test_an_identical_pair_is_mergeable(self):
        assert _pair().refusals == ()
        assert _pair().is_mergeable

    def test_a_duration_gap_over_the_threshold_is_an_identity_conflict(self):
        """Two tenants holding genuinely different recordings that collide on
        one identifier. Merging would destroy real data."""
        pair = _pair(loser_duration_ms=156_100 + SUSPECT_DURATION_DIFF_MS + 1)

        assert not pair.is_mergeable
        assert "identity conflict" in pair.refusals[0]

    def test_a_missing_duration_refuses_rather_than_assuming(self):
        pair = _pair(loser_duration_ms=None)

        assert "a duration is missing" in pair.refusals[0]

    def test_a_different_title_refuses_even_at_the_same_duration(self):
        pair = _pair(loser_title="Kindness - Live at Berghain")

        assert "different recordings" in pair.refusals[0]

    def test_every_cascade_only_table_refuses_the_pair(self):
        """None of these move with the merge, and the loser's delete CASCADEs
        them — a merge that runs anyway destroys them silently."""
        for column, _ in _UNSAFE_REASONS:
            pair = _pair(**{column: 1})

            assert len(pair.refusals) == 1, column
            assert "CASCADE" in pair.refusals[0], column

    def test_another_tenants_behavioural_rows_refuse_the_pair(self):
        """``move_references_to_track`` is not user-scoped: it would move them
        onto this user's canonical and leave them owned by the old tenant."""
        for column, _ in _LEFT_REASONS:
            pair = _pair(**{column: 1})

            assert len(pair.refusals) == 1, column
            assert "another tenant" in pair.refusals[0], column

    def test_a_constraint_the_merge_would_hit_refuses_the_pair(self):
        for column, _ in _CLASH_REFUSALS:
            pair = _pair(**{column: 1})

            assert len(pair.refusals) == 1, column

    def test_every_reason_is_reported_not_just_the_first(self):
        pair = _pair(unsafe_track_tags=1, left_track_likes=2, clash_play_dedup=1)

        assert len(pair.refusals) == 3

    def test_zero_counts_never_refuse(self):
        """The columns are counts, so the absent-and-zero case must read as
        safe — the opposite default would refuse every pair forever."""
        assert _pair(**{column: 0 for column, _ in _UNSAFE_REASONS}).refusals == ()


class TestMergeCollisionLabels:
    def test_a_clash_naming_the_winner_is_reported_as_a_collision(self):
        pair = to_pair(_row(), user=_USER)

        assert "uq_tracks_user_isrc" in pair.collisions
        assert pair.collisions_elsewhere == ()

    def test_a_clash_naming_a_third_owned_track_is_reported_separately(self):
        """One merge does not clear it — the operator needs to know a second
        pass will still find the loser colliding."""
        third = uuid7()
        pair = to_pair(_row(clash_mbid=third), user=_USER)

        assert "uq_tracks_user_mbid" not in pair.collisions
        assert pair.collisions_elsewhere == (("uq_tracks_user_mbid", third),)

    def test_an_identifier_clash_is_never_read_as_a_count(self):
        """The clash columns hold the colliding track's *id*; the refusal
        columns hold counts. Coercing the two together silently zeroes every
        collision, which would print an empty plan for a real one."""
        pair = to_pair(_row(), user=_USER)

        assert pair.counts.get("clash_isrc") is None
        assert pair.collisions != ()
