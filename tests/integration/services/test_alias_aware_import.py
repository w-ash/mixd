"""A scrobble under an artist's alias lands on the library's full-name track.

The story's import path against the real alias cache: the orchestrator compiles
the chunk's alias groups, the Last.fm resolver's title/artist probe reaches the
canonical credited "Totally Enormous Extinct Dinosaurs" for a "TEED"
identifier, and the reuse price accepts it.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import sqlalchemy as sa

from src.application.services.play_import_orchestrator import PlayImportOrchestrator
from src.domain.entities import ConnectorTrackPlay
from src.domain.entities.artist import ArtistAlias
from src.domain.entities.progress import NullProgressEmitter
from src.infrastructure.connectors.lastfm.play_resolver import (
    LastfmConnectorPlayResolver,
)
from src.infrastructure.persistence.database.models import (
    DBConnectorPlay,
    DBTrackMapping,
)
from tests.fixtures import make_connector_artist, make_track

TEED = "TEED"
FULL_NAME = "Totally Enormous Extinct Dinosaurs"


async def _cache_aliases(uow) -> None:
    """Cache one MusicBrainz artist under both of its spellings."""
    stored = await uow.get_artist_connector_repository().bulk_upsert_connector_artists(
        "musicbrainz",
        [
            make_connector_artist(
                "c6b4a0e6-8e38-4aa6-9f9d-1b1f1ba1c1a1",
                connector_name="musicbrainz",
                name=TEED,
            )
        ],
    )
    (connector_artist,) = stored.values()
    _ = await uow.get_artist_alias_repository().replace_aliases(
        connector_artist.id,
        [
            ArtistAlias(
                connector_artist_id=connector_artist.id,
                name=name,
                is_primary=name == TEED,
                fetched_at=datetime.now(UTC),
            )
            for name in (TEED, FULL_NAME)
        ],
    )


def _scrobble(user_id: str) -> ConnectorTrackPlay:
    """One Last.fm scrobble of "Garden", credited the way Last.fm states it."""
    return ConnectorTrackPlay(
        service="lastfm",
        artist_name=TEED,
        track_name="Garden",
        played_at=datetime(2024, 5, 4, 20, 15, tzinfo=UTC),
        ms_played=None,
        user_id=user_id,
        service_metadata={"loved": False},
        import_timestamp=datetime.now(UTC),
        import_source="lastfm_api",
        import_batch_id=f"TEST_{uuid4()}",
    )


def _unanswering_client() -> AsyncMock:
    """Last.fm client double with no enrichment on file for any track."""
    client = AsyncMock()
    client.get_track_info_comprehensive.return_value = None
    client.get_track_correction.return_value = None
    return client


class TestAliasAwareImport:
    async def test_teed_scrobble_lands_on_the_full_name_canonical_as_one_artist(
        self, db_session, real_unit_of_work, real_track_repository
    ):
        """Catches an inward path that skips the alias groups.

        Before the fix it probed by the normalized artist only, so the "TEED"
        scrobble minted a second canonical instead of landing on the
        library's track. A price that skips them records the reuse as an
        artist disagreement; an alias hit is an exact artist agreement.
        """
        user_id = f"TEST_alias_import_{uuid4().hex[:8]}"
        await _cache_aliases(real_unit_of_work)
        canonical = await real_track_repository.save_track(
            make_track(title="Garden", artist=FULL_NAME, user_id=user_id)
        )
        play = _scrobble(user_id)
        _ = await real_unit_of_work.get_connector_play_repository().bulk_insert_connector_plays([
            play
        ])

        async def resolver_factory(_service: str) -> LastfmConnectorPlayResolver:
            return LastfmConnectorPlayResolver(lastfm_client=_unanswering_client())

        _ = await PlayImportOrchestrator(
            resolver_factory=resolver_factory
        ).execute_resolution_phase(
            [play],
            real_unit_of_work,
            user_id=user_id,
            progress_emitter=NullProgressEmitter(),
        )

        resolved = (
            (
                await db_session.execute(
                    sa.select(DBConnectorPlay.resolved_track_id).where(
                        DBConnectorPlay.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert resolved == [canonical.id]
        evidence = (
            (
                await db_session.execute(
                    sa.select(DBTrackMapping.confidence_evidence).where(
                        DBTrackMapping.track_id == canonical.id,
                        DBTrackMapping.connector_name == "lastfm",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [row["artist_similarity"] for row in evidence if row] == [1.0]
