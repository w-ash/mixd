"""Unit tests for the artist-enrichment operation.

Covers what the operation decides rather than what MusicBrainz says: which
candidates get looked up by MBID and which by name, when a name search is too
ambiguous to act on, what lands in the alias cache and the url-rel-seeded
mappings, how the run commits, and what a provider fault or a dry run does.
"""

from unittest.mock import AsyncMock
from uuid import UUID, uuid7

import pytest

from src.application.use_cases.enrich_artists import (
    EnrichArtistsCommand,
    EnrichArtistsUseCase,
)
from src.domain.entities.artist import ConnectorArtist
from src.domain.matching.artist_enrichment import (
    ArtistAliasRecord,
    ArtistLookup,
    ArtistUrlRel,
)
from tests.fixtures import (
    TEST_USER_ID,
    make_artist,
    make_mock_artist_alias_repo,
    make_mock_artist_connector_repo,
    make_mock_artist_repo,
    make_mock_uow,
)

MBID = "8b1f0f7f-0000-4000-8000-000000000001"


def make_lookup(**overrides) -> ArtistLookup:
    """A MusicBrainz statement with aliases and one Spotify id."""
    defaults = {
        "mbid": MBID,
        "name": "Totally Enormous Extinct Dinosaurs",
        "kind": "person",
        "disambiguation": "Orlando Higginbottom",
        "aliases": (ArtistAliasRecord(name="TEED", alias_type="Artist name"),),
        "url_rels": (
            ArtistUrlRel("spotify", "sp-1", "https://open.spotify.com/artist/sp-1"),
        ),
    }
    return ArtistLookup(**(defaults | overrides))


def make_connector_repo(**overrides) -> AsyncMock:
    """Artist connector repo mock carrying the mapping-assert seam.

    ``assert_mappings`` / ``record_assertion`` are set explicitly: a protocol
    runtime check reads attributes statically, so a lazily built mock member
    would not satisfy ``ArtistMappingWriter``.
    """
    repo = make_mock_artist_connector_repo(**overrides)
    repo.assert_mappings = AsyncMock(return_value=object())
    repo.record_assertion = AsyncMock()
    return repo


def upsert_returns(repo: AsyncMock, ids: dict[str, UUID] | None = None) -> None:
    """Echo each upserted batch back keyed by identifier, as the real one does.

    ``ids`` pins the row id a given connector identifier comes back with, so a
    test can hand the same id to ``find_artists_by_connector_artist_ids``.
    """
    pinned = ids or {}

    async def _upsert(connector_name: str, artists) -> dict[str, ConnectorArtist]:
        return {
            artist.connector_artist_identifier: ConnectorArtist(
                connector_name=connector_name,
                connector_artist_identifier=artist.connector_artist_identifier,
                name=artist.name,
                raw_metadata=artist.raw_metadata,
                id=pinned.get(artist.connector_artist_identifier, artist.id),
            )
            for artist in artists
        }

    repo.bulk_upsert_connector_artists.side_effect = _upsert


# "not given", as distinct from "the provider answers None".
UNSET = object()


def build(candidates, *, hits=None, lookup=UNSET, error=None, connector_repo=None):
    """Wire a UoW, a fake provider and the repos one run needs.

    A search hit is re-read by MBID before anything is written, so ``lookup``
    defaults to the first hit: that is what MusicBrainz returns for the id the
    search just handed over, minus the relations only a lookup carries.
    """
    provider = AsyncMock()
    if lookup is UNSET:
        lookup = hits[0] if hits else None
    if error is not None:
        provider.search_artist.side_effect = error
        provider.lookup_artist.side_effect = error
    else:
        provider.search_artist.return_value = hits or []
        provider.lookup_artist.return_value = lookup

    artist_repo = make_mock_artist_repo(list_needing_enrichment=candidates)
    if connector_repo is None:
        connector_repo = make_connector_repo()
        upsert_returns(connector_repo)
    alias_repo = make_mock_artist_alias_repo(replace_aliases=2)
    uow = make_mock_uow(
        artist_repo=artist_repo,
        artist_connector_repo=connector_repo,
        artist_alias_repo=alias_repo,
        artist_enrichment_provider=provider,
    )
    return uow, provider, artist_repo, connector_repo, alias_repo


