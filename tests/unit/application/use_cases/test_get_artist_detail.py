"""Unit tests for GetArtistDetailUseCase.

Covers assembly (mappings + counts + favorite), the external-URL builders, the
related-project extraction from connector payloads, and the not-found path.
"""

from uuid import uuid7

import pytest

from src.application.use_cases.get_artist_detail import (
    GetArtistDetailCommand,
    GetArtistDetailUseCase,
)
from src.domain.exceptions import NotFoundError
from src.domain.repositories.artist import ArtistMappingInfo
from tests.fixtures import make_artist
from tests.fixtures.mocks import make_mock_uow


def _mapping(
    connector_name: str = "spotify",
    identifier: str = "4tZwfgrHOc3mvqYlEYSvVi",
    *,
    name: str = "Daft Punk",
    is_primary: bool = True,
    raw_metadata: dict | None = None,
) -> ArtistMappingInfo:
    return ArtistMappingInfo(
        mapping_id=uuid7(),
        connector_name=connector_name,
        connector_artist_identifier=identifier,
        match_method="mbid",
        confidence=95,
        origin="automatic",
        is_primary=is_primary,
        name=name,
        raw_metadata=raw_metadata or {},
    )


@pytest.fixture
def mock_uow():
    return make_mock_uow()


class TestGetArtistDetailUseCase:
    """Assembly, URL building and related projects."""

    async def test_assembles_detail(self, mock_uow) -> None:
        artist = make_artist("Daft Punk")
        mock_uow.get_artist_repository().get_artist_by_id.return_value = artist
        mock_uow.get_artist_repository().count_tracks_by_artist.return_value = {
            artist.id: 31
        }
        mock_uow.get_artist_favorite_repository().get_favorite_status_batch.return_value = {
            artist.id
        }
        mock_uow.get_artist_connector_repository().get_full_mappings_for_artist.return_value = [
            _mapping()
        ]

        result = await GetArtistDetailUseCase().execute(
            GetArtistDetailCommand(user_id="test-user", artist_id=artist.id), mock_uow
        )

        assert result.artist.name == "Daft Punk"
        assert result.track_count == 31
        assert result.is_favorited is True
        assert len(result.connector_mappings) == 1
        assert result.connector_mappings[0].external_url == (
            "https://open.spotify.com/artist/4tZwfgrHOc3mvqYlEYSvVi"
        )

    async def test_lastfm_url_is_name_encoded(self, mock_uow) -> None:
        artist = make_artist("Sigur Rós")
        mock_uow.get_artist_repository().get_artist_by_id.return_value = artist
        mock_uow.get_artist_connector_repository().get_full_mappings_for_artist.return_value = [
            _mapping("lastfm", "Sigur Rós", name="Sigur Rós")
        ]

        result = await GetArtistDetailUseCase().execute(
            GetArtistDetailCommand(user_id="test-user", artist_id=artist.id), mock_uow
        )

        assert result.connector_mappings[0].external_url == (
            "https://www.last.fm/music/Sigur%20R%C3%B3s"
        )

    async def test_unknown_connector_has_no_url(self, mock_uow) -> None:
        artist = make_artist("Nobody")
        mock_uow.get_artist_repository().get_artist_by_id.return_value = artist
        mock_uow.get_artist_connector_repository().get_full_mappings_for_artist.return_value = [
            _mapping("soundcloud", "123", name="Nobody")
        ]

        result = await GetArtistDetailUseCase().execute(
            GetArtistDetailCommand(user_id="test-user", artist_id=artist.id), mock_uow
        )

        assert result.connector_mappings[0].external_url is None

    async def test_related_projects_from_payloads(self, mock_uow) -> None:
        artist = make_artist("Caribou")
        mock_uow.get_artist_repository().get_artist_by_id.return_value = artist
        mock_uow.get_artist_connector_repository().get_full_mappings_for_artist.return_value = [
            _mapping(
                "musicbrainz",
                "mbid-1",
                name="Caribou",
                raw_metadata={
                    "aliases": [{"name": "Manitoba"}],
                    "url_rels": [
                        {"service": "discogs", "identifier": "55", "url": "http://x"}
                    ],
                },
            ),
            _mapping(
                "discogs",
                "55",
                name="Caribou",
                is_primary=False,
                raw_metadata={
                    "aliases": ["Daphni"],
                    "members": [{"name": "Dan Snaith", "id": "99"}],
                },
            ),
        ]

        result = await GetArtistDetailUseCase().execute(
            GetArtistDetailCommand(user_id="test-user", artist_id=artist.id), mock_uow
        )

        by_relation = {(r.name, r.relation) for r in result.related}
        assert ("Manitoba", "alias") in by_relation
        assert ("Daphni", "alias") in by_relation
        assert ("Dan Snaith", "member") in by_relation
        assert ("discogs", "same_as") in by_relation

    async def test_same_as_url_rels_keep_distinct_identifiers(self, mock_uow) -> None:
        """Two url_rels for one service (alias projects) must not collapse.

        Caribou's MusicBrainz entry states two Spotify artist ids — one for
        the Caribou alias, one for Daphni — via two ``url_rels`` entries that
        both name the "spotify" service. Naming both entries the same
        (``name=service``) must not dedupe them down to one.
        """
        artist = make_artist("Caribou")
        mock_uow.get_artist_repository().get_artist_by_id.return_value = artist
        mock_uow.get_artist_connector_repository().get_full_mappings_for_artist.return_value = [
            _mapping(
                "musicbrainz",
                "mbid-1",
                name="Caribou",
                raw_metadata={
                    "url_rels": [
                        {
                            "service": "spotify",
                            "identifier": "caribou-id",
                            "url": "http://x",
                        },
                        {
                            "service": "spotify",
                            "identifier": "daphni-id",
                            "url": "http://y",
                        },
                    ],
                },
            ),
        ]

        result = await GetArtistDetailUseCase().execute(
            GetArtistDetailCommand(user_id="test-user", artist_id=artist.id), mock_uow
        )

        identifiers = {r.identifier for r in result.related if r.relation == "same_as"}
        assert identifiers == {"caribou-id", "daphni-id"}

    async def test_identical_url_rels_still_dedupe(self, mock_uow) -> None:
        """Two mappings stating the same service url_rel collapse to one."""
        artist = make_artist("Caribou")
        mock_uow.get_artist_repository().get_artist_by_id.return_value = artist
        mock_uow.get_artist_connector_repository().get_full_mappings_for_artist.return_value = [
            _mapping(
                "musicbrainz",
                "mbid-1",
                name="Caribou",
                raw_metadata={
                    "url_rels": [
                        {
                            "service": "spotify",
                            "identifier": "caribou-id",
                            "url": "http://x",
                        }
                    ],
                },
            ),
            _mapping(
                "discogs",
                "55",
                name="Caribou",
                is_primary=False,
                raw_metadata={
                    "url_rels": [
                        {
                            "service": "spotify",
                            "identifier": "caribou-id",
                            "url": "http://x",
                        }
                    ],
                },
            ),
        ]

        result = await GetArtistDetailUseCase().execute(
            GetArtistDetailCommand(user_id="test-user", artist_id=artist.id), mock_uow
        )

        same_as = [r for r in result.related if r.relation == "same_as"]
        assert len(same_as) == 1

    async def test_malformed_payload_is_ignored(self, mock_uow) -> None:
        artist = make_artist("Broken")
        mock_uow.get_artist_repository().get_artist_by_id.return_value = artist
        mock_uow.get_artist_connector_repository().get_full_mappings_for_artist.return_value = [
            _mapping(
                "musicbrainz",
                "mbid-2",
                raw_metadata={"aliases": "not-a-list", "members": [{}, None]},
            )
        ]

        result = await GetArtistDetailUseCase().execute(
            GetArtistDetailCommand(user_id="test-user", artist_id=artist.id), mock_uow
        )

        assert result.related == []

    async def test_missing_artist_raises_not_found(self, mock_uow) -> None:
        mock_uow.get_artist_repository().get_artist_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await GetArtistDetailUseCase().execute(
                GetArtistDetailCommand(user_id="test-user", artist_id=uuid7()), mock_uow
            )
