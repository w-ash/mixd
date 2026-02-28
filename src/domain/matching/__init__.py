"""Track matching algorithms and types for cross-service music identification."""

from .algorithms import (
    calculate_confidence,
    calculate_title_similarity,
)
from .protocols import MatchProvider
from .types import ConfidenceEvidence, MatchResult, MatchResultsById

__all__ = [
    "ConfidenceEvidence",
    "MatchProvider",
    "MatchResult",
    "MatchResultsById",
    "calculate_confidence",
    "calculate_title_similarity",
]
