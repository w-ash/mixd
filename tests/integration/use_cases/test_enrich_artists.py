"""Artist enrichment against the real artist tables.

What a unit test cannot show: that one MusicBrainz statement lands as an
identity on ``artists``, rows in ``artist_aliases``, connector records and
mappings for two services, and ``entity_kind='artist'`` resolution events —
and that running the same pass again changes nothing, because the artist is
no longer a candidate.
"""

from uuid import uuid7

from sqlalchemy import func, select

from src.application.use_cases.enrich_artists import (
    EnrichArtistsCommand,
    EnrichArtistsUseCase,
)
from src.domain.entities.artist import Artist, ConnectorArtist
from src.domain.matching.artist_enrichment import (
    ArtistAliasRecord,
    ArtistLookup,
    ArtistUrlRel,
)
from src.infrastructure.persistence.database.models import (
    DBArtist,
    DBArtistAlias,
    DBArtistMapping,
    DBConnectorArtist,
    DBResolutionEvent,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

MBID = "d2f1f4f0-0000-4000-8000-00000000abcd"


class FakeEnrichmentProvider:
    """Canned MusicBrainz answers, and a record of what was asked."""

    def __init__(self, lookup: ArtistLookup | None) -> None:
        self.lookup = lookup
        self.searches: list[str] = []
        self.lookups: list[str] = []

    async def search_artist(self, name: str) -> list[ArtistLookup]:
        self.searches.append(name)
        return [self.lookup] if self.lookup else []

    async def lookup_artist(self, mbid: str) -> ArtistLookup | None:
        self.lookups.append(mbid)
        return self.lookup


def make_lookup() -> ArtistLookup:
    return ArtistLookup(
        mbid=MBID,
        name="Totally Enormous Extinct Dinosaurs",
        kind="person",
        disambiguation="Orlando Higginbottom",
        aliases=(
            ArtistAliasRecord(name="TEED", alias_type="Artist name", locale="en"),
        ),
        url_rels=(
            ArtistUrlRel("spotify", "sp-teed", "https://open.spotify.com/artist/x"),
        ),
    )


async def seed_artist(db_session, user_id: str, name: str) -> Artist:
    """Persist one canonical artist with no identity yet."""
    repo = get_unit_of_work(db_session).get_artist_repository()
    (saved,) = await repo.save_artists([Artist(name=name, user_id=user_id)])
    return saved


async def count(db_session, model, *criteria) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(model).where(*criteria)
    )
    return result.scalar_one()


class TestEnrichmentRoundTrip:
    async def test_one_lookup_writes_identity_aliases_and_two_services(
        self, db_session
    ):
        user_id = f"enrich-{uuid7()}"
        artist = await seed_artist(db_session, user_id, "TEED")
        provider = FakeEnrichmentProvider(make_lookup())

        result = await EnrichArtistsUseCase(provider=provider).execute(
            EnrichArtistsCommand(user_id=user_id), get_unit_of_work(db_session)
        )

        assert result.artists_identified == 1
        assert provider.searches == ["TEED"]
        # The search names the MBID; only the lookup carries the url-rels the
        # Spotify mapping below is seeded from, so it happens on this pass.
        assert provider.lookups == [MBID]

        stored = await db_session.get(DBArtist, artist.id)
        assert stored is not None
        assert stored.mbid == MBID
        assert stored.kind == "person"

        # Primary name plus the stated alias.
        aliases = await db_session.execute(
            select(DBArtistAlias.name, DBArtistAlias.is_primary)
            .join(
                DBConnectorArtist,
                DBConnectorArtist.id == DBArtistAlias.connector_artist_id,
            )
            .where(DBConnectorArtist.connector_artist_identifier == MBID)
        )
        assert set(aliases.tuples()) == {
            ("Totally Enormous Extinct Dinosaurs", True),
            ("TEED", False),
        }

        connector_rows = await db_session.execute(
            select(
                DBConnectorArtist.connector_name,
                DBConnectorArtist.connector_artist_identifier,
            ).where(
                DBConnectorArtist.connector_artist_identifier.in_([MBID, "sp-teed"])
            )
        )
        assert set(connector_rows.tuples()) == {
            ("musicbrainz", MBID),
            ("spotify", "sp-teed"),
        }

        mappings = await db_session.execute(
            select(
                DBArtistMapping.connector_name,
                DBArtistMapping.match_method,
                DBArtistMapping.is_primary,
            ).where(DBArtistMapping.user_id == user_id)
        )
        assert set(mappings.tuples()) == {
            ("musicbrainz", "mbid", True),
            ("spotify", "mb_url_rel", True),
        }

        assert (
            await count(
                db_session,
                DBResolutionEvent,
                DBResolutionEvent.user_id == user_id,
                DBResolutionEvent.entity_kind == "artist",
            )
            == 2
        )

    async def test_a_second_run_is_a_no_op(self, db_session):
        user_id = f"enrich-{uuid7()}"
        _ = await seed_artist(db_session, user_id, "TEED")
        provider = FakeEnrichmentProvider(make_lookup())
        command = EnrichArtistsCommand(user_id=user_id)

        first = await EnrichArtistsUseCase(provider=provider).execute(
            command, get_unit_of_work(db_session)
        )
        mappings_after_first = await count(
            db_session, DBArtistMapping, DBArtistMapping.user_id == user_id
        )

        second = await EnrichArtistsUseCase(provider=provider).execute(
            command, get_unit_of_work(db_session)
        )

        assert first.result.summary_metrics.get("artists_processed") == 1
        # Identified and freshly touched, so no longer a candidate.
        assert second.result.summary_metrics.get("artists_processed") == 0
        assert second.mappings_seeded == 0
        assert (
            await count(db_session, DBArtistMapping, DBArtistMapping.user_id == user_id)
            == mappings_after_first
        )

    async def test_a_collision_leaves_the_artist_unidentified_but_touched(
        self, db_session
    ):
        user_id = f"enrich-{uuid7()}"
        artist = await seed_artist(db_session, user_id, "Justice")

        class Colliding(FakeEnrichmentProvider):
            async def search_artist(self, name: str) -> list[ArtistLookup]:
                self.searches.append(name)
                return [
                    ArtistLookup(mbid="mb-1", name="Justice"),
                    ArtistLookup(mbid="mb-2", name="Justice"),
                ]

        before = (await db_session.get(DBArtist, artist.id)).updated_at

        result = await EnrichArtistsUseCase(provider=Colliding(None)).execute(
            EnrichArtistsCommand(user_id=user_id), get_unit_of_work(db_session)
        )

        assert result.unresolved == 1
        stored = await db_session.get(DBArtist, artist.id)
        await db_session.refresh(stored)
        assert stored.mbid is None
        assert stored.updated_at > before
        assert (
            await count(db_session, DBArtistMapping, DBArtistMapping.user_id == user_id)
            == 0
        )


