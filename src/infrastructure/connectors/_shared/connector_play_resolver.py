"""The play-resolution loop every connector play resolver shares.

A connector's own work is deciding which canonical track a play names. What
follows — building ``TrackPlay`` rows, recording unresolved plays as exclusions,
and tallying the metrics the importer reports — is identical across connectors,
and was written out per connector until this module.
"""

from collections.abc import Callable, Sequence
from uuid import UUID

from src.config import get_logger
from src.domain.entities import (
    ConnectorTrackPlay,
    PlayExclusionReason,
    Track,
    TrackPlay,
)
from src.domain.matching.play_projection import build_play_context
from src.domain.repositories.play import PlayResolutionOutcome, ResolutionMetrics
from src.infrastructure.connectors._shared.inward_track_resolver import (
    TrackResolutionMetrics,
)

logger = get_logger(__name__)


def empty_play_metrics(extra: ResolutionMetrics | None = None) -> ResolutionMetrics:
    """Metrics for a resolver that was handed nothing to do."""
    metrics: ResolutionMetrics = {
        "raw_plays": 0,
        "accepted_plays": 0,
        "error_count": 0,
        "resolution_failures": [],
        "new_tracks_count": 0,
        "updated_tracks_count": 0,
    }
    metrics.update(extra or {})
    return metrics


def build_play_outcome(
    resolved: Sequence[tuple[ConnectorTrackPlay, Track | None]],
    *,
    service: str,
    user_id: str,
    default_import_source: str,
    resolution_metrics: TrackResolutionMetrics,
    failure_detail: Callable[[ConnectorTrackPlay], dict[str, str]] | None = None,
    extra_metrics: ResolutionMetrics | None = None,
) -> PlayResolutionOutcome:
    """Turn resolved (play, track) pairs into the outcome the importer records.

    A pair whose track is ``None`` is an unresolved play: it is excluded rather
    than dropped, so the ledger keeps the row with ``resolved_track_id = NULL``
    and a later re-resolution pass can promote it.

    ``failure_detail`` adds connector-specific keys to a failure record (Apple
    names the catalog song id); ``extra_metrics`` adds connector-specific tallies.
    """
    track_plays: list[TrackPlay] = []
    resolutions: list[tuple[ConnectorTrackPlay, UUID]] = []
    exclusions: list[tuple[ConnectorTrackPlay, PlayExclusionReason]] = []
    failures: list[dict[str, str]] = []
    accepted = 0

    for connector_play, track in resolved:
        label = f"{connector_play.artist_name} - {connector_play.track_name}"
        if track is None:
            failure: dict[str, str] = {
                "track": label,
                "reason": "track_resolution_failed",
            }
            if failure_detail is not None:
                failure.update(failure_detail(connector_play))
            failures.append(failure)
            logger.warning(f"Track not resolved: {label}")
            exclusions.append((connector_play, "unresolved"))
            continue

        accepted += 1
        resolutions.append((connector_play, track.id))
        track_plays.append(
            TrackPlay(
                track_id=track.id,
                service=service,
                played_at=connector_play.played_at,
                user_id=user_id,
                ms_played=connector_play.ms_played,
                context=build_play_context(connector_play),
                import_timestamp=connector_play.import_timestamp,
                import_source=connector_play.import_source or default_import_source,
                import_batch_id=connector_play.import_batch_id,
            )
        )

    metrics: ResolutionMetrics = {
        "raw_plays": len(resolved),
        "accepted_plays": accepted,
        "error_count": len(failures),
        "resolution_failures": failures,
        "new_tracks_count": resolution_metrics.created,
        "updated_tracks_count": resolution_metrics.existing,
    }
    metrics.update(extra_metrics or {})

    logger.info(
        f"Processed {service} connector plays",
        total_plays=len(resolved),
        accepted_plays=accepted,
        error_count=len(failures),
        new_tracks=resolution_metrics.created,
        updated_tracks=resolution_metrics.existing,
    )

    return PlayResolutionOutcome(
        track_plays=track_plays,
        metrics=metrics,
        resolutions=tuple(resolutions),
        exclusions=tuple(exclusions),
    )
