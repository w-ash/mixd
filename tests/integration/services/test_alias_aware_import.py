"""A scrobble under an artist's alias lands on the library's full-name track.

The story's import path against the real alias cache: the Last.fm resolver's
reuse step compiles the batch's alias groups, its title/artist probe reaches
the canonical credited "Totally Enormous Extinct Dinosaurs" for a "TEED"
identifier, and the reuse price accepts it.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import sqlalchemy as sa

from src.application.services.play_import_orchestrator import PlayImportOrchestrator
from src.domain.entities import ConnectorTrackPlay
from src.domain.entities.progress import NullProgressEmitter
from src.infrastructure.connectors.lastfm.inward_resolver import LastfmInwardResolver
from src.infrastructure.connectors.lastfm.play_resolver import (
    LastfmConnectorPlayResolver,
)
from src.infrastructure.persistence.database.models import (
    DBConnectorPlay,
    DBTrackMapping,
)
from tests.fixtures import FULL_NAME, TEED, make_track, seed_aliases


def _scrobble(user_id: str, track_name: str = "Garden") -> ConnectorTrackPlay:
    """One Last.fm scrobble, credited "TEED" the way Last.fm states it."""
    return ConnectorTrackPlay(
        service="lastfm",
        artist_name=TEED,
        track_name=track_name,
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

        A probe by the scrobble's own spelling misses the full-name track, so
        the "TEED" scrobble mints a second canonical. A price that skips the
        groups records the reuse as an artist disagreement; an alias hit is an
        exact artist agreement.
        """
        user_id = f"TEST_alias_import_{uuid4().hex[:8]}"
        await seed_aliases(real_unit_of_work, TEED, FULL_NAME)
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

    async def test_creation_prices_an_alias_the_way_reuse_does(
        self, db_session, real_unit_of_work, real_track_repository
    ):
        """Catches a pass that prices one artist pair two ways.

        "Garden" reuses the library's full-name track. "Rouge" is new, and
        Last.fm corrects its artist to the full name, so its raw "TEED"
        mapping is priced against a full-name track as well. A creation step
        that skips the alias groups records that mapping as an artist
        disagreement while the reuse step records an agreement.
        """
        user_id = f"TEST_alias_import_{uuid4().hex[:8]}"
        await seed_aliases(real_unit_of_work, TEED, FULL_NAME)
        _ = await real_track_repository.save_track(
            make_track(title="Garden", artist=FULL_NAME, user_id=user_id)
        )
        plays = [_scrobble(user_id), _scrobble(user_id, "Rouge")]
        _ = await real_unit_of_work.get_connector_play_repository().bulk_insert_connector_plays(
            plays
        )
        client = _unanswering_client()

        async def correct(_artist: str, track: str) -> tuple[str, str] | None:
            return ("Rouge", FULL_NAME) if track == "rouge" else None

        client.get_track_correction.side_effect = correct

        async def resolver_factory(_service: str) -> LastfmConnectorPlayResolver:
            return LastfmConnectorPlayResolver(lastfm_client=client)

        _ = await PlayImportOrchestrator(
            resolver_factory=resolver_factory
        ).execute_resolution_phase(
            plays,
            real_unit_of_work,
            user_id=user_id,
            progress_emitter=NullProgressEmitter(),
        )

        rows = (
            await db_session.execute(
                sa.select(
                    DBTrackMapping.match_method, DBTrackMapping.confidence_evidence
                ).where(
                    DBTrackMapping.user_id == user_id,
                    DBTrackMapping.connector_name == "lastfm",
                )
            )
        ).all()
        assert {
            method: (evidence or {}).get("artist_similarity")
            for method, evidence in rows
        } == {
            "canonical_reuse": 1.0,
            "lastfm_import": 1.0,
            "lastfm_import_raw_alias": 1.0,
        }

    async def test_a_spelling_two_artists_share_reaches_neither_artists_track(
        self, real_unit_of_work, real_track_repository
    ):
        """Catches a compile that joins two artists through a shared spelling.

        Two cached bands go by "Nirvana". Joined into one group, a scrobble
        credited "Nirvana" probes under "Nirvana (UK)" as well and reuses the
        UK band's track at artist similarity 1.0. The spelling names neither
        band, so the scrobble mints its own canonical.
        """
        user_id = f"TEST_alias_import_{uuid4().hex[:8]}"
        await seed_aliases(
            real_unit_of_work,
            "Nirvana",
            "Nirvana (US)",
            mbid="5b11f4ce-a62d-471e-81fc-a69a8278c7da",
            primary="Nirvana",
        )
        await seed_aliases(
            real_unit_of_work,
            "Nirvana",
            "Nirvana (UK)",
            mbid="9282c8b4-ca0b-4c6b-b7e3-4f7762dfc4d6",
            primary="Nirvana",
        )
        uk_track = await real_track_repository.save_track(
            make_track(title="Rainbow Chaser", artist="Nirvana (UK)", user_id=user_id)
        )

        resolved, metrics = await LastfmInwardResolver(
            lastfm_client=_unanswering_client()
        ).resolve_to_canonical_tracks(
            ["nirvana::rainbow chaser"], real_unit_of_work, user_id=user_id
        )

        assert (metrics.reused, metrics.created) == (0, 1)
        assert resolved["nirvana::rainbow chaser"].id != uk_track.id
