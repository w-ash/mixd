"""Mint canonical artists from the connector credits the library already stores.

An import mints artists from the ids its payload carries, so a track minted
before v0.12.1 — or backfilled into ``track_artists`` by migration 061, which
writes every credit with ``artist_id = NULL`` — has credits no canonical
artist owns. The minter only runs when an import re-encounters the track, and
a library nobody re-imports is never re-encountered: its ``connector_artists``
rows and the service ids on its ``connector_track_artists`` rows sit unused
and ``/artists`` stays empty.

This operation is that catch-up pass. It reads the stored payloads back —
never the network, and never MusicBrainz — and hands each page's credits to
the same ``uow.get_artist_minter()`` walk the import path uses, so every
identity decision is made in exactly one place. Artist *identity* against
MusicBrainz is the separate, slow half (``enrich_artists``); this one is only
as slow as the database.

Resumable by construction: a minted credit stops being NULL, so it leaves the
candidate query and a restart pages only what is left. A credit the minter
cannot fill — a Various Artists sentinel, a credited name no payload spelling
matches — stays a candidate and is re-read by every later run, which costs one
page read and writes nothing.
"""

from collections.abc import Sequence
from typing import Final
from uuid import UUID

from attrs import define

from src.application.use_cases._shared.batch_commit import commit_batch
from src.config import create_matching_config, get_logger
from src.config.constants import BusinessLimits
from src.domain.entities import ConnectorTrack, Track
from src.domain.entities.operations import OperationResult
from src.domain.entities.progress import (
    NullProgressEmitter,
    ProgressEmitter,
    create_progress_event,
    tracked_operation,
)
from src.domain.matching.artist_resolution import ArtistCreditSource
from src.domain.matching.config import MatchingConfig
from src.domain.repositories.artist import ArtistMinterProtocol, ArtistMintSummary
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)

# Tracks per page and per commit. The page is one query and the minting walk a
# handful more, so the batch is wide — unlike enrichment, nothing here waits on
# a rate limit.
_PAGE_SIZE: Final = 500


@define(frozen=True, slots=True)
class MintArtistsCommand:
    """Selectors for one minting pass over the stored credits."""

    user_id: str
    limit: int | None = None
    dry_run: bool = False


@define(frozen=True, slots=True)
class MintArtistsResult:
    """Minting outcome.

    Every count rides in ``result.summary_metrics`` — every caller either
    renders that table or forwards the ``OperationResult`` whole.
    """

    result: OperationResult


@define(slots=True)
class _Tally:
    """Running counters for one pass."""

    tracks: int = 0
    created: int = 0
    reused: int = 0
    linked: int = 0
    # Dry run only: credits that name a service artist record, so the report
    # says what a real run would have to work with.
    available: int = 0

    def add(self, summary: ArtistMintSummary) -> None:
        self.created += summary.artists_created
        self.reused += summary.artists_reused
        self.linked += summary.credits_assigned


