"""Factory functions that bridge config settings to domain objects.

Separates object construction from Pydantic settings definitions,
keeping settings.py focused on configuration schema and validation.
"""

import functools

from src.domain.matching.config import MatchingConfig as DomainMatchingConfig
from src.domain.matching.evaluation_service import TrackMatchEvaluationService

from .settings import settings


@functools.cache
def create_matching_config() -> DomainMatchingConfig:
    """Create domain MatchingConfig from application settings.

    Bridges the Pydantic settings layer to the domain value object,
    keeping domain code free of config imports. Explicit field mapping
    so pyright verifies each assignment — type mismatches fail at check time.
    """
    m = settings.matching
    return DomainMatchingConfig(
        identical_similarity_score=m.identical_similarity_score,
        variation_similarity_score=m.variation_similarity_score,
        auto_accept_threshold=m.auto_accept_threshold,
        review_threshold=m.review_threshold,
        high_similarity_threshold=m.high_similarity_threshold,
        phonetic_similarity_score=m.phonetic_similarity_score,
    )


@functools.cache
def create_evaluation_service() -> TrackMatchEvaluationService:
    """Create a TrackMatchEvaluationService with production config.

    Centralizes the two-step construction pattern used by
    InwardTrackResolver, SpotifyCrossDiscoveryProvider, and
    MatchAndIdentifyTracksUseCase.
    """
    return TrackMatchEvaluationService(config=create_matching_config())
