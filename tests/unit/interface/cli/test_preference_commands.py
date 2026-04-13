"""Tests for `mixd preference …` CLI commands.

Focus: the *command-level* contract — invalid input produces a clean
error (not a stack trace), and valid input delegates to the use case
with the expected arguments. Use-case behavior itself is covered by
unit tests elsewhere.
"""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid7

from typer.testing import CliRunner

from src.application.use_cases.set_track_preference import SetTrackPreferenceResult
from src.interface.cli.app import app
from tests.fixtures import make_track, make_track_preference

runner = CliRunner()


def _patched_preference_env(
    *,
    set_result: SetTrackPreferenceResult | None = None,
    resolved_track=None,
):
    """Patch the shared CLI helpers so commands run without a DB."""
    track = resolved_track or make_track()
    set_result = set_result or SetTrackPreferenceResult(
        track_id=track.id, state="yah", changed=True
    )
    stack = [
        patch(
            "src.interface.cli.preference_commands.resolve_track_ref",
            return_value=track,
        ),
        patch(
            "src.application.use_cases.set_track_preference.run_set_track_preference",
            return_value=set_result,
        ),
    ]
    return stack, track


class TestSetPreference:
    def test_invalid_state_prints_clean_error(self) -> None:
        result = runner.invoke(
            app, ["preference", "set", str(uuid7()), "--state", "superlike"]
        )
        # typer.BadParameter exits with code 2 and prints to stderr.
        assert result.exit_code == 2
        assert "superlike" in result.output
        assert "not a valid state" in result.output
        # No stack trace leaked.
        assert "Traceback" not in result.output

    def test_valid_state_delegates_to_use_case(self) -> None:
        stack, track = _patched_preference_env()
        with stack[0], stack[1] as run_set:
            result = runner.invoke(
                app, ["preference", "set", str(track.id), "--state", "yah"]
            )

        assert result.exit_code == 0
        assert "Set preference to" in result.output
        run_set.assert_called_once()
        kwargs = run_set.call_args.kwargs
        assert kwargs["track_id"] == track.id
        assert kwargs["state"] == "yah"

    def test_unchanged_preference_reports_no_change(self) -> None:
        track = make_track()
        stack, _ = _patched_preference_env(
            set_result=SetTrackPreferenceResult(
                track_id=track.id, state="yah", changed=False
            ),
            resolved_track=track,
        )
        with stack[0], stack[1]:
            result = runner.invoke(
                app, ["preference", "set", str(track.id), "--state", "yah"]
            )

        assert result.exit_code == 0
        assert "no change" in result.output


class TestClearPreference:
    def test_clears_via_use_case(self) -> None:
        track = make_track()
        stack, _ = _patched_preference_env(
            set_result=SetTrackPreferenceResult(
                track_id=track.id, state=None, changed=True
            ),
            resolved_track=track,
        )
        with stack[0], stack[1] as run_set:
            result = runner.invoke(app, ["preference", "clear", str(track.id)])

        assert result.exit_code == 0
        assert "cleared" in result.output
        kwargs = run_set.call_args.kwargs
        assert kwargs["state"] is None


class TestListPreferences:
    def test_invalid_state_clean_error(self) -> None:
        result = runner.invoke(app, ["preference", "list", "--state", "nope"])
        assert result.exit_code == 2
        assert "not a valid state" in result.output
        assert "Traceback" not in result.output

    def test_empty_list_shows_message(self) -> None:
        with patch("src.application.runner.execute_use_case", return_value=[]):
            result = runner.invoke(app, ["preference", "list", "--state", "star"])
        assert result.exit_code == 0
        assert "No tracks with preference 'star'" in result.output

    def test_renders_tracks_and_timestamps(self) -> None:
        tracks = [make_track(title=f"Song {i}") for i in range(2)]
        rows = [
            (tracks[0], datetime(2026, 4, 10, 14, 30, tzinfo=UTC)),
            (tracks[1], datetime(2026, 4, 11, 9, 0, tzinfo=UTC)),
        ]
        with patch("src.application.runner.execute_use_case", return_value=rows):
            result = runner.invoke(app, ["preference", "list", "--state", "yah"])

        assert result.exit_code == 0
        assert "Song 0" in result.output
        assert "Song 1" in result.output
        assert "2026-04-10" in result.output


class TestStats:
    def test_renders_counts_per_state(self) -> None:
        counts = {"star": 3, "yah": 2, "hmm": 1, "nah": 0}
        with patch("src.application.runner.execute_use_case", return_value=counts):
            result = runner.invoke(app, ["preference", "stats"])

        assert result.exit_code == 0
        for state, count in counts.items():
            assert state in result.output
            assert str(count) in result.output
        assert "Total" in result.output

    def test_empty_counts_shows_message(self) -> None:
        with patch("src.application.runner.execute_use_case", return_value={}):
            result = runner.invoke(app, ["preference", "stats"])

        assert result.exit_code == 0
        assert "No preferences set yet" in result.output


# Silence the linter — fixture factory is imported for type / runtime usage
# above but ruff may flag when imports are only used inside closures.
_ = make_track_preference