def command(**overrides) -> EnrichArtistsCommand:
    return EnrichArtistsCommand(user_id=TEST_USER_ID, **overrides)


class TestIdentification:
    async def test_an_anchored_artist_is_refreshed_through_its_mbid(self):
        artist = make_artist(name="TEED", mbid=MBID)
        uow, provider, artist_repo, _, _ = build([artist], lookup=make_lookup())

        result = await EnrichArtistsUseCase().execute(command(), uow)

        provider.search_artist.assert_not_awaited()
        provider.lookup_artist.assert_awaited_once_with(MBID)
        artist_repo.set_identity.assert_awaited_once()
        # Already identified: the run refreshed it, it did not identify it.
        assert result.artists_identified == 0
        assert result.unresolved == 0

    async def test_an_unanchored_artist_is_searched_by_name(self):
        artist = make_artist(name="Totally Enormous Extinct Dinosaurs")
        uow, provider, artist_repo, _, _ = build([artist], hits=[make_lookup()])

        result = await EnrichArtistsUseCase().execute(command(), uow)

        provider.search_artist.assert_awaited_once_with(artist.name)
        assert result.artists_identified == 1
        _, kwargs = artist_repo.set_identity.call_args
        assert kwargs["mbid"] == MBID
        assert kwargs["kind"] == "person"

    async def test_an_alias_hit_identifies_the_artist(self):
        # Mixd holds the abbreviation; MusicBrainz states it as an alias.
        artist = make_artist(name="TEED")
        uow, _, artist_repo, _, _ = build([artist], hits=[make_lookup()])

        result = await EnrichArtistsUseCase().execute(command(), uow)

        assert result.artists_identified == 1
        artist_repo.set_identity.assert_awaited_once()

    async def test_a_lookup_that_does_not_resolve_leaves_the_artist_touched(self):
        artist = make_artist(name="TEED", mbid=MBID)
        uow, _, artist_repo, _, _ = build([artist], lookup=None)

        result = await EnrichArtistsUseCase().execute(command(), uow)

        assert result.unresolved == 1
        artist_repo.set_identity.assert_not_awaited()
        artist_repo.touch.assert_awaited_once_with([artist.id], user_id=TEST_USER_ID)


class TestAmbiguity:
    async def test_a_near_tie_leaves_the_artist_unidentified(self):
        # Two MusicBrainz artists both named "Justice" — the ranking between
        # them is arbitrary, so neither may claim the name.
        artist = make_artist(name="Justice")
        hits = [
            make_lookup(mbid="mb-1", name="Justice", aliases=(), url_rels=()),
            make_lookup(mbid="mb-2", name="Justice", aliases=(), url_rels=()),
        ]
        uow, _, artist_repo, connector_repo, _ = build([artist], hits=hits)

        result = await EnrichArtistsUseCase().execute(command(), uow)

        assert result.unresolved == 1
        assert result.artists_identified == 0
        artist_repo.set_identity.assert_not_awaited()
        connector_repo.assert_mappings.assert_not_awaited()
        # Touched anyway: the run has to advance past a collision.
        artist_repo.touch.assert_awaited_once_with([artist.id], user_id=TEST_USER_ID)

    async def test_a_clear_winner_is_accepted_over_a_weaker_runner_up(self):
        artist = make_artist(name="Aphex Twin")
        hits = [
            make_lookup(mbid="mb-1", name="Aphex Twin", aliases=(), url_rels=()),
            make_lookup(
                mbid="mb-2", name="Aphex Twin Tribute Band", aliases=(), url_rels=()
            ),
        ]
        uow, _, _, _, _ = build([artist], hits=hits)

        result = await EnrichArtistsUseCase().execute(command(), uow)

        assert result.artists_identified == 1

    async def test_an_empty_search_leaves_the_artist_unresolved(self):
        artist = make_artist(name="Nobody At All")
        uow, _, _, _, _ = build([artist], hits=[])

        result = await EnrichArtistsUseCase().execute(command(), uow)

        assert result.unresolved == 1


