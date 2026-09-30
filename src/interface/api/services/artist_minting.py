"""The one launcher behind ``POST /artists/mint`` and the chat's ``mint_artists``."""

from src.interface.api.schemas.imports import OperationStartedResponse
from src.interface.api.services.progress import OperationBoundEmitter
from src.interface.api.services.sse_operations import launch_sse_operation


async def launch_artist_minting(
    *,
    user_id: str,
    limit: int | None,
    dry_run: bool = False,
    initiated_by: str = "manual",
) -> OperationStartedResponse:
    """Mint canonical artists from the stored connector credits, in the background."""

    async def _mint(emitter: OperationBoundEmitter) -> object:
        from src.application.use_cases.mint_artists import run_mint_artists

        minted = await run_mint_artists(
            user_id=user_id,
            limit=limit,
            dry_run=dry_run,
            progress_emitter=emitter,
        )
        # Unwrap to the OperationResult so the audit path records counts.
        return minted.result

    return await launch_sse_operation(
        user_id=user_id,
        operation_type="artist_minting",
        coro_factory=_mint,
        initiated_by=initiated_by,
    )


__all__ = ["launch_artist_minting"]
