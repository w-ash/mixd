"""``ArtistResolutionService``: sources the payloads and delegates the walk.

The walk itself is ``ArtistMinter``'s (tested in infrastructure); here the
question is what the service hands the unit of work's minter.
"""

from datetime import UTC, datetime

from src.application.services.artist_resolution import ArtistResolutionService
from src.config import create_matching_config
from src.domain.entities import ArtistCredit, ConnectorTrack, Track
from src.domain.repositories.artist import ArtistMintSummary
from tests.fixtures import TEST_USER_ID, make_mock_uow

CONFIG = create_matching_config()
SERVICE = ArtistResolutionService(evaluator_config=CONFIG)


def _payload(
    identifier: str, *names: str, artist_ids: list[str | None]
) -> ConnectorTrack:
    return ConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=identifier,
        title=f"Song {identifier}",
        artists=[ArtistCredit(credited_name=name) for name in names],
        raw_metadata={"artist_ids": list(artist_ids)},
        last_updated=datetime.now(UTC),
    )


class TestIngest:
    async def test_reads_each_payload_into_a_credit_source_and_delegates(self):
        summary = ArtistMintSummary(connector_artists_upserted=2, artists_created=1)
        uow = make_mock_uow()
        uow.get_artist_minter().mint.return_value = summary
        canonical = Track(
            title="Song",
            artists=[ArtistCredit(credited_name="Caribou")],
            user_id=TEST_USER_ID,
        )

        result = await SERVICE.ingest(
            "spotify",
            [
                _payload("t1", "Caribou", "Koushik", artist_ids=["sp-1", "sp-2"]),
                _payload("t2", "Tycho", artist_ids=[None]),
            ],
            uow,
            user_id=TEST_USER_ID,
            canonicals={"t1": canonical},
        )

        assert result is summary
        mint = uow.get_artist_minter().mint
        mint.assert_awaited_once()
        connector, sources, canonicals = mint.await_args.args
        assert connector == "spotify"
        assert [(s.key, s.artist_ids) for s in sources] == [
            ("t1", ("sp-1", "sp-2")),
            ("t2", (None,)),
        ]
        assert canonicals == {"t1": canonical}
        assert mint.await_args.kwargs == {"user_id": TEST_USER_ID, "config": CONFIG}

    async def test_an_empty_batch_still_asks_the_minter_and_reports_its_answer(self):
        uow = make_mock_uow()

        result = await SERVICE.ingest(
            "spotify", [], uow, user_id=TEST_USER_ID, canonicals={}
        )

        assert result == ArtistMintSummary()
        assert result.empty
        (_, sources, _) = uow.get_artist_minter().mint.await_args.args
        assert sources == []