class TestCachedRows:
    async def test_the_primary_name_is_written_as_a_primary_alias(self):
        artist = make_artist(name="TEED")
        uow, _, _, connector_repo, alias_repo = build([artist], hits=[make_lookup()])

        _ = await EnrichArtistsUseCase().execute(command(), uow)

        _, aliases = alias_repo.replace_aliases.call_args.args
        assert [(a.name, a.is_primary) for a in aliases] == [
            ("Totally Enormous Extinct Dinosaurs", True),
            ("TEED", False),
        ]
        mb_upsert = connector_repo.bulk_upsert_connector_artists.call_args_list[0]
        assert mb_upsert.args[0] == "musicbrainz"
        assert mb_upsert.args[1][0].raw_metadata["kind"] == "person"

    async def test_url_rels_seed_one_mapping_per_service(self):
        artist = make_artist(name="Aphex Twin")
        lookup = make_lookup(
            name="Aphex Twin",
            aliases=(),
            url_rels=(
                ArtistUrlRel("spotify", "sp-1", "https://open.spotify.com/artist/sp-1"),
                ArtistUrlRel("discogs", "1289", "https://www.discogs.com/artist/1289"),
                ArtistUrlRel("apple", "1234", "https://music.apple.com/us/artist/x"),
                ArtistUrlRel("tidal", "77", "https://tidal.com/artist/77"),
            ),
        )
        uow, _, _, connector_repo, _ = build([artist], hits=[lookup])

        result = await EnrichArtistsUseCase().execute(command(), uow)

        (rows,) = connector_repo.assert_mappings.call_args.args
        by_service = {row["connector_name"]: row for row in rows}
        assert set(by_service) == {
            "musicbrainz",
            "spotify",
            "discogs",
            "apple",
            "tidal",
        }
        assert by_service["musicbrainz"]["match_method"] == "mbid"
        assert all(
            by_service[service]["match_method"] == "mb_url_rel"
            for service in ("spotify", "discogs", "apple", "tidal")
        )
        # Id-tier evidence, the same band a direct connector id scores at.
        assert by_service["spotify"]["confidence"] == 100
        assert result.mappings_seeded == 5
        connector_repo.record_assertion.assert_awaited_once()

    async def test_several_rels_for_one_service_are_all_kept(self):
        # Alias projects carry separate ids on one service and are linked,
        # never merged — the election decides which one the UI shows.
        artist = make_artist(name="Caribou")
        lookup = make_lookup(
            name="Caribou",
            aliases=(),
            url_rels=(
                ArtistUrlRel("spotify", "sp-1", "https://open.spotify.com/artist/sp-1"),
                ArtistUrlRel("spotify", "sp-2", "https://open.spotify.com/artist/sp-2"),
            ),
        )
        uow, _, _, connector_repo, _ = build([artist], hits=[lookup])

        _ = await EnrichArtistsUseCase().execute(command(), uow)

        (rows,) = connector_repo.assert_mappings.call_args.args
        assert sum(1 for row in rows if row["connector_name"] == "spotify") == 2
        (candidates,) = connector_repo.ensure_primaries.call_args.args
        spotify_candidates = [c for c in candidates if c.connector_name == "spotify"]
        assert len(spotify_candidates) == 2
        assert connector_repo.ensure_primaries.call_args.kwargs["mode"] == "fill"


class TestBatching:
    async def test_the_run_commits_every_twenty_five_artists(self):
        artists = [make_artist(name=f"Artist {i}", id=uuid7()) for i in range(60)]
        uow, _, _, connector_repo, _ = build([artists[0]], hits=[])
        uow.get_artist_repository.return_value.list_needing_enrichment.return_value = (
            artists
        )

        _ = await EnrichArtistsUseCase().execute(command(), uow)

        # Two full batches plus the closing flush.
        assert uow.commit_batch.await_count == 3
        assert connector_repo.assert_mappings.await_count == 0

    async def test_nothing_due_is_a_clean_empty_run(self):
        # The steady state: every artist is identified and fresh.
        uow, provider, _, connector_repo, _ = build([])

        result = await EnrichArtistsUseCase().execute(command(), uow)

        assert result.result.summary_metrics.get("artists_processed") == 0
        assert not result.result.is_failure
        provider.search_artist.assert_not_awaited()
        connector_repo.assert_mappings.assert_not_awaited()


