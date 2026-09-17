"""The ``match_method`` / ``origin`` vocabularies and the maps keyed by them.

The ``Literal`` aliases and their ``frozenset`` twins are edited together by
hand; these pin that the runtime sets, the stale-id pairing and the
presentation maps all describe the same members.
"""

from src.domain.entities.track_mapping import (
    MAPPING_ORIGINS,
    MATCH_METHOD_CATEGORIES,
    MATCH_METHOD_CATEGORY_ORDER,
    MATCH_METHOD_DESCRIPTIONS,
    MATCH_METHODS,
    STALE_ID_FOR,
    is_mapping_origin,
    is_match_method,
)
from src.domain.matching.types import ISRC_GRADE_METHODS


class TestVocabularySets:
    def test_guards_accept_members_and_reject_the_empty_string(self):
        assert all(is_match_method(m) for m in MATCH_METHODS)
        assert not is_match_method("")
        assert not is_match_method("direct-import")
        assert all(is_mapping_origin(o) for o in MAPPING_ORIGINS)
        assert not is_mapping_origin("manual")


class TestDerivedMaps:
    def test_stale_id_pairs_are_members_with_the_suffix(self):
        for primary, stale in STALE_ID_FOR.items():
            assert primary in MATCH_METHODS
            assert stale in MATCH_METHODS
            assert stale == f"{primary}_stale_id"

    def test_every_method_has_a_category_and_a_description(self):
        assert set(MATCH_METHOD_CATEGORIES) == MATCH_METHODS
        assert set(MATCH_METHOD_DESCRIPTIONS) == MATCH_METHODS
        assert set(MATCH_METHOD_CATEGORIES.values()) <= set(MATCH_METHOD_CATEGORY_ORDER)

    def test_isrc_grade_methods_are_members(self):
        assert set(ISRC_GRADE_METHODS) <= MATCH_METHODS
