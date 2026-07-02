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

    # Three-zone classification thresholds
    auto_accept_threshold: int
    review_threshold: int

    # Similarity thresholds
    high_similarity_threshold: float

    # Phonetic matching
    phonetic_similarity_score: float
