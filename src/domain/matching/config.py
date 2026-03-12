"""Domain value object for matching algorithm configuration.

Pure shape — no defaults. Values are injected from application settings at
construction time, keeping the domain layer free of config imports.
"""

from attrs import define


@define(frozen=True, slots=True)
class MatchingConfig:
    """Configuration for track matching algorithms and confidence scoring.

    All fields are required with no defaults — the caller (config layer)
    is responsible for providing values from application settings.
    """

    # Title similarity scores
    identical_similarity_score: float
    variation_similarity_score: float

    # Base confidence by match method
    base_confidence_isrc: int
    base_confidence_mbid: int
    base_confidence_artist_title: int
    isrc_suspect_base_confidence: int

    # Three-zone classification thresholds
    auto_accept_threshold: int
    review_threshold: int

    # Legacy per-method thresholds (used as floor within review zone)
    threshold_isrc: int
    threshold_mbid: int
    threshold_artist_title: int
    threshold_default: int

    # Similarity thresholds
    high_similarity_threshold: float

    # Penalty caps
    title_max_penalty: int
    artist_max_penalty: int

    # Phonetic matching
    phonetic_similarity_score: float

    # Duration penalty
    duration_missing_penalty: int
    duration_max_penalty: int
    duration_tolerance_ms: int
    duration_per_second_penalty: float
