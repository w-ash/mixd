"""Tests for matcher_version: determinism, config sensitivity, and breadth.

Breadth matters here specifically because the hash's whole guarantee rests on
introspection (``vars(probabilistic)``) actually finding every
``ComparisonLevel`` — a filter bug that silently returned an empty list would
still produce *a* hash, just one blind to most of the matcher's behavior.
"""

import attrs
import pytest

from src.domain.matching import (
    algorithms,
    isrc_validation,
    probabilistic,
    text_normalization,
    types,
)
from src.domain.matching.config import MatchingConfig
from src.domain.matching.probabilistic import ComparisonLevel
from src.domain.matching.version import (
    _canonical_serialization,  # pyright: ignore[reportPrivateUsage]
    _comparison_levels,  # pyright: ignore[reportPrivateUsage]
    matcher_version,
)


def make_config(**overrides: float) -> MatchingConfig:
    defaults: dict[str, float] = {
        "identical_similarity_score": 1.0,
        "variation_similarity_score": 0.6,
        "auto_accept_threshold": 85,
        "review_threshold": 50,
        "high_similarity_threshold": 0.9,
        "phonetic_similarity_score": 0.8,
    }
    defaults.update(overrides)
    return MatchingConfig(**defaults)  # pyright: ignore[reportArgumentType]


class TestDeterminism:
    def test_equal_but_distinct_config_instances_yield_the_same_hash(self):
        first = make_config()
        second = make_config()
        assert matcher_version(first) == matcher_version(second)

    def test_hash_is_twelve_hex_characters(self):
        result = matcher_version(make_config())
        assert len(result) == 12
        assert all(c in "0123456789abcdef" for c in result)


class TestConfigSensitivity:
    @pytest.mark.parametrize(
        "field_name", [field.name for field in attrs.fields(MatchingConfig)]
    )
    def test_changing_any_config_field_changes_the_hash(self, field_name: str):
        """Every field is hashed, including one added after this test was written."""
        baseline = make_config()
        changed = attrs.evolve(
            baseline, **{field_name: getattr(baseline, field_name) + 1}
        )
        assert matcher_version(changed) != matcher_version(baseline)

    def test_a_small_float_change_changes_the_hash(self):
        changed = make_config(phonetic_similarity_score=0.81)
        assert matcher_version(changed) != matcher_version(make_config())


LEVEL_NAMES = [
    "artist_alias_name",
    "artist_exact",
    "artist_high_fuzzy",
    "artist_id_connector",
    "artist_id_mbid",
    "artist_low_fuzzy",
    "artist_mismatch",
    "artist_missing",
    "artist_name_lastfm",
    "artist_phonetic",
    "duration_close",
    "duration_mismatch",
    "duration_missing",
    "duration_moderate",
    "duration_near",
    "isrc_absent",
    "isrc_exact",
    "isrc_suspect",
    "title_exact",
    "title_high_fuzzy",
    "title_mismatch",
    "title_missing",
    "title_moderate_fuzzy",
    "title_phonetic",
    "title_variation",
]


class TestBreadth:
    def test_introspection_discovers_every_comparison_level_sorted_by_name(self):
        # A filter or vars() bug that returned nothing would still produce a
        # hash, just one blind to the matcher's behavior.
        assert [level.name for level in _comparison_levels()] == LEVEL_NAMES

    def test_serialization_labels_each_hashed_input_in_a_fixed_order(self):
        """One labelled line per hashed input, sorted keys within each.

        The labels are hashed too: renaming one would move every matcher
        version without any matcher change.
        """
        expected_prefixes = [
            "config:auto_accept_threshold=",
            "tier_boundaries:duration_close_ms=",
            "variation_markers:markers=",
            "text_equivalences:rules=",
            "isrc_validation:grade_methods=",
            *(f"level:{name}:m_probability=" for name in LEVEL_NAMES),
        ]

        lines = _canonical_serialization(make_config()).split("\n")

        assert len(lines) == len(expected_prefixes)
        for line, prefix in zip(lines, expected_prefixes, strict=True):
            assert line.startswith(prefix)


class TestConstantSensitivity:
    """Changing any hashed module constant must change the serialization.

    These target ``_canonical_serialization`` rather than ``matcher_version``
    on purpose: the latter is ``functools.cache``d on the config, so a
    monkeypatched constant would be invisible behind a stale cached hash and
    the test would pass for the wrong reason.
    """

    @pytest.mark.parametrize(
        ("module", "name", "value"),
        [
            pytest.param(
                algorithms,
                "VARIATION_MARKERS",
                frozenset({"live"}),
                id="variation_markers",
            ),
            pytest.param(
                isrc_validation,
                "SUSPECT_DURATION_DIFF_MS",
                12_000,
                id="suspect_duration",
            ),
            pytest.param(
                types, "ISRC_GRADE_METHODS", ("isrc",), id="isrc_grade_methods"
            ),
            pytest.param(
                text_normalization,
                "EQUIVALENCE_RULES",
                (("ft", 2, "featuring"),),
                id="equivalence_rules",
            ),
            pytest.param(
                probabilistic,
                "TIER_BOUNDARIES",
                (
                    ("moderate_similarity", 0.75),
                    ("duration_close_ms", 1_000),
                    ("duration_near_ms", 3_000),
                    ("duration_moderate_ms", 10_000),
                ),
                id="tier_boundaries",
            ),
            pytest.param(
                probabilistic,
                "TITLE_EXACT",
                ComparisonLevel("title_exact", m_probability=0.9, u_probability=0.005),
                id="level_m_probability",
            ),
            pytest.param(
                probabilistic,
                "TITLE_EXACT",
                ComparisonLevel("title_exact", m_probability=0.95, u_probability=0.01),
                id="level_u_probability",
            ),
        ],
    )
    def test_changing_a_hashed_constant_changes_the_serialization(
        self,
        monkeypatch: pytest.MonkeyPatch,
        module: object,
        name: str,
        value: object,
    ):
        config = make_config()
        baseline = _canonical_serialization(config)

        monkeypatch.setattr(module, name, value)

        assert _canonical_serialization(config) != baseline
