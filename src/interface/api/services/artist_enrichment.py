"""The one launcher behind ``POST /artists/enrich`` and the chat's ``enrich_artists``."""

from src.interface.api.schemas.imports import OperationStartedResponse
from src.interface.api.services.progress import OperationBoundEmitter
from src.interface.api.services.sse_operations import launch_sse_operation


async def launch_artist_enrichment(
    *,
    user_id: str,
    limit: int | None,
    refresh_older_than_days: int,
    initiated_by: str = "manual",
) -> OperationStartedResponse:
    """Resolve artist identity against MusicBrainz as a background SSE operation."""

    async def _enrich(emitter: OperationBoundEmitter) -> object:
        from src.application.use_cases.enrich_artists import run_enrich_artists

        enriched = await run_enrich_artists(
            user_id=user_id,
            limit=limit,
            refresh_older_than_days=refresh_older_than_days,
            progress_emitter=emitter,
        )
        # Unwrap to the OperationResult so the audit path records counts.
        return enriched.result

    return await launch_sse_operation(
        user_id=user_id,
        operation_type="artist_enrichment",
        coro_factory=_enrich,
        initiated_by=initiated_by,
    )


__all__ = ["launch_artist_enrichment"]
