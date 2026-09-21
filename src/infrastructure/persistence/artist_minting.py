"""The import-path artist minting walk: connector ids → canonical artists.

Every connector track ingested brings positional ``artist_ids`` in its raw
metadata. This walk turns them into ``connector_artists`` rows, canonical
``artists`` where none holds the id yet, the mappings that say so, and the
``track_artists.artist_id`` fills on the credits that named them. Same
transaction as the track write, zero network, and every decision the
domain's (``domain.matching.artist_resolution``): the minter does the
repository calls in the order ``ArtistWrites`` lists them.

One home for both callers — the application ``ArtistResolutionService`` and
the connector inward resolvers — reached through ``uow.get_artist_minter()``.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from attrs import define

from src.domain.entities import Track
from src.domain.matching.artist_resolution import (
    ArtistCreditSource,
    artist_writes,
    credited_artists,
    plan_artist_resolution,
)
from src.domain.matching.config import MatchingConfig
from src.domain.repositories.artist import ArtistMintSummary
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ArtistMinter:
    """Mint canonical artists from connector ids through the unit of work's repositories."""

    uow: UnitOfWorkProtocol

    async def mint(
        self,
        connector: str,
        sources: Sequence[ArtistCreditSource],
        canonicals: Mapping[str, Track],
        *,
        user_id: str,
        config: MatchingConfig,
    ) -> ArtistMintSummary:
        """Upsert connector records, probe owners, plan, and persist the writes.

        Order matters and is the domain's: connector rows first (their ids
        are the strong ids), then the owner probe, the planner, the new
        artists, their mappings (with primaries elected and the accept
        events recorded), the credit fills, and last the freshness touch on
        every artist reused. Runs inside the caller's transaction.
        """
        intake = credited_artists(connector, sources)
        if not intake.connector_artists:
            return ArtistMintSummary()

        artist_connectors = self.uow.get_artist_connector_repository()
        stored = await artist_connectors.bulk_upsert_connector_artists(
            connector, list(intake.connector_artists)
        )
        upserted = len(stored)
        described = intake.described(stored)
        if not described:
            return ArtistMintSummary(connector_artists_upserted=upserted)

        owners = await artist_connectors.find_artists_by_connector_artist_ids(
            [stored[item.key].id for item in described], user_id=user_id
        )
        plan = plan_artist_resolution(
            described,
            strong_owners={str(row_id): artist for row_id, artist in owners.items()},
            config=config,
        )
        writes = artist_writes(
            plan,
            intake,
            stored,
            canonicals,
            connector=connector,
            user_id=user_id,
            now=datetime.now(UTC),
        )

        artist_repo = self.uow.get_artist_repository()
        if writes.artists:
            _ = await artist_repo.save_artists(list(writes.artists))
        if writes.mapping_rows:
            assertion = await artist_connectors.assert_mappings(
                list(writes.mapping_rows)
            )
            # The rows ask for primacy on insert; a row that landed on an
            # already-mapped key was rewritten without it, and the fill
            # promotes into exactly that vacancy.
            _ = await artist_connectors.ensure_primaries(
                list(writes.primaries), mode="fill"
            )
            await artist_connectors.record_assertion(assertion)
        assigned = 0
        if writes.assignments:
            assigned = await self.uow.get_track_repository().set_credit_artist_ids(
                list(writes.assignments), user_id=user_id
            )
        if writes.reused:
            await artist_repo.touch(list(writes.reused), user_id=user_id)
        if owners:
            await artist_connectors.touch_last_seen(
                connector, list(owners), user_id=user_id
            )
        return ArtistMintSummary(
            connector_artists_upserted=upserted,
            artists_created=len(writes.artists),
            artists_reused=len(writes.reused),
            credits_assigned=assigned,
        )


__all__ = ["ArtistMinter"]
