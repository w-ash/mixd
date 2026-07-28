"""Tests for matcher_version: determinism, config sensitivity, and breadth.

Breadth matters here specifically because the hash's whole guarantee rests on
introspection (``vars(probabilistic)``) actually finding every
``ComparisonLevel`` — a filter bug that silently returned an empty list would
still produce *a* hash, just one blind to most of the matcher's behavior.
"""

from src.domain.matching.config import MatchingConfig
from src.domain.matching.probabilistic import TIER_BOUNDARIES
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
    def test_same_config_yields_same_hash_twice(self):
        config = make_config()
        assert matcher_version(config) == matcher_version(config)

    def test_equal_but_distinct_config_instances_yield_the_same_hash(self):
        first = make_config()
        second = make_config()
        assert matcher_version(first) == matcher_version(second)

    def test_hash_is_twelve_hex_characters(self):
        result = matcher_version(make_config())
        assert len(result) == 12
        assert all(c in "0123456789abcdef" for c in result)


class TestConfigSensitivity:
    def test_differing_threshold_changes_the_hash(self):
        baseline = make_config()
        changed = make_config(auto_accept_threshold=86)
        assert matcher_version(baseline) != matcher_version(changed)

    def test_differing_float_score_changes_the_hash(self):
        baseline = make_config()
        changed = make_config(phonetic_similarity_score=0.81)
        assert matcher_version(baseline) != matcher_version(changed)


class TestBreadth:
    def test_introspection_discovers_more_than_twenty_comparison_levels(self):
        # Guards against the isinstance filter or vars() call silently
        # returning nothing (e.g. a typo'd import) — the hash would still
        # "work" but stop covering the matcher's actual behavior.
        levels = _comparison_levels()
        assert len(levels) > 20

    def test_canonical_serialization_includes_config_and_tier_boundaries(self):
        serialized = _canonical_serialization(make_config())
        assert "config:" in serialized
        assert "tier_boundaries:" in serialized
        for name, _ in TIER_BOUNDARIES:
            assert name in serialized

    def test_canonical_serialization_includes_every_discovered_level(self):
        serialized = _canonical_serialization(make_config())
        for level in _comparison_levels():
            assert f"level:{level.name}:" in serialized
