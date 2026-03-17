"""Track matching algorithms and types for cross-service music identification."""

from .algorithms import (
    SimilarityResult,
    calculate_confidence,
    calculate_title_similarity,
    select_best_by_title_similarity,
)
from .config import MatchingConfig
from .protocols import CrossDiscoveryProvider, MatchProvider
from .text_normalization import normalize_for_comparison, strip_parentheticals
from .types import (
    ConfidenceEvidence,
    EvaluationResult,
    MatchResult,
    MatchResultsById,
)

__all__ = [
    "ConfidenceEvidence",
    "CrossDiscoveryProvider",
    "EvaluationResult",
    "MatchProvider",
    "MatchResult",
    "MatchResultsById",
    "MatchingConfig",
    "SimilarityResult",
    "calculate_confidence",
    "calculate_title_similarity",
    "normalize_for_comparison",
    "select_best_by_title_similarity",
    "strip_parentheticals",
]
