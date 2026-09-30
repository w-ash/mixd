"""Unit tests for the artist-minting operation.

Covers what the operation decides rather than what the minter writes: how it
pages the candidate query, that each page reaches the minter once per connector
with that page's own sources and canonicals, when it commits, and what a limit
or a dry run does.
"""

from unittest.mock import AsyncMock

from src.application.use_cases.mint_artists import (
    MintArtistsCommand,
    MintArtistsUseCase,
)
from src.domain.repositories.artist import ArtistMintSummary
from tests.fixtures import (
    TEST_USER_ID,
    make_connector_track,
    make_mock_connector_repo,
    make_mock_uow,
    make_track,
)


def make_pair(index: int, *, connector: str = "spotify"):
    """One (canonical, payload) pair as the candidate query hands it back."""
    track = make_track(title=f"Song {index}", artist=f"Artist {index}")
    payload = make_connector_track(
        f"{connector}-{index}",
        connector_name=connector,
        artist=f"Artist {index}",
        connector_artist_identifier=f"{connector}-artist-{index}",
    )
    return track, payload


def make_uow(pages: list[list[tuple[object, object]]], **overrides):
    """A UoW whose candidate query returns each page in turn, then nothing."""
    repo = make_mock_connector_repo()
    repo.count_unlinked_credit_sources = AsyncMock(
        return_value=overrides.pop("count", sum(len(page) for page in pages))
    )
    repo.list_unlinked_credit_sources = AsyncMock(side_effect=[*pages, []])
    uow = make_mock_uow(connector_repo=repo, **overrides)
    uow.commit_batch = AsyncMock()
    return uow, repo


class TestPaging:
    async def test_pages_until_the_query_runs_dry_and_commits_each_page(self):
        first = [make_pair(0), make_pair(1)]
        second = [make_pair(2)]
        uow, repo = make_uow([first, second])

        result = await MintArtistsUseCase(page_size=2).execute(
            MintArtistsCommand(user_id=TEST_USER_ID), uow
        )

        assert result.result.summary_metrics.get("tracks_processed") == 3
        # Three calls: two full pages and the empty one that ends the walk.
        assert repo.list_unlinked_credit_sources.await_count == 3
        assert uow.commit_batch.await_count == 2

    async def test_each_page_resumes_past_its_last_track(self):
        first = [make_pair(0), make_pair(1)]
        uow, repo = make_uow([first])

        _ = await MintArtistsUseCase(page_size=2).execute(
            MintArtistsCommand(user_id=TEST_USER_ID), uow
        )

        after = [
            call.kwargs["after_track_id"]
            for call in repo.list_unlinked_credit_sources.await_args_list
        ]
        assert after == [None, first[-1][0].id]

    async def test_the_minter_is_called_once_per_connector_with_that_page(self):
        spotify = [make_pair(0), make_pair(1)]
        tidal = [make_pair(2, connector="tidal")]
        uow, _ = make_uow([[*spotify, *tidal]])
        minter = uow.get_artist_minter.return_value

        _ = await MintArtistsUseCase().execute(
            MintArtistsCommand(user_id=TEST_USER_ID), uow
        )

        by_connector = {
            call.args[0]: call for call in minter.mint.await_args_list if call.args
        }
        assert set(by_connector) == {"spotify", "tidal"}

        sources = by_connector["spotify"].args[1]
        assert [source.key for source in sources] == [
            payload.connector_track_identifier for _, payload in spotify
        ]
        # The canonical each payload's credits are filled on, keyed by payload.
        canonicals = by_connector["spotify"].args[2]
        assert canonicals == {
            payload.connector_track_identifier: track for track, payload in spotify
        }
        assert by_connector["spotify"].kwargs["user_id"] == TEST_USER_ID

    async def test_the_minter_summaries_add_up_into_the_result(self):
        uow, _ = make_uow([[make_pair(0)], [make_pair(1)]])
        minter = uow.get_artist_minter.return_value
        minter.mint.side_effect = [
            ArtistMintSummary(artists_created=2, artists_reused=1, credits_assigned=3),
            ArtistMintSummary(artists_created=1, artists_reused=2, credits_assigned=2),
        ]

        result = await MintArtistsUseCase(page_size=1).execute(
            MintArtistsCommand(user_id=TEST_USER_ID), uow
        )

        metrics = result.result.summary_metrics
        assert metrics.get("artists_created") == 3
        assert metrics.get("artists_reused") == 3
        assert metrics.get("credits_linked") == 5


class TestLimitAndDryRun:
    async def test_limit_caps_the_page_size_and_stops_the_walk(self):
        uow, repo = make_uow([[make_pair(0), make_pair(1)]], count=50)

        result = await MintArtistsUseCase(page_size=500).execute(
            MintArtistsCommand(user_id=TEST_USER_ID, limit=2), uow
        )

        assert result.result.summary_metrics.get("tracks_processed") == 2
        # One page only, asked for exactly the limit — no page beyond it.
        assert repo.list_unlinked_credit_sources.await_count == 1
        assert repo.list_unlinked_credit_sources.await_args.kwargs["limit"] == 2

    async def test_dry_run_mints_nothing_and_reports_what_it_found(self):
        uow, _ = make_uow([[make_pair(0), make_pair(1)]])
        minter = uow.get_artist_minter.return_value

        result = await MintArtistsUseCase().execute(
            MintArtistsCommand(user_id=TEST_USER_ID, dry_run=True), uow
        )

        minter.mint.assert_not_awaited()
        uow.commit_batch.assert_not_awaited()
        metrics = result.result.summary_metrics
        assert metrics.get("tracks_processed") == 2
        # One credit per payload, each carrying a service artist id.
        assert metrics.get("credits_available") == 2
        assert result.result.metadata["dry_run"] is True

    async def test_a_fully_minted_library_is_a_no_op(self):
        uow, _ = make_uow([], count=0)
        minter = uow.get_artist_minter.return_value

        result = await MintArtistsUseCase().execute(
            MintArtistsCommand(user_id=TEST_USER_ID), uow
        )

        minter.mint.assert_not_awaited()
        assert result.result.summary_metrics.get("tracks_processed") == 0
