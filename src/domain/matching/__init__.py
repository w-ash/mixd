"""Track matching algorithms and types for cross-service music identification."""

from .algorithms import (
    calculate_confidence,
    calculate_title_similarity,
)
from .config import MatchingConfig
from .protocols import MatchProvider
from .types import ConfidenceEvidence, MatchResult, MatchResultsById

__all__ = [
    "ConfidenceEvidence",
    "MatchProvider",
    "MatchResult",
    "MatchResultsById",
    "MatchingConfig",
    "calculate_confidence",
    "calculate_title_similarity",
]
