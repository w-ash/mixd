"""Pure domain types for track matching and confidence scoring.

These types represent the core concepts in our matching domain with zero external dependencies.
"""

from typing import Any, TypedDict

from attrs import define, field


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

    def as_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage in track_mappings.confidence_evidence."""
        return {
            "base_score": self.base_score,
            "title_score": round(self.title_score, 2),
            "artist_score": round(self.artist_score, 2),
            "duration_score": round(self.duration_score, 2),
            "title_similarity": round(self.title_similarity, 2),
            "artist_similarity": round(self.artist_similarity, 2),
            "duration_diff_ms": self.duration_diff_ms,
            "final_score": self.final_score,
        }


# Type aliases for clarity
TracksById = dict[int, Any]  # Track type will be imported later
MatchResultsById = dict[int, "MatchResult"]


@define(frozen=True, slots=True)
class MatchResult:
    """Result of track identity resolution with clean separation of concerns.

    This class represents a match between an internal track and an external service,
    containing both the match assessment and service-specific data.

    - Match assessment: Stored in track_mappings (confidence, method, evidence)
    - Service data: Stored in connector_tracks.raw_metadata
    """

    track: Any  # Track type will be imported later to avoid circular dependencies
    success: bool
    connector_id: str = ""  # ID in the target system
    confidence: int = 0
    match_method: str = ""  # "isrc", "mbid", "artist_title"
    service_data: dict[str, Any] = field(factory=dict)  # Data from external service
    evidence: ConfidenceEvidence | None = None  # Evidence for confidence calculation
