"""Unit tests for CLI helper utilities.

Tests cover:
- Date parsing and validation
- File path validation
- User input prompting
- Progress context integration
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from src.application.services.batch_file_import_service import ImportProgressSpec
from src.interface.cli.cli_helpers import (
    console as cli_console,
    parse_date_string,
    prompt_batch_size,
    run_import_with_progress,
    validate_date_range,
    validate_file_path,
)
from tests.fixtures import fake_run_async


class TestParseDateString:
    """Test date string parsing with timezone handling."""

    def test_parse_valid_date_string(self):
        """Parse valid YYYY-MM-DD format returns UTC datetime."""
        result = parse_date_string("2025-03-15", "test-date")
        assert result == datetime(2025, 3, 15, tzinfo=UTC)

    @pytest.mark.parametrize("raw", [None, ""])
    def test_absent_date_returns_none(self, raw):
        """An omitted option (None) and an empty string both mean no date."""
        assert parse_date_string(raw, "test-date") is None

    @pytest.mark.parametrize("raw", ["2025/03/15", "2025-13-45"])
    def test_malformed_or_impossible_date_exits_1(self, raw):
        """A wrong separator and an impossible month/day both exit with code 1."""
        with pytest.raises(typer.Exit) as exc_info:
            parse_date_string(raw, "test-date")
        assert exc_info.value.exit_code == 1


class TestValidateDateRange:
    """Test date range validation."""

    @pytest.mark.parametrize(
        ("from_date", "to_date"),
        [
            (datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC)),
            (datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
            (None, None),
            (None, datetime(2025, 1, 1, tzinfo=UTC)),
            (datetime(2025, 1, 1, tzinfo=UTC), None),
        ],
        ids=["ordered", "same-day", "both-open", "open-start", "open-end"],
    )
    def test_accepted_range_does_not_exit_or_warn(self, from_date, to_date):
        """Ordered, same-day, and open-ended ranges pass silently."""
        with cli_console.capture() as capture:
            validate_date_range(from_date, to_date)
        assert capture.get() == ""

    def test_inverted_range_warns_and_exits_1(self):
        """From date after to date prints the reason and exits with code 1."""
        from_date = datetime(2025, 12, 31, tzinfo=UTC)
        to_date = datetime(2025, 1, 1, tzinfo=UTC)
        with cli_console.capture() as capture, pytest.raises(typer.Exit) as exc_info:
            validate_date_range(from_date, to_date)
        assert exc_info.value.exit_code == 1
        assert "from-date cannot be later than to-date" in capture.get()


class TestPromptBatchSize:
    """Test batch size prompting."""

    @patch("src.interface.cli.cli_helpers.Prompt.ask")
    def test_prompt_with_valid_integer_returns_int(self, mock_ask):
        """User input of valid integer returns int."""
        mock_ask.return_value = "100"
        result = prompt_batch_size()
        assert result == 100

    @patch("src.interface.cli.cli_helpers.Prompt.ask")
    def test_prompt_with_empty_returns_none(self, mock_ask):
        """User input of empty string returns None (default)."""
        mock_ask.return_value = ""
        result = prompt_batch_size()
        assert result is None

    @patch("src.interface.cli.cli_helpers.Prompt.ask")
    def test_prompt_uses_correct_message(self, mock_ask):
        """Prompt message is descriptive."""
        mock_ask.return_value = ""
        prompt_batch_size()
        mock_ask.assert_called_once_with(
            "Batch size (leave empty for default)",
            default="",
        )


class TestValidateFilePath:
    """Test file path validation."""

    def test_valid_file_path_passes(self, tmp_path):
        """Valid existing file passes without error."""
        test_file = tmp_path / "test.json"
        test_file.write_text("{}")
        # Should not raise
        validate_file_path(test_file)

    def test_missing_file_raises_exit(self, tmp_path):
        """Missing file raises typer.Exit."""
        missing_file = tmp_path / "missing.json"
        with pytest.raises(typer.Exit) as exc_info:
            validate_file_path(missing_file)
        assert exc_info.value.exit_code == 1

    def test_directory_raises_exit(self, tmp_path):
        """Directory path raises typer.Exit."""
        with pytest.raises(typer.Exit) as exc_info:
            validate_file_path(tmp_path)
        assert exc_info.value.exit_code == 1


class TestRunImportWithProgress:
    """The import spec reaches the use case whole, under the live broker."""

    def test_every_spec_field_and_the_broker_reach_run_import(self):
        broker = MagicMock()
        ctx = MagicMock()
        ctx.get_progress_broker.return_value = broker
        from_date = datetime(2025, 1, 1, tzinfo=UTC)
        to_date = datetime(2025, 6, 30, tzinfo=UTC)
        spec = ImportProgressSpec(
            service="lastfm",
            mode="incremental",
            limit=500,
            username="rj",
            file_path=Path("/exports/history.json"),
            confirm=True,
            from_date=from_date,
            to_date=to_date,
            batch_size=200,
        )
        outcome = object()
        run_import = AsyncMock(return_value=outcome)

        with (
            patch(
                "src.interface.cli.cli_helpers.progress_coordination_context"
            ) as mock_context,
            patch("src.interface.cli.cli_helpers.run_async", side_effect=asyncio.run),
            patch(
                "src.application.use_cases.import_play_history.run_import", run_import
            ),
            patch(
                "src.interface.cli.cli_helpers.get_cli_user_id", return_value="cli-user"
            ),
        ):
            mock_context.return_value.__aenter__.return_value = ctx
            result = run_import_with_progress(spec)

        assert result is outcome
        run_import.assert_awaited_once_with(
            user_id="cli-user",
            service="lastfm",
            mode="incremental",
            limit=500,
            username="rj",
            file_path=Path("/exports/history.json"),
            confirm=True,
            from_date=from_date,
            to_date=to_date,
            progress_emitter=broker,
            batch_size=200,
        )


# ---------------------------------------------------------------------------
# Epic 6 additions: validators, resolvers, renderers
# ---------------------------------------------------------------------------


class TestValidatePreferenceState:
    def test_accepts_valid_state(self):
        from src.interface.cli.cli_helpers import validate_preference_state

        assert validate_preference_state("star") == "star"

    def test_rejects_invalid_state_with_bad_parameter(self):
        from src.interface.cli.cli_helpers import validate_preference_state

        with pytest.raises(typer.BadParameter) as exc_info:
            validate_preference_state("superlike")
        assert "superlike" in str(exc_info.value)


class TestValidateTag:
    def test_returns_normalized_form(self):
        from src.interface.cli.cli_helpers import validate_tag

        assert validate_tag("Mood:Chill") == "mood:chill"

    def test_wraps_value_error_in_bad_parameter(self):
        from src.interface.cli.cli_helpers import validate_tag

        with pytest.raises(typer.BadParameter):
            validate_tag("cafe!")


class TestResolveTrackRef:
    def test_uuid_path_fetches_by_id(self):
        from uuid import uuid7

        from src.interface.cli.cli_helpers import resolve_track_ref
        from tests.fixtures import make_track

        track = make_track()
        with patch(
            "src.interface.cli.cli_helpers.run_async", side_effect=fake_run_async(track)
        ):
            result = resolve_track_ref(str(uuid7()), user_id="u1")
        assert result is track

    def test_search_returns_unique_match(self):
        from src.interface.cli.cli_helpers import resolve_track_ref
        from tests.fixtures import make_track

        track = make_track(title="Creep")
        with patch(
            "src.interface.cli.cli_helpers.run_async",
            side_effect=fake_run_async([track]),
        ):
            result = resolve_track_ref("Creep", user_id="u1")
        assert result is track

    def test_empty_search_raises_bad_parameter(self):
        from src.interface.cli.cli_helpers import resolve_track_ref

        with patch(
            "src.interface.cli.cli_helpers.run_async", side_effect=fake_run_async([])
        ):
            with pytest.raises(typer.BadParameter, match="No track matching"):
                resolve_track_ref("nosuchtrack", user_id="u1")

    def test_ambiguous_search_lists_candidates(self):
        from src.interface.cli.cli_helpers import resolve_track_ref
        from tests.fixtures import make_tracks

        candidates = make_tracks(count=3)
        with patch(
            "src.interface.cli.cli_helpers.run_async",
            side_effect=fake_run_async(candidates),
        ):
            with pytest.raises(typer.BadParameter) as exc_info:
                resolve_track_ref("song", user_id="u1")
        message = str(exc_info.value)
        assert "multiple tracks" in message
        for t in candidates:
            assert str(t.id) in message


class TestResolvePlaylistRef:
    def test_exact_name_match_beats_substring_matches(self):
        """ "chill" names "Chill" even though "Chill Night" also contains it."""
        from src.interface.cli.cli_helpers import resolve_playlist_ref
        from tests.fixtures import make_playlist

        exact = make_playlist(name="Chill")
        longer = make_playlist(name="Chill Night")
        with patch(
            "src.interface.cli.cli_helpers.run_async",
            side_effect=fake_run_async([longer, exact]),
        ):
            assert resolve_playlist_ref("chill", user_id="u1") is exact

    def test_no_match_raises(self):
        from src.interface.cli.cli_helpers import resolve_playlist_ref

        with patch(
            "src.interface.cli.cli_helpers.run_async", side_effect=fake_run_async([])
        ):
            with pytest.raises(typer.BadParameter, match="No playlist matching"):
                resolve_playlist_ref("nothing", user_id="u1")

    def test_ambiguous_suggests_uuid(self):
        from src.interface.cli.cli_helpers import resolve_playlist_ref
        from tests.fixtures import make_playlist

        playlists = [
            make_playlist(name="Chill Morning"),
            make_playlist(name="Chill Night"),
        ]
        with patch(
            "src.interface.cli.cli_helpers.run_async",
            side_effect=fake_run_async(playlists),
        ):
            with pytest.raises(typer.BadParameter, match="multiple playlists"):
                resolve_playlist_ref("chill", user_id="u1")


class TestRenderTracksTable:
    def test_default_columns_show_title_artists_and_id(self):
        from src.interface.cli.cli_helpers import render_tracks_table
        from tests.fixtures import make_track

        track = make_track(title="Creep", artist="Radiohead")
        table = render_tracks_table([track], title="Test")

        assert [col.header for col in table.columns] == ["Title", "Artist", "ID"]
        assert [col._cells[0] for col in table.columns] == [
            "Creep",
            "Radiohead",
            str(track.id),
        ]

    def test_extra_column_sits_before_id_with_its_accessor_value(self):
        from src.interface.cli.cli_helpers import render_tracks_table
        from tests.fixtures import make_track

        track = make_track(title="Creep", artist="Radiohead")
        table = render_tracks_table(
            [track],
            title="Test",
            extra_columns=[("Plays", lambda _t: "42")],
        )

        assert [col.header for col in table.columns] == [
            "Title",
            "Artist",
            "Plays",
            "ID",
        ]
        assert [col._cells[0] for col in table.columns] == [
            "Creep",
            "Radiohead",
            "42",
            str(track.id),
        ]


class TestBatchOperationResult:
    def test_total_counts_all_outcomes(self):
        from src.interface.cli.cli_helpers import BatchOperationResult

        result = BatchOperationResult(
            succeeded=5, skipped=2, failed=["bad-id", "timeout"]
        )
        assert result.total == 9

    def test_render_summary_uses_counts(self):
        from src.interface.cli.cli_helpers import (
            BatchOperationResult,
            render_batch_summary,
        )

        table = render_batch_summary(
            BatchOperationResult(succeeded=3, skipped=1, failed=[]),
            title="Batch Tag",
        )
        assert str(table.title) == "Batch Tag"
        assert list(table.columns[0]._cells) == [
            "Succeeded",
            "Skipped",
            "Failed",
            "Total",
        ]
        assert list(table.columns[1]._cells) == ["3", "1", "0", "4"]


class TestValidateSort:
    def test_a_declared_key_comes_back_as_itself(self):
        from src.domain.repositories.artist import ARTIST_SORTS
        from src.interface.cli.cli_helpers import validate_sort

        assert validate_sort("name_desc", ARTIST_SORTS, default="name_asc") == (
            "name_desc"
        )

    def test_an_absent_option_takes_the_default(self):
        from src.domain.repositories.artist import ARTIST_SORTS
        from src.interface.cli.cli_helpers import validate_sort

        assert validate_sort(None, ARTIST_SORTS, default="name_asc") == "name_asc"

    def test_an_unknown_key_is_a_bad_parameter_naming_the_choices(self):
        import typer

        from src.domain.repositories.artist import ARTIST_SORTS
        from src.interface.cli.cli_helpers import validate_sort

        with pytest.raises(typer.BadParameter, match="not a valid sort") as exc:
            _ = validate_sort("loudest", ARTIST_SORTS, default="name_asc")
        assert "name_asc" in str(exc.value)


class TestRunWithProgress:
    """``run_async`` is replaced by ``asyncio.run`` so the factory really runs."""

    def test_the_factory_gets_the_context_broker(self):
        from src.interface.cli.cli_helpers import run_with_progress

        broker = MagicMock()
        ctx = MagicMock()
        ctx.get_progress_broker.return_value = broker
        seen: list[object] = []

        async def _factory(emitter):
            seen.append(emitter)
            return "done"

        with (
            patch(
                "src.interface.cli.cli_helpers.progress_coordination_context"
            ) as mock_context,
            patch("src.interface.cli.cli_helpers.run_async", side_effect=asyncio.run),
        ):
            mock_context.return_value.__aenter__.return_value = ctx
            assert run_with_progress(_factory) == "done"
        assert seen == [broker]

    def test_without_a_broker_the_fallback_emitter_is_used(self):
        from src.domain.entities.progress import NullProgressEmitter
        from src.interface.cli.cli_helpers import run_with_progress

        ctx = MagicMock()
        ctx.get_progress_broker.return_value = None
        fallback = NullProgressEmitter()
        seen: list[object] = []

        async def _factory(emitter):
            seen.append(emitter)
            return 1

        with (
            patch(
                "src.interface.cli.cli_helpers.progress_coordination_context"
            ) as mock_context,
            patch("src.interface.cli.cli_helpers.run_async", side_effect=asyncio.run),
        ):
            mock_context.return_value.__aenter__.return_value = ctx
            assert run_with_progress(_factory, fallback_emitter=fallback) == 1
        assert seen == [fallback]
