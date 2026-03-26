"""Match review entity for human disambiguation of medium-confidence matches.

Represents a proposed track-to-connector mapping that fell in the gray zone
between auto-accept and auto-reject, queued for human review.
"""

from datetime import datetime

from attrs import define, field


@define(frozen=True, slots=True)
class MatchReview:
    """A proposed match awaiting human review.

    Created when a match scores between review_threshold and auto_accept_threshold.
    On accept → creates a real TrackMapping. On reject → marked as rejected to
    prevent re-queuing the same pair.
    """

    track_id: int
    connector_name: str
    connector_track_id: int
    match_method: str
    confidence: int
    match_weight: float
    user_id: str = "default"
    confidence_evidence: dict[str, object] | None = None
    status: str = "pending"
    id: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Denormalized fields for display (from connector_tracks table)
    connector_track_title: str = ""
    connector_track_artists: list[str] = field(factory=list)
