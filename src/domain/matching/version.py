"""Matcher version: a content hash of the resolution matcher's full configuration.

No ER product semvers its matcher (Splink's ``model.json`` is the closest
precedent, and it hashes too) — a version number is something a human assigns
and can therefore forget to bump. A silently edited threshold or a newly tuned
``ComparisonLevel`` probability must not be able to masquerade as "the same
matcher" just because nobody remembered to change a string (memo §10.4). A
content hash makes that structurally impossible: any change to any input here
changes the hash.

The hash is on purpose broader than just :class:`MatchingConfig`. It covers,
in full:

* every field of :class:`MatchingConfig`, enumerated by attrs introspection
  rather than named literally, so a newly added config field cannot escape it;
* ``probabilistic.TIER_BOUNDARIES``, which shape classification even though
  they never appear in :class:`MatchingConfig` itself;
* every module-level :class:`ComparisonLevel` instance in
  :mod:`src.domain.matching.probabilistic`, discovered by introspection so a
  newly added level can't silently escape either;
* ``algorithms.VARIATION_MARKERS``, the tokens that decide whether a title
  difference scores as a variation or a mismatch;
* ``text_normalization.EQUIVALENCE_RULES``, the rewrites applied before every
  string comparison — pattern, flags, and replacement, in application order;
* ``isrc_validation.SUSPECT_DURATION_DIFF_MS`` and ``types.ISRC_GRADE_METHODS``,
  which decide ISRC_EXACT vs ISRC_SUSPECT and which match methods carry
  ISRC-grade weight at all.

Constants are read as module attributes (``algorithms.VARIATION_MARKERS``, not
a ``from`` import) so the hash reflects what those modules hold at call time.

Deliberately *not* hashed: the structural normalization regexes
(``_LEADING_ARTICLE``, ``_NON_ALNUM``, and ``strip_parentheticals``'
alternation). Those are substrate, not tuning levers — they define what "text"
means for every comparison rather than where any threshold sits, and editing
one is a change of representation that would invalidate stored evidence
wholesale, not a retunable matcher parameter. The boundary is declared here so
their absence reads as a decision rather than an oversight; moving one of them
into the lever category means adding it to ``_canonical_serialization``.

``matcher_version`` is a provenance/validity key recorded on every
:class:`~src.domain.entities.resolution_event.ResolutionEvent` — never an
auto-re-resolution trigger. Consumers decide separately whether a version
change warrants re-resolving anything.
"""

from collections.abc import Mapping
import functools
import hashlib
from typing import Final

import attrs

from src.domain.matching import (
    algorithms,
    isrc_validation,
    probabilistic,
    text_normalization,
    types,
)
from src.domain.matching.config import MatchingConfig
from src.domain.matching.probabilistic import ComparisonLevel

# Hex characters of the sha256 digest kept — enough to distinguish configs in
# practice, short enough to sit comfortably in a column and a log line.
_HASH_LENGTH: Final = 12


@functools.cache
def matcher_version(config: MatchingConfig) -> str:
    """Content hash identifying this exact matcher configuration.

    Same ``config`` and same contents in every module listed in this module's
    docstring always yield the same hash; any difference in any of them
    changes it.

    Cached on the frozen config: the other hashed inputs are module-level
    constants, so within one process the answer cannot change. Without this,
    every ``ResolutionRecorder`` re-introspected ``probabilistic`` and re-ran
    sha256 — and the recorders are built several times per import run.
    """
    canonical = _canonical_serialization(config)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_HASH_LENGTH]


def _canonical_serialization(config: MatchingConfig) -> str:
    """Deterministic string covering every hashed component.

    Sorted keys throughout (component sections, then fields within each
    section) so the same inputs always serialize identically regardless of
    dict/attribute iteration order; values use ``repr`` so no formatting choice
    (locale, trailing zeros, scientific notation) can vary between two
    otherwise-identical values.

    Collections are canonicalized by the caller before they get here: sets are
    sorted (no iteration-order dependence), sequences whose order is itself
    behavior — the equivalence rules, applied in sequence — are left alone.
    """
    # attrs introspection rather than six literals: a field added to
    # ``MatchingConfig`` is picked up automatically instead of silently
    # escaping the hash. ``asdict`` is flat here (all fields are floats/ints).
    config_fields: Mapping[str, object] = attrs.asdict(config)

    sections: list[str] = [
        _serialize_section("config", config_fields),
        _serialize_section("tier_boundaries", dict(probabilistic.TIER_BOUNDARIES)),
        _serialize_section(
            "variation_markers",
            {"markers": tuple(sorted(algorithms.VARIATION_MARKERS))},
        ),
        _serialize_section(
            "text_equivalences",
            {"rules": text_normalization.EQUIVALENCE_RULES},
        ),
        _serialize_section(
            "isrc_validation",
            {
                "suspect_duration_diff_ms": isrc_validation.SUSPECT_DURATION_DIFF_MS,
                "grade_methods": types.ISRC_GRADE_METHODS,
            },
        ),
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


def _serialize_section(section: str, values: Mapping[str, object]) -> str:
    body = ",".join(f"{key}={_serialize_value(values[key])}" for key in sorted(values))
    return f"{section}:{body}"


def _serialize_value(value: object) -> str:
    """``repr`` of an already-canonical value.

    Deliberately not a recursive serializer: callers hand in scalars or
    collections they have already put in a deterministic order, so ``repr`` is
    both stable and honest about nesting.
    """
    return repr(value)
