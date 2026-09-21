"""Mint canonical artists from the ids a connector payload already carries.

The import-path half of artist identity, as the application sees it: every
connector track ingested brings positional ``artist_ids`` in its raw
metadata, and this service reads them into credit sources and hands them to
the unit of work's artist minter, which does the walk. Same transaction as
the track write, zero network — an import never waits on MusicBrainz.
"""

from collections.abc import Mapping, Sequence

from attrs import Factory, define

from src.config import create_matching_config
from src.domain.entities import ConnectorTrack, Track
from src.domain.matching.artist_resolution import ArtistCreditSource, credit_source
from src.domain.matching.config import MatchingConfig
from src.domain.repositories.artist import ArtistMintSummary
from src.domain.repositories.uow import UnitOfWorkProtocol


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
    ) -> ArtistMintSummary:
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
    ) -> ArtistMintSummary:
        """Mint from credit sources through the unit of work's artist minter."""
        return await uow.get_artist_minter().mint(
            connector,
            sources,
            canonicals,
            user_id=user_id,
            config=self.evaluator_config,
        )


__all__ = ["ArtistResolutionService"]
