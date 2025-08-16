"""Narada domain layer - pure business logic with zero external dependencies."""

# Export all domain components
from . import entities, matching, transforms

# Re-export key types for convenience
from .entities import (
    Artist,
    OperationResult,
    Playlist,
    PlayRecord,
    SyncCheckpoint,
    Track,
    TrackList,
    TrackPlay,
    WorkflowResult,
)
from .matching import (
    ConfidenceEvidence,
    MatchResult,
    calculate_confidence,
    calculate_title_similarity,
)

# TrackMatchingService has been replaced by TrackMatchEvaluationService in matching module
from .transforms import (
    Transform,
    concatenate,
    create_pipeline,
    filter_by_predicate,
    filter_duplicates,
    limit,
    rename,
    sort_by_attribute,
)

__all__ = [
    "Artist",
    "ConfidenceEvidence",
    "MatchResult",
    "OperationResult",
    "PlayRecord",
    "Playlist",
    "SyncCheckpoint",
    "Track",
    "TrackList",
    "TrackPlay",
    "Transform",
    "WorkflowResult",
    "calculate_confidence",
    "calculate_title_similarity",
    "concatenate",
    "create_pipeline",
    "entities",
    "filter_by_predicate",
    "filter_duplicates",
    "limit",
    "matching",
    "rename",
    "sort_by_attribute",
    "transforms",
]