@define(slots=True)
class MintArtistsUseCase:
    """Walk the library's unminted credits through the import-path artist minter."""

    # Set only to make a test's library span several pages, never to tune a
    # real run: the page is also the commit window.
    page_size: int = _PAGE_SIZE

    async def execute(
        self,
        command: MintArtistsCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
    ) -> MintArtistsResult:
        """Mint canonical artists for every credit the stored payloads can fill."""
        emitter = progress_emitter or NullProgressEmitter()
        config = create_matching_config()
        tally = _Tally()

        async with uow:
            connectors = uow.get_connector_repository()
            minter = uow.get_artist_minter()

            candidates = await connectors.count_unlinked_credit_sources(
                user_id=command.user_id
            )
            total = min(candidates, command.limit) if command.limit else candidates

            async with tracked_operation(
                emitter,
                "Minting artists from stored connector credits",
                # None, not 0: a fully minted library is the steady state, and a
                # zero total is rejected as a malformed operation.
                total_items=total or None,
            ) as operation_id:
                after: UUID | None = None
                while (page_size := self._next_page_size(command, tally)) > 0:
                    sources = await connectors.list_unlinked_credit_sources(
                        user_id=command.user_id,
                        after_track_id=after,
                        limit=page_size,
                    )
                    if not sources:
                        break
                    # Ordered by ``tracks.id``, so the last pair's track is the
                    # page's high-water mark and the next page starts past it.
                    after = sources[-1][0].id
                    tally.tracks += len({track.id for track, _ in sources})

                    if command.dry_run:
                        tally.available += _identified_credits(sources)
                    else:
                        await self._mint_page(
                            sources, minter, tally, command=command, config=config
                        )
                        await commit_batch(uow)

                    await emitter.emit_progress(
                        create_progress_event(
                            operation_id,
                            current=tally.tracks,
                            total=total or None,
                            message=f"Minted artists for {tally.tracks} track(s)",
                        )
                    )

        logger.info(
            "Artist minting complete",
            user_id=command.user_id,
            dry_run=command.dry_run,
            tracks=tally.tracks,
            created=tally.created,
            reused=tally.reused,
            linked=tally.linked,
        )
        return self._build_result(tally, dry_run=command.dry_run)

    def _next_page_size(self, command: MintArtistsCommand, tally: _Tally) -> int:
        """How many tracks the next page may take — 0 once ``limit`` is spent."""
        if command.limit is None:
            return self.page_size
        return max(0, min(self.page_size, command.limit - tally.tracks))

    async def _mint_page(
        self,
        sources: Sequence[tuple[Track, ConnectorTrack]],
        minter: ArtistMinterProtocol,
        tally: _Tally,
        *,
        command: MintArtistsCommand,
        config: MatchingConfig,
    ) -> None:
        """Hand one page's credits to the minter, one call per connector.

        The minter reads the connector-artist records a single service stored,
        so a page spanning services is one call each. ``canonicals`` is keyed by
        the payload's own identifier: two canonicals live-mapped to the same
        payload can only fill the first, which is the same arbitration the
        import path makes.
        """
        for connector, group in _by_connector(sources).items():
            credits: list[ArtistCreditSource] = []
            canonicals: dict[str, Track] = {}
            for track, payload in group:
                key = payload.connector_track_identifier
                if key in canonicals:
                    continue
                canonicals[key] = track
                credits.append(ArtistCreditSource(key, payload.artists))
            summary = await minter.mint(
                connector,
                credits,
                canonicals,
                user_id=command.user_id,
                config=config,
            )
            tally.add(summary)

    def _build_result(self, tally: _Tally, *, dry_run: bool) -> MintArtistsResult:
        """Render the run's counters as a reportable result."""
        operation_name = "Artist Minting (dry run)" if dry_run else "Artist Minting"
        result = OperationResult(operation_name=operation_name, execution_time=0.0)
        result.metadata["dry_run"] = dry_run
        result.summary_metrics.add(
            "tracks_processed", tally.tracks, "Tracks Processed", significance=0
        )
        result.summary_metrics.add(
            "artists_created", tally.created, "Artists Created", significance=1
        )
        result.summary_metrics.add(
            "artists_reused", tally.reused, "Artists Reused", significance=2
        )
        result.summary_metrics.add(
            "credits_linked", tally.linked, "Credits Linked", significance=3
        )
        if dry_run:
            result.summary_metrics.add(
                "credits_available",
                tally.available,
                "Credits With Service Ids",
                significance=4,
            )
        return MintArtistsResult(result=result)


def _by_connector(
    sources: Sequence[tuple[Track, ConnectorTrack]],
) -> dict[str, list[tuple[Track, ConnectorTrack]]]:
    """A page's pairs grouped by the service whose records they name."""
    grouped: dict[str, list[tuple[Track, ConnectorTrack]]] = {}
    for pair in sources:
        grouped.setdefault(pair[1].connector_name, []).append(pair)
    return grouped


def _identified_credits(sources: Sequence[tuple[Track, ConnectorTrack]]) -> int:
    """Credits across a page that name a service artist record."""
    return sum(
        1
        for _, payload in sources
        for credit in payload.artists
        if credit.connector_artist_identifier is not None
    )


async def run_mint_artists(
    *,
    user_id: str,
    limit: int | None = None,
    dry_run: bool = False,
    progress_emitter: ProgressEmitter | None = None,
) -> MintArtistsResult:
    """Convenience wrapper mirroring ``run_enrich_artists`` for CLI, API and chat."""
    from src.application.runner import execute_use_case

    command = MintArtistsCommand(user_id=user_id, limit=limit, dry_run=dry_run)
    return await execute_use_case(
        lambda uow: MintArtistsUseCase().execute(
            command, uow, progress_emitter=progress_emitter
        ),
        user_id=user_id,
        statement_timeout=BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT,
    )


__all__ = [
    "MintArtistsCommand",
    "MintArtistsResult",
    "MintArtistsUseCase",
    "run_mint_artists",
]
