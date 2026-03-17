"""Pure domain types for track matching and confidence scoring.

These types represent the core concepts in our matching domain with zero external dependencies.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: service_metadata, raw_data dicts, factory patterns

from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, TypedDict

from attrs import define, field

from src.domain.entities.track import Track

# Async progress callback signature used across matching layers.
# Parameters: (completed_count, total, description)
type ProgressCallback = Callable[[int, int, str], Awaitable[None]]


class MatchFailureReason(Enum):
    """Reasons why a track match attempt failed.

    This enum provides structured failure classification to enable intelligent
    handling of different failure types by calling code.
    """

    NO_ISRC = "no_isrc"  # Track missing required ISRC code
    NO_METADATA = "no_metadata"  # Track missing title/artist data
    API_ERROR = "api_error"  # External service API failure
    NO_RESULTS = "no_results"  # Service found no matching tracks
    INVALID_RESPONSE = "invalid_response"  # Service returned malformed data
    RATE_LIMITED = "rate_limited"  # Service rate limiting active
    AUTH_ERROR = "auth_error"  # Service authentication failed


@define(frozen=True, slots=True)
class MatchFailure:
    """Details of a track match failure.

    This class captures structured information about why a match attempt failed,
    enabling intelligent handling and comprehensive logging.
    """

    track_id: int  # ID of the track that failed to match
    reason: MatchFailureReason  # Structured failure reason
    service: str  # Name of the external service ("spotify", "musicbrainz", "lastfm")
    method: str  # Match method attempted ("isrc", "artist_title", "mbid")
    details: str = ""  # Human-readable details about the failure
    exception_type: str = ""  # Exception class name for API errors


class RawProviderMatch(TypedDict):
    """Raw match data from external service providers.

    This structure contains the raw service data without any business logic applied.
    Infrastructure providers return this format, which is then processed by domain services.
    """

    connector_id: str  # External service ID (e.g., Spotify track ID)
    match_method: str  # How the match was found ("isrc", "artist_title", "mbid")
    service_data: dict[str, Any]  # Raw data from external service


@define(frozen=True, slots=True)
class ConfidenceEvidence:
    """Evidence used to calculate the confidence score.

    This class captures the details of how a confidence score was calculated,
    including similarity scores for different attributes and penalties applied.

    This is internal matching information that should be stored in
    track_mappings.confidence_evidence, never in connector_tracks.raw_metadata.
    """

    base_score: int
    title_score: float = 0.0
    artist_score: float = 0.0
    duration_score: float = 0.0
    title_similarity: float = 0.0
    artist_similarity: float = 0.0
    duration_diff_ms: int = 0
    final_score: int = 0
    isrc_suspect: bool = False
    match_weight: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage in track_mappings.confidence_evidence."""
        result: dict[str, Any] = {
            "base_score": self.base_score,
            "title_score": round(self.title_score, 2),
            "artist_score": round(self.artist_score, 2),
            "duration_score": round(self.duration_score, 2),
            "title_similarity": round(self.title_similarity, 2),
            "artist_similarity": round(self.artist_similarity, 2),
            "duration_diff_ms": self.duration_diff_ms,
            "final_score": self.final_score,
        }
        if self.isrc_suspect:
            result["isrc_suspect"] = True
        if self.match_weight != 0.0:
            result["match_weight"] = round(self.match_weight, 4)
        return result


@define(frozen=True, slots=True)
class MatchResult:
    """Result of track identity resolution with clean separation of concerns.

    This class represents a match between an internal track and an external service,
    containing both the match assessment and service-specific data.

    - Match assessment: Stored in track_mappings (confidence, method, evidence)
    - Service data: Stored in connector_tracks.raw_metadata
    """

    track: Track
    success: bool
    review_required: bool = False
    connector_id: str = ""  # ID in the target system
    confidence: int = 0
    match_method: str = ""  # "isrc", "mbid", "artist_title"
    service_data: dict[str, Any] = field(factory=dict)  # Data from external service
    evidence: ConfidenceEvidence | None = None  # Evidence for confidence calculation

    @property
    def evidence_dict(self) -> dict[str, Any] | None:
        """Serialize evidence for DB storage, returning None when absent."""
        return self.evidence.as_dict() if self.evidence else None


# Type alias for match results by ID (defined after MatchResult class)
MatchResultsById = dict[int, MatchResult]


@define(frozen=True, slots=True)
class EvaluationResult:
    """Result of evaluating raw matches — separates accepted and review-required matches."""

    accepted: MatchResultsById = field(factory=dict)
    review_candidates: MatchResultsById = field(factory=dict)


@define(frozen=True, slots=True)
class ProviderMatchResult:
    """Result of provider match attempt including both successes and failures.

    This replaces the simple dict return type from providers to capture both
    successful matches and structured failure information.
    """

    matches: dict[int, RawProviderMatch] = field(factory=dict)  # Successful matches
    failures: list[MatchFailure] = field(factory=list)  # Failed match attempts

    @property
    def total_attempts(self) -> int:
        """Total number of match attempts (successes + failures)."""
        return len(self.matches) + len(self.failures)

    @property
    def success_rate(self) -> float:
        """Success rate as a float between 0.0 and 1.0."""
        if self.total_attempts == 0:
            return 0.0
        return len(self.matches) / self.total_attempts
