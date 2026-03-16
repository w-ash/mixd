"""Factory functions that bridge config settings to domain objects.

Separates object construction from Pydantic settings definitions,
keeping settings.py focused on configuration schema and validation.
"""

# pyright: reportAny=false
# Legitimate Any: Pydantic model_dump() returns dict[str, Any]

from typing import TYPE_CHECKING

from src.domain.matching.config import MatchingConfig as DomainMatchingConfig

from .settings import settings

if TYPE_CHECKING:
    from src.domain.matching.evaluation_service import TrackMatchEvaluationService


def create_matching_config() -> DomainMatchingConfig:
    """Create domain MatchingConfig from application settings.

    Bridges the Pydantic settings layer to the domain value object,
    keeping domain code free of config imports. Uses model_dump() so
    adding a field to one side but not the other fails loudly (TypeError).
    """
    return DomainMatchingConfig(**settings.matching.model_dump())


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
