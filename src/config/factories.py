"""Factory functions that bridge config settings to domain objects.

Separates object construction from Pydantic settings definitions,
keeping settings.py focused on configuration schema and validation.
"""

import functools
from typing import TYPE_CHECKING

from src.domain.matching.config import MatchingConfig as DomainMatchingConfig

from .settings import settings

if TYPE_CHECKING:
    from src.domain.matching.evaluation_service import TrackMatchEvaluationService


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
        base_confidence_isrc=m.base_confidence_isrc,
        base_confidence_mbid=m.base_confidence_mbid,
        base_confidence_artist_title=m.base_confidence_artist_title,
        isrc_suspect_base_confidence=m.isrc_suspect_base_confidence,
        auto_accept_threshold=m.auto_accept_threshold,
        review_threshold=m.review_threshold,
        threshold_isrc=m.threshold_isrc,
        threshold_mbid=m.threshold_mbid,
        threshold_artist_title=m.threshold_artist_title,
        threshold_default=m.threshold_default,
        high_similarity_threshold=m.high_similarity_threshold,
        title_max_penalty=m.title_max_penalty,
        artist_max_penalty=m.artist_max_penalty,
        phonetic_similarity_score=m.phonetic_similarity_score,
        duration_missing_penalty=m.duration_missing_penalty,
        duration_max_penalty=m.duration_max_penalty,
        duration_tolerance_ms=m.duration_tolerance_ms,
        duration_per_second_penalty=m.duration_per_second_penalty,
    )


@functools.cache
def create_evaluation_service() -> TrackMatchEvaluationService:
    """Create a TrackMatchEvaluationService with production config.

    Centralizes the two-step construction pattern used by
    InwardTrackResolver, SpotifyCrossDiscoveryProvider, and
    MatchAndIdentifyTracksUseCase.
    """
    from src.domain.matching.evaluation_service import (
        TrackMatchEvaluationService as _Svc,
    )

    return _Svc(config=create_matching_config())
