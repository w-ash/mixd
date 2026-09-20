"""The injected alias seam: compiling groups, and what it does to a match.

The epic's acceptance criterion is here — "TEED" and "Totally Enormous Extinct
Dinosaurs" score as an artist match when alias data is present and as a
mismatch without it — plus the property that makes the seam safe to add: a
caller that passes nothing gets exactly the old score.
"""

from src.domain.matching.algorithms import (
    InternalTrackData,
    ServiceTrackData,
    calculate_confidence,
)
from src.domain.matching.artist_equivalence import (
    EMPTY_EQUIVALENCE,
    ArtistEquivalence,
)
from src.domain.matching.config import MatchingConfig

TEED = "TEED"
FULL_NAME = "Totally Enormous Extinct Dinosaurs"


def make_config() -> MatchingConfig:
    return MatchingConfig(
        identical_similarity_score=1.0,
        variation_similarity_score=0.6,
        auto_accept_threshold=85,
        review_threshold=50,
        high_similarity_threshold=0.9,
        phonetic_similarity_score=0.85,
    )


def internal(artist: str) -> InternalTrackData:
    return {"title": "Garden", "artists": [artist], "duration_ms": 240_000}


def service(artist: str) -> ServiceTrackData:
    return {"title": "Garden", "artist": artist, "duration_ms": 240_000}


class TestCompilation:
    def test_members_of_one_group_are_the_same_artist(self):
        equivalence = ArtistEquivalence.from_groups([[TEED, FULL_NAME]])
        assert equivalence.same(TEED, FULL_NAME)
        assert equivalence.same(FULL_NAME, TEED)

    def test_normalization_applies_to_both_sides(self):
        equivalence = ArtistEquivalence.from_groups([["Björk Guðmundsdóttir", "Bjork"]])
        assert equivalence.same("bjork guðmundsdottir", "BJORK")

    def test_members_of_different_groups_are_not_the_same(self):
        equivalence = ArtistEquivalence.from_groups([[TEED, FULL_NAME], ["Tourist"]])
        assert not equivalence.same(TEED, "Tourist")

    def test_an_unknown_name_is_never_a_match(self):
        equivalence = ArtistEquivalence.from_groups([[TEED, FULL_NAME]])
        assert not equivalence.same(TEED, "Four Tet")
        assert not equivalence.same("Four Tet", "KH")

    def test_overlapping_groups_merge_into_one_artist(self):
        # A MusicBrainz merge can leave two alias sets sharing a name; the
        # result must not depend on which row was read first.
        equivalence = ArtistEquivalence.from_groups([
            [TEED, FULL_NAME],
            [FULL_NAME, "Orlando Higginbottom"],
        ])
        assert equivalence.same(TEED, "Orlando Higginbottom")

    def test_blank_names_are_dropped_rather_than_grouped(self):
        equivalence = ArtistEquivalence.from_groups([["", "   "], ["", "Jungle"]])
        assert not equivalence.same("", "Jungle")
        assert not equivalence.same("", "")

    def test_the_empty_equivalence_matches_nothing(self):
        assert not EMPTY_EQUIVALENCE.same(TEED, TEED)


class TestConfidenceWithAliases:
    def test_alias_data_makes_the_abbreviation_an_artist_match(self):
        config = make_config()
        equivalence = ArtistEquivalence.from_groups([[TEED, FULL_NAME]])

        _, aliased = calculate_confidence(
            internal(FULL_NAME),
            service(TEED),
            "artist_title",
            config,
            artist_equivalence=equivalence,
        )
        _, bare = calculate_confidence(
            internal(FULL_NAME), service(TEED), "artist_title", config
        )

        assert aliased.artist_similarity == 1.0
        assert bare.artist_similarity < config.high_similarity_threshold
        assert aliased.artist_score > bare.artist_score

    def test_an_alias_hit_scores_as_an_exact_artist_agreement(self):
        config = make_config()
        equivalence = ArtistEquivalence.from_groups([[TEED, FULL_NAME]])

        _, aliased = calculate_confidence(
            internal(FULL_NAME),
            service(TEED),
            "artist_title",
            config,
            artist_equivalence=equivalence,
        )
        _, identical = calculate_confidence(
            internal(TEED), service(TEED), "artist_title", config
        )

        assert aliased.artist_score == identical.artist_score

    def test_unrelated_artists_still_mismatch_with_alias_data_present(self):
        config = make_config()
        equivalence = ArtistEquivalence.from_groups([[TEED, FULL_NAME]])

        _, unrelated = calculate_confidence(
            internal(FULL_NAME),
            service("Justice"),
            "artist_title",
            config,
            artist_equivalence=equivalence,
        )
        _, aliased = calculate_confidence(
            internal(FULL_NAME),
            service(TEED),
            "artist_title",
            config,
            artist_equivalence=equivalence,
        )

        assert unrelated.artist_similarity < config.high_similarity_threshold
        assert unrelated.artist_score < 0  # evidence against, not merely weak
        assert unrelated.artist_score < aliased.artist_score

    def test_omitting_the_seam_reproduces_the_previous_score(self):
        config = make_config()
        default = calculate_confidence(
            internal(FULL_NAME), service(FULL_NAME), "artist_title", config
        )
        explicit_none = calculate_confidence(
            internal(FULL_NAME),
            service(FULL_NAME),
            "artist_title",
            config,
            artist_equivalence=None,
        )
        empty = calculate_confidence(
            internal(FULL_NAME),
            service(FULL_NAME),
            "artist_title",
            config,
            artist_equivalence=EMPTY_EQUIVALENCE,
        )

        assert default == explicit_none == empty
