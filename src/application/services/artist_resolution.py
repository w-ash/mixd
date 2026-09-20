"""Mint canonical artists from the ids a connector payload already carries.

The import-path half of artist identity: every connector track ingested
brings positional ``artist_ids`` in its raw metadata, and this service turns
them into ``connector_artists`` rows, canonical ``artists`` where none holds
the id yet, the mappings that say so, and the ``track_artists.artist_id``
fills on the credits that named them. Same transaction as the track write,
zero network — an import never waits on MusicBrainz — and every decision is
the domain's (``domain.matching.artist_resolution``): this class does the
repository calls in the order the domain's ``ArtistWrites`` lists them.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from attrs import Factory, define

from src.config import create_matching_config
from src.domain.entities import ConnectorTrack, Track
from src.domain.matching.artist_resolution import (
    ArtistCreditSource,
    artist_writes,
    credit_source,
    credited_artists,
    mapping_seam_of,
    plan_artist_resolution,
)
from src.domain.matching.config import MatchingConfig
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ArtistIngestSummary:
    """What one minting pass wrote, for the caller's log line."""

    connector_artists_upserted: int = 0
    artists_created: int = 0
    artists_reused: int = 0
    credits_assigned: int = 0

    @property
    def empty(self) -> bool:
        return self == ArtistIngestSummary()


@define(frozen=True, slots=True)
class ArtistResolutionService:
    """Resolve credited artists to canonical artists from connector ids.

    ``evaluator_config`` prices every decision through the artist planner —
    injected so a test can pin thresholds without reaching into settings.
    """

    evaluator_config: MatchingConfig = Factory(create_matching_config)

    async def ingest(
        self,
        connector: str,
        tracks: Sequence[ConnectorTrack],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        canonicals: Mapping[str, Track],
    ) -> ArtistIngestSummary:
        """Mint from a batch of connector payloads.

        ``canonicals`` maps each payload's connector track identifier to the
        canonical track it resolved to, so the credits that named an artist
        can be filled on it. Runs inside the caller's transaction; the
        caller commits.
        """
        return await self.mint(
            connector,
            [
                credit_source(
                    track.connector_track_identifier, track.artists, track.raw_metadata
                )
                for track in tracks
            ],
            uow,
            user_id=user_id,
            canonicals=canonicals,
        )

    async def mint(
        self,
        connector: str,
        sources: Sequence[ArtistCreditSource],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        canonicals: Mapping[str, Track],
    ) -> ArtistIngestSummary:
        """Mint from credit sources: upsert records, probe owners, plan, persist.

        Order matters and is the domain's: connector rows first (their ids
        are the strong ids), then the owner probe, the planner, the new
        artists, their mappings (with primaries elected and the accept
        events recorded), the credit fills, and last the freshness touch on
        every artist reused.
        """
        intake = credited_artists(connector, sources)
        if not intake.connector_artists:
            return ArtistIngestSummary()

        artist_connectors = uow.get_artist_connector_repository()
        stored = await artist_connectors.bulk_upsert_connector_artists(
            connector, list(intake.connector_artists)
        )
        upserted = len(stored)
        described = intake.described(stored)
        if not described:
            return ArtistIngestSummary(connector_artists_upserted=upserted)

        owners = await artist_connectors.find_artists_by_connector_artist_ids(
            [stored[item.key].id for item in described], user_id=user_id
        )
        plan = plan_artist_resolution(
            described,
            strong_owners={str(row_id): artist for row_id, artist in owners.items()},
            config=self.evaluator_config,
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

        artist_repo = uow.get_artist_repository()
        if writes.artists:
            _ = await artist_repo.save_artists(list(writes.artists))
        if writes.mapping_rows:
            seam = mapping_seam_of(artist_connectors)
            assertion = await seam.assert_mappings(list(writes.mapping_rows))
            # The rows ask for primacy on insert; a row that landed on an
            # already-mapped key was rewritten without it, and the fill
            # promotes into exactly that vacancy.
            _ = await artist_connectors.ensure_primaries(
                list(writes.primaries), mode="fill"
            )
            await seam.record_assertion(assertion)
        assigned = 0
        if writes.assignments:
            assigned = await uow.get_track_repository().set_credit_artist_ids(
                list(writes.assignments), user_id=user_id
            )
        if writes.reused:
            await artist_repo.touch(list(writes.reused), user_id=user_id)
        if owners:
            await artist_connectors.touch_last_seen(
                connector, list(owners), user_id=user_id
            )
        return ArtistIngestSummary(
            connector_artists_upserted=upserted,
            artists_created=len(writes.artists),
            artists_reused=len(writes.reused),
            credits_assigned=assigned,
        )


__all__ = ["ArtistIngestSummary", "ArtistResolutionService"]