class TestExistingMappingsSurvive:
    """What the import path wrote outranks what a url-rel infers."""

    async def seed_spotify_mapping(self, db_session, artist: Artist) -> None:
        """Give an artist the ``direct`` Spotify mapping an import would write."""
        connectors = get_unit_of_work(db_session).get_artist_connector_repository()
        stored = await connectors.bulk_upsert_connector_artists(
            "spotify",
            [
                ConnectorArtist(
                    connector_name="spotify",
                    connector_artist_identifier="sp-teed",
                    name=artist.name,
                )
            ],
        )
        assertion = await connectors.assert_mappings([
            {
                "user_id": artist.user_id,
                "artist_id": artist.id,
                "connector_artist_id": stored["sp-teed"].id,
                "connector_name": "spotify",
                "match_method": "direct",
                "confidence": 100,
                "confidence_evidence": None,
            }
        ])
        await connectors.record_assertion(assertion)
        await db_session.commit()

    async def spotify_mapping(self, db_session, user_id: str):
        result = await db_session.execute(
            select(
                DBArtistMapping.artist_id,
                DBArtistMapping.match_method,
            ).where(
                DBArtistMapping.user_id == user_id,
                DBArtistMapping.connector_name == "spotify",
            )
        )
        return result.tuples().all()

    async def test_a_direct_mapping_is_not_downgraded_by_the_rel_that_repeats_it(
        self, db_session
    ):
        user_id = f"enrich-{uuid7()}"
        artist = await seed_artist(db_session, user_id, "TEED")
        await self.seed_spotify_mapping(db_session, artist)

        result = await EnrichArtistsUseCase(
            provider=FakeEnrichmentProvider(make_lookup())
        ).execute(EnrichArtistsCommand(user_id=user_id), get_unit_of_work(db_session))

        assert result.artists_identified == 1
        # Still the import's own word for it, on the same artist.
        assert await self.spotify_mapping(db_session, user_id) == [
            (artist.id, "direct")
        ]
        # Only the MusicBrainz anchor was seeded this run.
        assert result.mappings_seeded == 1
        assert not result.result.resolution_failures

    async def test_a_rel_claiming_another_artists_id_is_reported_not_re_pointed(
        self, db_session
    ):
        user_id = f"enrich-{uuid7()}"
        owner = await seed_artist(db_session, user_id, "Orlando Higginbottom")
        await self.seed_spotify_mapping(db_session, owner)
        # Identified already, so the run below only has the claimant to do.
        await (
            get_unit_of_work(db_session)
            .get_artist_repository()
            .set_identity(owner.id, user_id=user_id, mbid="mb-owner", kind="person")
        )
        await db_session.commit()
        claimant = await seed_artist(db_session, user_id, "TEED")

        result = await EnrichArtistsUseCase(
            provider=FakeEnrichmentProvider(make_lookup())
        ).execute(EnrichArtistsCommand(user_id=user_id), get_unit_of_work(db_session))

        assert result.artists_identified == 1
        # The Spotify id stays where it was; the claimant gets no Spotify row.
        assert await self.spotify_mapping(db_session, user_id) == [(owner.id, "direct")]
        (issue,) = result.result.resolution_failures
        assert issue["artist"] == "TEED"
        assert "Orlando Higginbottom" in str(issue["reason"])
        stored = await db_session.get(DBArtist, claimant.id)
        assert stored.mbid == MBID
