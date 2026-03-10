"""Track-to-connector mapping domain entity.

Replaces dict[str, Any] with a typed frozen entity for track mapping data
flowing between the persistence layer and domain/application layers.
"""

from attrs import define


@define(frozen=True, slots=True)
class TrackMapping:
    """Maps a canonical track to an external connector track with confidence scoring.

    Represents the relationship between an internal track and its corresponding
    external service track (Spotify, Last.fm, etc.) with metadata about how
    the match was determined and its reliability.
    """

    track_id: int = 0
    connector_track_id: int = 0
    connector_name: str = ""
    match_method: str = ""
    confidence: int = 0
    confidence_evidence: dict[str, object] | None = None
    origin: str = "automatic"
    is_primary: bool = False
    id: int | None = None