class TestFailures:
    async def test_a_provider_fault_is_recorded_and_the_run_continues(self):
        artists = [make_artist(name="Boom"), make_artist(name="TEED")]
        provider = AsyncMock()
        provider.search_artist.side_effect = [
            RuntimeError("musicbrainz unreachable"),
            [make_lookup()],
        ]
        provider.lookup_artist.return_value = make_lookup()
        connector_repo = make_connector_repo()
        upsert_returns(connector_repo)
        uow = make_mock_uow(
            artist_repo=make_mock_artist_repo(list_needing_enrichment=artists),
            artist_connector_repo=connector_repo,
            artist_alias_repo=make_mock_artist_alias_repo(replace_aliases=2),
            artist_enrichment_provider=provider,
        )

        result = await EnrichArtistsUseCase().execute(command(), uow)

        assert result.result.summary_metrics.get("artists_processed") == 2
        assert result.artists_identified == 1
        assert result.unresolved == 1
        assert result.result.is_failure
        assert result.result.is_partial_failure
        (issue,) = result.result.resolution_failures
        assert issue["artist"] == "Boom"
        assert "musicbrainz unreachable" in str(issue["reason"])


class TestDryRun:
    async def test_a_dry_run_looks_up_and_writes_nothing(self):
        artist = make_artist(name="TEED")
        uow, provider, artist_repo, connector_repo, alias_repo = build(
            [artist], hits=[make_lookup()]
        )

        result = await EnrichArtistsUseCase().execute(command(dry_run=True), uow)

        provider.search_artist.assert_awaited_once()
        artist_repo.set_identity.assert_not_awaited()
        artist_repo.touch.assert_not_awaited()
        connector_repo.bulk_upsert_connector_artists.assert_not_awaited()
        connector_repo.assert_mappings.assert_not_awaited()
        alias_repo.replace_aliases.assert_not_awaited()
        uow.commit_batch.assert_not_awaited()

        assert result.artists_identified == 1
        assert result.aliases_written == 2
        assert result.mappings_seeded == 2
        assert result.result.metadata["dry_run"] is True


class TestMappingSeam:
    async def test_a_repository_without_the_assert_seam_is_rejected(self):
        artist = make_artist(name="TEED")
        uow, _, _, _, _ = build([artist], hits=[make_lookup()])
        # A repository that cannot assert mappings is a wiring defect, not a
        # silently skipped write.
        del uow.get_artist_connector_repository.return_value.assert_mappings

        with pytest.raises(TypeError, match="assert_mappings"):
            _ = await EnrichArtistsUseCase().execute(command(), uow)


class TestFirstPassSeeding:
    """A name-identified artist gets its ids on the run that identified it."""

    async def test_a_search_hit_is_re_read_by_mbid_before_anything_is_written(self):
        # What MusicBrainz' /artist?query= actually returns: no relations.
        hit = make_lookup(url_rels=())
        full = make_lookup(
            url_rels=(
                ArtistUrlRel("spotify", "sp-1", "https://open.spotify.com/artist/sp-1"),
                ArtistUrlRel("discogs", "1289", "https://www.discogs.com/artist/1289"),
            )
        )
        artist = make_artist(name="TEED")
        uow, provider, _, connector_repo, _ = build([artist], hits=[hit], lookup=full)

        result = await EnrichArtistsUseCase().execute(command(), uow)

        provider.lookup_artist.assert_awaited_once_with(MBID)
        (rows,) = connector_repo.assert_mappings.call_args.args
        assert {row["connector_name"] for row in rows} == {
            "musicbrainz",
            "spotify",
            "discogs",
        }
        assert result.mappings_seeded == 3

    async def test_the_search_hit_carries_the_artist_when_the_re_read_fails(self):
        artist = make_artist(name="TEED")
        hit = make_lookup(url_rels=())
        uow, _, artist_repo, connector_repo, _ = build([artist], hits=[hit])
        uow.get_artist_enrichment_provider.return_value.lookup_artist.side_effect = (
            RuntimeError("musicbrainz 503")
        )

        result = await EnrichArtistsUseCase().execute(command(), uow)

        # Identified on the hit's own aliases; the ids wait for the next pass.
        assert result.artists_identified == 1
        artist_repo.set_identity.assert_awaited_once()
        (rows,) = connector_repo.assert_mappings.call_args.args
        assert {row["connector_name"] for row in rows} == {"musicbrainz"}
        (issue,) = result.result.resolution_failures
        assert "lookup after search" in str(issue["reason"])


