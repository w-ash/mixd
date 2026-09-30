"""Tests for `mixd artists …` CLI commands.

Command-level contract only: valid input delegates to the use case with the
expected arguments, and bad input produces a clean one-liner rather than a
stack trace. Use-case behavior is covered by its own unit tests.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

from typer.testing import CliRunner

from src.application.use_cases.favorite_artist import FavoriteArtistResult
from src.application.use_cases.list_artists import ListArtistsResult
from src.application.use_cases.mint_artists import MintArtistsResult
from src.domain.entities.operations import OperationResult
from src.domain.exceptions import NotFoundError
from src.interface.cli.app import app
from tests.fixtures import make_artist

runner = CliRunner()


class TestListArtists:
    def test_renders_rows(self) -> None:
        artist = make_artist("Radiohead")
        result_obj = ListArtistsResult(
            artists=[artist],
            total=1,
            limit=50,
            offset=0,
            track_counts={artist.id: 7},
            favorited_ids={artist.id},
            connector_names={artist.id: ["spotify"]},
        )
        with patch(
            "src.application.use_cases.list_artists.run_list_artists",
            return_value=result_obj,
        ) as run:
            result = runner.invoke(app, ["artists", "list", "--search", "radio"])

        assert result.exit_code == 0
        assert "Radiohead" in result.output
        assert "spotify" in result.output
        assert run.call_args.kwargs["search"] == "radio"
        assert run.call_args.kwargs["favorites_only"] is False

    def test_favorites_flag_is_forwarded(self) -> None:
        empty = ListArtistsResult(artists=[], total=0, limit=50, offset=0)
        with patch(
            "src.application.use_cases.list_artists.run_list_artists",
            return_value=empty,
        ) as run:
            result = runner.invoke(app, ["artists", "list", "--favorites"])

        assert result.exit_code == 0
        assert "No artists found" in result.output
        assert run.call_args.kwargs["favorites_only"] is True

    def test_invalid_sort_prints_clean_error(self) -> None:
        result = runner.invoke(app, ["artists", "list", "--sort", "loudest"])

        assert result.exit_code == 2
        assert "Traceback" not in result.output


class TestFavoriteArtist:
    def test_favorites_by_id(self) -> None:
        artist_id = uuid7()
        with patch(
            "src.application.use_cases.favorite_artist.run_favorite_artist",
            return_value=FavoriteArtistResult(
                artist_id=artist_id, is_favorited=True, changed=True
            ),
        ) as run:
            result = runner.invoke(app, ["artists", "favorite", str(artist_id)])

        assert result.exit_code == 0
        assert "Favorited artist" in result.output
        assert run.call_args.kwargs["is_favorited"] is True
        assert run.call_args.kwargs["artist_id"] == artist_id

    def test_repeat_favorite_reports_no_change(self) -> None:
        artist_id = uuid7()
        with patch(
            "src.application.use_cases.favorite_artist.run_favorite_artist",
            return_value=FavoriteArtistResult(
                artist_id=artist_id, is_favorited=True, changed=False
            ),
        ):
            result = runner.invoke(app, ["artists", "favorite", str(artist_id)])

        assert result.exit_code == 0
        assert "no change" in result.output

    def test_unfavorite_forwards_false(self) -> None:
        artist_id = uuid7()
        with patch(
            "src.application.use_cases.favorite_artist.run_favorite_artist",
            return_value=FavoriteArtistResult(
                artist_id=artist_id, is_favorited=False, changed=True
            ),
        ) as run:
            result = runner.invoke(app, ["artists", "unfavorite", str(artist_id)])

        assert result.exit_code == 0
        assert run.call_args.kwargs["is_favorited"] is False

    def test_non_uuid_prints_clean_error(self) -> None:
        result = runner.invoke(app, ["artists", "favorite", "radiohead"])

        assert result.exit_code == 2
        assert "Traceback" not in result.output

    def test_unknown_artist_exits_one(self) -> None:
        with patch(
            "src.application.use_cases.favorite_artist.run_favorite_artist",
            side_effect=NotFoundError("Artist not found"),
        ):
            result = runner.invoke(app, ["artists", "favorite", str(uuid7())])

        assert result.exit_code == 1
        assert "Traceback" not in result.output


class TestMintArtists:
    """``mixd artists mint`` — the launch contract, not the walk."""

    @staticmethod
    def _result() -> MintArtistsResult:
        result = OperationResult(operation_name="Artist Minting", execution_time=0.0)
        result.summary_metrics.add("artists_created", 4, "Artists Created")
        return MintArtistsResult(result=result)

    def test_forwards_the_limit_and_renders_the_summary(self) -> None:
        with patch(
            "src.application.use_cases.mint_artists.run_mint_artists",
            new=AsyncMock(return_value=self._result()),
        ) as run:
            result = runner.invoke(app, ["artists", "mint", "--limit", "25"])

        assert result.exit_code == 0
        assert "Artists Created" in result.output
        assert run.await_args.kwargs["limit"] == 25
        assert run.await_args.kwargs["dry_run"] is False

    def test_dry_run_says_nothing_was_written(self) -> None:
        with patch(
            "src.application.use_cases.mint_artists.run_mint_artists",
            new=AsyncMock(return_value=self._result()),
        ) as run:
            result = runner.invoke(app, ["artists", "mint", "--dry-run"])

        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert run.await_args.kwargs["dry_run"] is True

    def test_zero_limit_prints_clean_error(self) -> None:
        result = runner.invoke(app, ["artists", "mint", "--limit", "0"])

        assert result.exit_code == 2
        assert "Traceback" not in result.output
