"""Matcher version: a content hash of the resolution matcher's full configuration.

No ER product semvers its matcher (Splink's ``model.json`` is the closest
precedent, and it hashes too) — a version number is something a human assigns
and can therefore forget to bump. A silently edited threshold or a newly tuned
``ComparisonLevel`` probability must not be able to masquerade as "the same
matcher" just because nobody remembered to change a string (memo §10.4). A
content hash makes that structurally impossible: any change to any input here
changes the hash.

The hash covers three things, on purpose broader than just :class:`MatchingConfig`:
the config's own six fields; every module-level :class:`ComparisonLevel`
instance in :mod:`src.domain.matching.probabilistic`, discovered by
introspection so a newly added level can't silently escape the hash; and that
module's ``TIER_BOUNDARIES``, which shape classification even though they never
appear in :class:`MatchingConfig` itself.

``matcher_version`` is a provenance/validity key recorded on every
:class:`~src.domain.entities.resolution_event.ResolutionEvent` — never an
auto-re-resolution trigger. Consumers decide separately whether a version
change warrants re-resolving anything.
"""

from collections.abc import Mapping
import hashlib
from typing import Final

from src.domain.matching import probabilistic
from src.domain.matching.config import MatchingConfig
from src.domain.matching.probabilistic import TIER_BOUNDARIES, ComparisonLevel

# Hex characters of the sha256 digest kept — enough to distinguish configs in
# practice, short enough to sit comfortably in a column and a log line.
_HASH_LENGTH: Final = 12


def matcher_version(config: MatchingConfig) -> str:
    """Content hash identifying this exact matcher configuration.

    Same ``config`` and same ``probabilistic`` module contents always yield
    the same hash; any difference in either changes it.
    """
    canonical = _canonical_serialization(config)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_HASH_LENGTH]


def _canonical_serialization(config: MatchingConfig) -> str:
    """Deterministic string covering every hashed component.

    Sorted keys throughout (component sections, then fields within each
    section) so the same inputs always serialize identically regardless of
    dict/attribute iteration order; floats and ints use ``repr`` so no
    formatting choice (locale, trailing zeros, scientific notation) can vary
    between two otherwise-identical values.
    """
    sections: list[str] = [
        _serialize_section(
            "config",
            {
                "identical_similarity_score": config.identical_similarity_score,
                "variation_similarity_score": config.variation_similarity_score,
                "auto_accept_threshold": config.auto_accept_threshold,
                "review_threshold": config.review_threshold,
                "high_similarity_threshold": config.high_similarity_threshold,
                "phonetic_similarity_score": config.phonetic_similarity_score,
            },
        ),
        _serialize_section("tier_boundaries", dict(TIER_BOUNDARIES)),
    ]
    sections.extend(
        _serialize_section(
            f"level:{level.name}",
            {
                "m_probability": level.m_probability,
                "u_probability": level.u_probability,
            },
        )
        for level in _comparison_levels()
    )
    return "\n".join(sections)


def _comparison_levels() -> list[ComparisonLevel]:
    """Every module-level ``ComparisonLevel`` in ``probabilistic``, by name.

    Introspection rather than a hand-maintained list: a level added to that
    module is picked up automatically, so the hash can't silently go stale the
    way a manually curated tuple would.
    """
    members: dict[str, object] = dict(vars(probabilistic))
    levels = [
        member for member in members.values() if isinstance(member, ComparisonLevel)
    ]
    return sorted(levels, key=lambda level: level.name)


def _serialize_section(section: str, values: Mapping[str, float | int]) -> str:
    body = ",".join(f"{key}={_serialize_value(values[key])}" for key in sorted(values))
    return f"{section}:{body}"


def _serialize_value(value: float) -> str:
    return repr(value)
