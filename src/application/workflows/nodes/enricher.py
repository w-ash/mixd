"""Enricher nodes that augment tracks with live data from external services.

Unlike the generic enricher pipeline (node_factories.create_enricher_node) which
routes through MetricsApplicationService for numeric metrics, these nodes handle
non-metric enrichment — boolean flags, status checks, etc. — that require direct
connector access and custom persistence logic.
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from src.application.connector_protocols import LibraryContainsConnector
from src.application.workflows.protocols import NodeResult
from src.config import get_logger
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import TrackList
from src.domain.repositories.uow import UnitOfWorkProtocol

from .execution_context import NodeContext

logger = get_logger(__name__)


async def enrich_spotify_liked_status(
    context: dict[str, object],
    _config: Mapping[str, JsonValue],
) -> NodeResult:
    """Check which tracks are saved in the user's Spotify library.

    Calls Spotify's /me/library/contains endpoint for live liked status,
    then updates both in-memory track metadata (for downstream filters)
    and the canonical track_likes table (for DB consistency).

    Args:
        context: Workflow execution context with tracklist and connectors.
        _config: Currently unused; reserved for future options. The engine
            calls node functions positionally, so the slot must remain.

    Returns:
        NodeResult with tracks annotated with is_liked metadata.
    """
    ctx = NodeContext(context)
    tracklist = ctx.extract_tracklist()
    connector = ctx.get_connector(
        "spotify", capability="library_contains", protocol=LibraryContainsConnector
    )
    workflow_context = ctx.extract_workflow_context()

    await ctx.emit_phase_progress("enrich", "enricher", "Checking Spotify saved status")

    # Build URI → track index map for reverse lookup
    uri_to_idx: dict[str, int] = {}
    for i, track in enumerate(tracklist.tracks):
        spotify_id = track.connector_track_identifiers.get("spotify")
        if spotify_id:
            uri_to_idx[f"spotify:track:{spotify_id}"] = i

    if not uri_to_idx:
        logger.warning(
            "No tracks have Spotify identifiers — skipping liked status check"
        )
        return {"tracklist": tracklist}

    logger.info(f"Checking saved status for {len(uri_to_idx)} tracks on Spotify")

    # 1. Check saved status via Spotify API
    saved_status = await connector.check_library_contains(list(uri_to_idx))

    # 2. Update in-memory metadata + build persistence batches in one pass.
    # Likes are presence rows: a saved track gets a row, an unsaved one loses it.
    updated_tracks = list(tracklist.tracks)
    likes_to_save: list[tuple[UUID, str, datetime | None]] = []
    likes_to_delete: list[tuple[UUID, str]] = []
    for uri, is_saved in saved_status.items():
        idx = uri_to_idx[uri]
        updated_tracks[idx] = updated_tracks[idx].with_connector_metadata(
            "spotify", {"is_liked": is_saved}
        )
        track = updated_tracks[idx]
        if is_saved:
            likes_to_save.append((track.id, "spotify", None))
        else:
            likes_to_delete.append((track.id, "spotify"))

    if likes_to_save or likes_to_delete:
        user_id = workflow_context.user_id

        async def _persist_likes(uow: UnitOfWorkProtocol) -> None:
            like_repo = uow.get_like_repository()
            if likes_to_save:
                await like_repo.save_track_likes_batch(likes_to_save, user_id=user_id)
            if likes_to_delete:
                await like_repo.delete_track_likes_batch(
                    likes_to_delete, user_id=user_id
                )
            await uow.commit()

        await workflow_context.execute_service(_persist_likes)
        logger.info(
            "Persisted track like statuses to database",
            saved=len(likes_to_save),
            removed=len(likes_to_delete),
        )

    logger.info(
        "Liked status enrichment complete",
        liked=sum(1 for v in saved_status.values() if v),
        total=len(saved_status),
    )

    return {"tracklist": TrackList(tracks=updated_tracks, metadata=tracklist.metadata)}
