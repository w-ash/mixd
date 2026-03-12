"""Fellegi-Sunter probabilistic scoring model for track identity resolution.

Replaces the additive base-minus-penalty model with log-likelihood ratio scoring.
Each attribute comparison produces a log-likelihood ratio where rare agreements
provide stronger evidence than common ones.

The model uses the Splink "comparison levels" pattern: each attribute is compared
at multiple granularity tiers (exact > phonetic > high fuzzy > low fuzzy > mismatch),
with each tier having its own m/u probabilities.

References:
    - Fellegi & Sunter (1969): "A Theory for Record Linkage"
    - Splink: https://moj-analytical-services.github.io/splink/
    - "(Almost) All of Entity Resolution": https://doi.org/10.1126/sciadv.abi8021
"""

import math

from attrs import define


@define(frozen=True, slots=True)
class ComparisonLevel:
    """A single comparison outcome tier for an attribute.

    Each level has m-probability (P(agree at this level | true match)) and
    u-probability (P(agree at this level | random pair)). The log-likelihood
    ratio log(m/u) measures the discriminating power of this outcome.
    """

    name: str
    m_probability: float
    u_probability: float

    @property
    def log_likelihood_ratio(self) -> float:
        """Log-likelihood ratio: evidence strength of this comparison outcome."""
        if self.u_probability <= 0 or self.m_probability <= 0:
            # Avoid log(0); treat as very strong/weak evidence
            if self.m_probability > 0:
                return 15.0  # Extremely strong evidence for match
            return -15.0  # Extremely strong evidence against match
        return math.log(self.m_probability / self.u_probability)


@define(frozen=True, slots=True)
class AttributeResult:
    """Result of classifying an attribute comparison into a level."""

    attribute_name: str
    level: ComparisonLevel


# ── Title comparison levels ─────────────────────────────────────────────

TITLE_EXACT = ComparisonLevel("title_exact", m_probability=0.95, u_probability=0.005)
TITLE_PHONETIC = ComparisonLevel(
    "title_phonetic", m_probability=0.90, u_probability=0.01
)
TITLE_HIGH_FUZZY = ComparisonLevel(
    "title_high_fuzzy", m_probability=0.85, u_probability=0.02
)
TITLE_MODERATE_FUZZY = ComparisonLevel(
    "title_moderate_fuzzy", m_probability=0.70, u_probability=0.05
)
TITLE_VARIATION = ComparisonLevel(
    "title_variation", m_probability=0.30, u_probability=0.03
)
TITLE_MISMATCH = ComparisonLevel(
    "title_mismatch", m_probability=0.05, u_probability=0.60
)

# ── Artist comparison levels ────────────────────────────────────────────

ARTIST_EXACT = ComparisonLevel(
    "artist_exact", m_probability=0.95, u_probability=0.002
)
ARTIST_PHONETIC = ComparisonLevel(
    "artist_phonetic", m_probability=0.88, u_probability=0.01
)
ARTIST_HIGH_FUZZY = ComparisonLevel(
    "artist_high_fuzzy", m_probability=0.80, u_probability=0.03
)
ARTIST_LOW_FUZZY = ComparisonLevel(
    "artist_low_fuzzy", m_probability=0.60, u_probability=0.08
)
ARTIST_MISMATCH = ComparisonLevel(
    "artist_mismatch", m_probability=0.05, u_probability=0.70
)

# ── Duration comparison levels ──────────────────────────────────────────

DURATION_CLOSE = ComparisonLevel(
    "duration_close", m_probability=0.95, u_probability=0.10
)
DURATION_NEAR = ComparisonLevel(
    "duration_near", m_probability=0.90, u_probability=0.15
)
DURATION_MODERATE = ComparisonLevel(
    "duration_moderate", m_probability=0.70, u_probability=0.25
)
DURATION_MISMATCH = ComparisonLevel(
    "duration_mismatch", m_probability=0.15, u_probability=0.60
)
DURATION_MISSING = ComparisonLevel(
    "duration_missing", m_probability=0.50, u_probability=0.50
)

# ── ISRC comparison levels ──────────────────────────────────────────────

ISRC_EXACT = ComparisonLevel("isrc_exact", m_probability=0.99, u_probability=0.0001)
ISRC_SUSPECT = ComparisonLevel(
    "isrc_suspect", m_probability=0.80, u_probability=0.001
)
ISRC_ABSENT = ComparisonLevel(
    "isrc_absent", m_probability=0.50, u_probability=0.50
)  # Neutral


def classify_title(
    similarity: float,
    *,
    is_phonetic_match: bool,
    is_variation: bool,
    high_similarity_threshold: float = 0.9,
) -> ComparisonLevel:
    """Classify a title comparison into the appropriate level.

    Args:
        similarity: Raw similarity score (0.0-1.0).
        is_phonetic_match: Whether the titles match phonetically.
        is_variation: Whether a title variation was detected (live, remix, etc.).
        high_similarity_threshold: Threshold for "high fuzzy" tier.
    """
    if similarity >= 1.0:
        return TITLE_EXACT
    if is_variation:
        return TITLE_VARIATION
    if is_phonetic_match:
        return TITLE_PHONETIC
    if similarity >= high_similarity_threshold:
        return TITLE_HIGH_FUZZY
    if similarity >= 0.7:
        return TITLE_MODERATE_FUZZY
    return TITLE_MISMATCH


def classify_artist(
    similarity: float,
    *,
    is_phonetic_match: bool,
    high_similarity_threshold: float = 0.9,
) -> ComparisonLevel:
    """Classify an artist comparison into the appropriate level."""
    if similarity >= 1.0:
        return ARTIST_EXACT
    if is_phonetic_match:
        return ARTIST_PHONETIC
    if similarity >= high_similarity_threshold:
        return ARTIST_HIGH_FUZZY
    if similarity >= 0.7:
        return ARTIST_LOW_FUZZY
    return ARTIST_MISMATCH


def classify_duration(duration_diff_ms: int | None) -> ComparisonLevel:
    """Classify a duration comparison into the appropriate level."""
    if duration_diff_ms is None:
        return DURATION_MISSING
    if duration_diff_ms <= 1_000:
        return DURATION_CLOSE
    if duration_diff_ms <= 3_000:
        return DURATION_NEAR
    if duration_diff_ms <= 10_000:
        return DURATION_MODERATE
    return DURATION_MISMATCH


def classify_isrc(
    *, isrc_matched: bool, isrc_suspect: bool, isrc_available: bool
) -> ComparisonLevel:
    """Classify an ISRC comparison into the appropriate level."""
    if not isrc_available:
        return ISRC_ABSENT
    if isrc_matched and isrc_suspect:
        return ISRC_SUSPECT
    if isrc_matched:
        return ISRC_EXACT
    return ISRC_ABSENT  # ISRC available but didn't match — neutral


def calculate_match_weight(results: list[AttributeResult]) -> float:
    """Sum log-likelihood ratios across all attribute comparisons.

    Returns:
        Raw match weight (log-odds). Positive = evidence for match,
        negative = evidence against match.
    """
    return sum(r.level.log_likelihood_ratio for r in results)


def weight_to_confidence(weight: float) -> int:
    """Convert raw match weight (log-odds) to 0-100 confidence score.

    Uses sigmoid function: P = 1 / (1 + exp(-weight)), then scales to 0-100.
    Monotonic and bounded.
    """
    probability = 1.0 / (1.0 + math.exp(-weight))
    return max(0, min(100, round(probability * 100)))
