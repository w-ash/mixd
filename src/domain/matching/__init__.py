"""Track matching algorithms and types for cross-service music identification."""

from .algorithms import (
    calculate_confidence,
    calculate_title_similarity,
)
from .protocols import MatchingService, TrackData
from .types import ConfidenceEvidence, MatchResult, MatchResultsById, TracksById

__all__ = [
    "ConfidenceEvidence",
    "MatchResult",
    "MatchResultsById",
    "MatchingService",
    "TrackData",
    "TracksById",
    "calculate_confidence",
    "calculate_title_similarity",
]