class TestExistingMappingsSurvive:
    """url-rel seeding is additive: it never rewrites a live mapping."""

    def _repo_with_existing(self, connector_artist_id, owner):
        repo = make_connector_repo(
            find_artists_by_connector_artist_ids={connector_artist_id: owner}
        )
        upsert_returns(repo, {"sp-1": connector_artist_id})
        return repo

    async def test_an_existing_direct_mapping_is_stamped_not_rewritten(self):
        artist = make_artist(name="TEED")
        sp_row_id = uuid7()
        repo = self._repo_with_existing(sp_row_id, artist)
        uow, _, _, connector_repo, _ = build(
            [artist], hits=[make_lookup()], connector_repo=repo
        )

        result = await EnrichArtistsUseCase().execute(command(), uow)

        (rows,) = connector_repo.assert_mappings.call_args.args
        # The MusicBrainz anchor only — the import path's ``direct`` Spotify
        # mapping is left exactly as the import wrote it.
        assert [row["connector_name"] for row in rows] == ["musicbrainz"]
        connector_repo.touch_last_seen.assert_awaited_once_with(
            "spotify", [sp_row_id], user_id=artist.user_id
        )
        assert result.mappings_seeded == 1
        assert not result.result.resolution_failures

    async def test_a_conflicting_owner_is_reported_and_nothing_is_re_pointed(self):
        artist = make_artist(name="TEED")
        other = make_artist(name="Orlando Higginbottom")
        sp_row_id = uuid7()
        repo = self._repo_with_existing(sp_row_id, other)
        uow, _, _, connector_repo, _ = build(
            [artist], hits=[make_lookup()], connector_repo=repo
        )

        result = await EnrichArtistsUseCase().execute(command(), uow)

        (rows,) = connector_repo.assert_mappings.call_args.args
        assert [row["connector_name"] for row in rows] == ["musicbrainz"]
        # Not even a freshness stamp: the id is not this artist's to claim.
        connector_repo.touch_last_seen.assert_not_awaited()
        (issue,) = result.result.resolution_failures
        assert "Orlando Higginbottom" in str(issue["reason"])
        assert "left as is" in str(issue["reason"])

    async def test_two_artists_in_one_batch_do_not_fight_over_one_id(self):
        # Neither is mapped yet, so the database can answer for neither: the
        # first to stage the claim keeps it.
        first = make_artist(name="Caribou")
        second = make_artist(name="Daphni")
        sp_row_id = uuid7()
        repo = make_connector_repo()
        upsert_returns(repo, {"sp-1": sp_row_id})
        uow, _, _, connector_repo, _ = build(
            [first, second],
            hits=[make_lookup(name="Caribou", aliases=())],
            connector_repo=repo,
        )
        uow.get_artist_enrichment_provider.return_value.search_artist.side_effect = [
            [make_lookup(name="Caribou", aliases=())],
            [make_lookup(mbid="mb-daphni", name="Daphni", aliases=())],
        ]
        uow.get_artist_enrichment_provider.return_value.lookup_artist.side_effect = [
            make_lookup(name="Caribou", aliases=()),
            make_lookup(mbid="mb-daphni", name="Daphni", aliases=()),
        ]

        result = await EnrichArtistsUseCase().execute(command(), uow)

        (rows,) = connector_repo.assert_mappings.call_args.args
        spotify_rows = [row for row in rows if row["connector_name"] == "spotify"]
        assert len(spotify_rows) == 1
        assert spotify_rows[0]["artist_id"] == first.id
        (issue,) = result.result.resolution_failures
        assert issue["artist"] == "Daphni"
