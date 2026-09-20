"""Track matching algorithms and types for cross-service music identification."""

from .algorithms import (
    SimilarityResult,
    calculate_confidence,
    calculate_title_similarity,
    select_best_by_title_similarity,
)
from .artist_confidence import calculate_artist_confidence
from .artist_enrichment import (
    ArtistAliasRecord,
    ArtistEnrichmentProviderProtocol,
    ArtistLookup,
    ArtistUrlRel,
)
from .artist_equivalence import EMPTY_EQUIVALENCE, ArtistEquivalence
from .config import MatchingConfig
from .protocols import (
    CrossDiscoveryProvider,
    DiscoveryOutcome,
    DiscoveryRequest,
    MatchProvider,
    NewMapping,
    Nothing,
    ReuseExisting,
)
from .text_normalization import normalize_for_comparison, strip_parentheticals
from .types import (
    ArtistEvidence,
    ArtistEvidenceLevel,
    ConfidenceEvidence,
    EvaluationResult,
    MatchResult,
    MatchResultsById,
)

__all__ = [
    "EMPTY_EQUIVALENCE",
    "ArtistAliasRecord",
    "ArtistEnrichmentProviderProtocol",
    "ArtistEquivalence",
    "ArtistEvidence",
    "ArtistEvidenceLevel",
    "ArtistLookup",
    "ArtistUrlRel",
    "ConfidenceEvidence",
    "CrossDiscoveryProvider",
    "DiscoveryOutcome",
    "DiscoveryRequest",
    "EvaluationResult",
    "MatchProvider",
    "MatchResult",
    "MatchResultsById",
    "MatchingConfig",
    "NewMapping",
    "Nothing",
    "ReuseExisting",
    "SimilarityResult",
    "calculate_artist_confidence",
    "calculate_confidence",
    "calculate_title_similarity",
    "normalize_for_comparison",
    "select_best_by_title_similarity",
    "strip_parentheticals",
]
