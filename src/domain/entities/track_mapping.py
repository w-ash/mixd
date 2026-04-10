"""Track-to-connector mapping domain entity.

Typed frozen entity for track mapping data flowing between the persistence
layer and domain/application layers.
"""

from uuid import UUID, uuid7

from attrs import define, field


@define(frozen=True, slots=True)
class TrackMapping:
    """Maps a canonical track to an external connector track with confidence scoring.

    Represents the relationship between an internal track and its corresponding
    external service track (Spotify, Last.fm, etc.) with metadata about how
    the match was determined and its reliability.

    ``confidence_evidence`` uses ``dict[str, object]`` rather than ``JsonDict``
    because application-layer producers (matching pipeline, manual overrides)
    construct evidence dicts with mixed types (UUID, datetime) that the
    persistence layer serialises at the JSONB boundary.
    """

    user_id: str = "default"
    track_id: UUID = field(factory=uuid7)
    connector_track_id: UUID = field(factory=uuid7)
    connector_name: str = ""
    match_method: str = ""
    confidence: int = 0
    confidence_evidence: dict[str, object] | None = None
    origin: str = "automatic"
    is_primary: bool = False
    id: UUID = field(factory=uuid7)
