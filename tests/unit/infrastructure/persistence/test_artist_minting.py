"""``ArtistMinter.mint``: the repository walk, with zero I/O.

The decisions are tested in the domain; here the question is which seams
the minter calls, in what order, and with what — against ``make_mock_uow``
with the artist repositories answering the probes and echoing the writes.
The connector-artist rows are the connector-track writer's; the minter only
reads them back by identifier.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

from src.config import create_matching_config
from src.domain.entities import (
    ArtistCredit,
    ConnectorArtistCredit,
    ConnectorTrack,
    Track,
)
from src.domain.entities.artist import Artist, ConnectorArtist
from src.domain.matching.artist_resolution import ArtistCreditSource
from src.domain.repositories.artist import ArtistMintSummary
from src.infrastructure.persistence.artist_minting import ArtistMinter
from tests.fixtures import TEST_USER_ID, make_mock_uow

CONFIG = create_matching_config()


async def _mint(connector: str, payloads: list[ConnectorTrack], uow, canonicals):
    """Mint the payloads the way ``ArtistResolutionService.ingest`` sources them."""
    return await ArtistMinter(uow).mint(
        connector,
        [ArtistCreditSource(p.connector_track_identifier, p.artists) for p in payloads],
        canonicals,
        user_id=TEST_USER_ID,
        config=CONFIG,
    )


def _payload(connector: str, identifier: str, *credits: tuple[str, str | None]):
    return ConnectorTrack(
        connector_name=connector,
        connector_track_identifier=identifier,
        title=f"Song {identifier}",
        artists=[
            ConnectorArtistCredit(credited_name=name, connector_artist_identifier=aid)
            for name, aid in credits
        ],
        last_updated=datetime.now(UTC),
    )


def _canonical(*names: str) -> Track:
    return Track(
        title="Song",
        artists=[ArtistCredit(credited_name=name) for name in names],
        user_id=TEST_USER_ID,
    )


def _uow(
    *, stored: dict[str, ConnectorArtist], owners: dict[str, Artist] | None = None
):
    """A UoW whose artist repositories answer the reads and echo the writes.

    ``find_connector_artists`` answers from ``stored`` (identifier → row);
    ``find_artists_by_connector_artist_ids`` maps each stored row whose
    identifier is in ``owners`` to that artist.
    """
    uow = make_mock_uow()
    artist_connectors = uow.get_artist_connector_repository()

    async def _find(connector: str, identifiers: list[str]):
        return {i: stored[i] for i in identifiers if i in stored}

    async def _owners(row_ids: list[UUID], *, user_id: str) -> dict[UUID, Artist]:
        by_row = {row.id: identifier for identifier, row in stored.items()}
        return {
            row_id: (owners or {})[by_row[row_id]]
            for row_id in row_ids
            if by_row.get(row_id) in (owners or {})
        }

    async def _fill(assignments: list[tuple[UUID, int, UUID]], *, user_id: str) -> int:
        return len(assignments)

    artist_connectors.find_connector_artists = AsyncMock(side_effect=_find)
    artist_connectors.find_artists_by_connector_artist_ids = AsyncMock(
        side_effect=_owners
    )
    artist_connectors.assert_mappings = AsyncMock(return_value="assertion")
    artist_connectors.ensure_primaries = AsyncMock(return_value=[])
    artist_connectors.record_assertion = AsyncMock(return_value=None)
    uow.get_artist_repository().save_artists = AsyncMock(side_effect=list)
    uow.get_artist_repository().touch = AsyncMock(return_value=None)
    uow.get_track_repository().set_credit_artist_ids = AsyncMock(side_effect=_fill)
    return uow


def _stored(connector: str, *identifiers: str) -> dict[str, ConnectorArtist]:
    return {
        identifier: ConnectorArtist(
            connector_name=connector,
            connector_artist_identifier=identifier,
            name=identifier,
        )
        for identifier in identifiers
    }


class TestMinting:
    async def test_spotify_ids_mint_artists_mappings_and_credits(self):
        stored = _stored("spotify", "sp-1", "sp-2")
        uow = _uow(stored=stored)
        canonical = _canonical("Caribou", "Koushik")
        payload = _payload("spotify", "t1", ("Caribou", "sp-1"), ("Koushik", "sp-2"))

        summary = await _mint("spotify", [payload], uow, {"t1": canonical})

        assert summary == ArtistMintSummary(
            artists_created=2, artists_reused=0, credits_assigned=2
        )
        connectors = uow.get_artist_connector_repository()
        connectors.find_connector_artists.assert_awaited_once_with(
            "spotify", ["sp-1", "sp-2"]
        )
        connectors.bulk_upsert_connector_artists.assert_not_awaited()
        connectors.ensure_connector_artists.assert_not_awaited()

        saved = uow.get_artist_repository().save_artists.await_args.args[0]
        assert [(a.name, a.user_id, a.mbid) for a in saved] == [
            ("Caribou", TEST_USER_ID, None),
            ("Koushik", TEST_USER_ID, None),
        ]
        rows = connectors.assert_mappings.await_args.args[0]
        assert [
            (
                r["artist_id"],
                r["connector_artist_id"],
                r["connector_name"],
                r["match_method"],
                r["origin"],
                r["is_primary"],
            )
            for r in rows
        ] == [
            (saved[0].id, stored["sp-1"].id, "spotify", "direct", "automatic", True),
            (saved[1].id, stored["sp-2"].id, "spotify", "direct", "automatic", True),
        ]
        assert rows[0]["confidence_evidence"]["level"] == "connector_id"
        connectors.ensure_primaries.assert_awaited_once()
        assert connectors.ensure_primaries.await_args.kwargs == {"mode": "fill"}
        connectors.record_assertion.assert_awaited_once_with("assertion")
        assignments = uow.get_track_repository().set_credit_artist_ids.await_args.args[
            0
        ]
        assert assignments == [
            (canonical.id, 0, saved[0].id),
            (canonical.id, 1, saved[1].id),
        ]
        uow.get_artist_repository().touch.assert_not_awaited()

    async def test_an_existing_mapping_reuses_instead_of_creating(self):
        owner = Artist(name="Caribou", user_id=TEST_USER_ID)
        uow = _uow(stored=_stored("spotify", "sp-1"), owners={"sp-1": owner})
        canonical = _canonical("Caribou")

        summary = await _mint(
            "spotify",
            [_payload("spotify", "t1", ("Caribou", "sp-1"))],
            uow,
            {"t1": canonical},
        )

        assert summary == ArtistMintSummary(artists_reused=1, credits_assigned=1)
        uow.get_artist_repository().save_artists.assert_not_awaited()
        uow.get_artist_connector_repository().assert_mappings.assert_not_awaited()
        uow.get_artist_repository().touch.assert_awaited_once_with(
            [owner.id], user_id=TEST_USER_ID
        )
        assert uow.get_track_repository().set_credit_artist_ids.await_args.args[0] == [
            (canonical.id, 0, owner.id)
        ]

    async def test_various_artists_and_none_ids_mint_nothing(self):
        uow = _uow(stored=_stored("spotify", "sp-va"))
        payloads = [
            _payload("spotify", "t1", ("Various Artists", "sp-va")),
            _payload("apple", "t2", ("Tycho", None)),
            _payload("spotify", "t3", ("Tycho", None)),
        ]

        for payload in payloads:
            summary = await _mint(
                payload.connector_name,
                [payload],
                uow,
                {payload.connector_track_identifier: _canonical("Tycho")},
            )
            assert summary == ArtistMintSummary()

        uow.get_artist_connector_repository().find_connector_artists.assert_not_awaited()
        uow.get_artist_repository().save_artists.assert_not_awaited()
        uow.get_track_repository().set_credit_artist_ids.assert_not_awaited()

    async def test_lastfm_never_mints_an_artist(self):
        uow = _uow(stored=_stored("lastfm", "Bonobo"))

        summary = await _mint(
            "lastfm",
            [_payload("lastfm", "bonobo::kiara", ("Bonobo", "Bonobo"))],
            uow,
            {"bonobo::kiara": _canonical("Bonobo")},
        )

        assert summary == ArtistMintSummary()
        connectors = uow.get_artist_connector_repository()
        connectors.find_connector_artists.assert_not_awaited()
        connectors.find_artists_by_connector_artist_ids.assert_not_awaited()
        uow.get_artist_repository().save_artists.assert_not_awaited()
        uow.get_track_repository().set_credit_artist_ids.assert_not_awaited()

    async def test_an_id_with_no_stored_row_is_skipped(self):
        """The writer stores the rows before the minter runs; a missing one is not invented."""
        uow = _uow(stored={})

        summary = await _mint(
            "spotify",
            [_payload("spotify", "t1", ("Caribou", "sp-1"))],
            uow,
            {"t1": _canonical("Caribou")},
        )

        assert summary == ArtistMintSummary()
        uow.get_artist_repository().save_artists.assert_not_awaited()

    async def test_one_artist_across_two_payloads_is_minted_once(self):
        uow = _uow(stored=_stored("spotify", "sp-1"))
        first, second = _canonical("Tycho"), _canonical("Tycho")

        summary = await _mint(
            "spotify",
            [
                _payload("spotify", "t1", ("Tycho", "sp-1")),
                _payload("spotify", "t2", ("Tycho", "sp-1")),
            ],
            uow,
            {"t1": first, "t2": second},
        )

        assert summary.artists_created == 1
        assert summary.credits_assigned == 2
        (artist,) = uow.get_artist_repository().save_artists.await_args.args[0]
        assert uow.get_track_repository().set_credit_artist_ids.await_args.args[0] == [
            (first.id, 0, artist.id),
            (second.id, 0, artist.id),
        ]
