"""Tests for the `mixd tracks` negative-cache commands.

``rejections`` / ``dead-ids`` / ``unreject``. Focus: the *command-level*
contract — invalid or missing state produces a clean error rather than a stack
trace, and valid input renders the result. Use-case behavior itself is covered
by ``tests/unit/application/use_cases/test_unreject_mapping_candidate.py``.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

import pytest
from typer.testing import CliRunner

from src.application.use_cases.list_resolution_negatives import (
    ListResolutionNegativesResult,
)
from src.application.use_cases.unreject_mapping_candidate import (
    UnrejectMappingCandidateResult,
)
from src.domain.exceptions import NotFoundError
from src.domain.repositories.resolution import NegativeListing
from src.interface.cli.app import app

runner = CliRunner()

# The listing commands go through the use-case runner helper, imported
# function-scoped at the call site — so the patchable name lives on the use-case
# module, as with `run_list_tags` in test_tag_commands.py.
RUN_LIST_NEGATIVES = (
    "src.application.use_cases.list_resolution_negatives.run_list_resolution_negatives"
)


class TestUnrejectCommand:
    def test_withdraws_and_prints_confirmation(self) -> None:
        connector_track_id = uuid7()
        candidate_track_id = uuid7()
        mock_result = UnrejectMappingCandidateResult(
            connector_name="spotify",
            connector_track_id=connector_track_id,
            candidate_track_id=candidate_track_id,
        )

        with patch(
            "src.application.runner.execute_use_case",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = runner.invoke(
                app,
                [
                    "tracks",
                    "unreject",
                    str(connector_track_id),
                    str(candidate_track_id),
                ],
            )

        assert result.exit_code == 0
        assert "Withdrew rejection" in result.output
        assert "spotify" in result.output
        assert str(connector_track_id) in result.output
        assert str(candidate_track_id) in result.output
        assert "Traceback" not in result.output

    def test_no_active_rejection_reports_clean_error(self) -> None:
        with patch(
            "src.application.runner.execute_use_case",
            new_callable=AsyncMock,
            side_effect=NotFoundError("No active rejection for spotify:x x y"),
        ):
            result = runner.invoke(
                app, ["tracks", "unreject", str(uuid7()), str(uuid7())]
            )

        assert result.exit_code == 1
        assert "Failed to unreject mapping candidate" in result.output
        assert "No active rejection" in result.output
        assert "Traceback" not in result.output

    def test_invalid_uuid_reports_clean_error(self) -> None:
        result = runner.invoke(app, ["tracks", "unreject", "not-a-uuid", str(uuid7())])

        assert result.exit_code == 1
        assert "Traceback" not in result.output


class TestNegativeListingCommands:
    """``rejections`` and ``dead-ids`` share a query, a row shape, and a renderer.

    Both are parametrised over the same cases because that sharing is the point:
    a divergence in how either renders its listing would mean the two commands
    had quietly grown separate implementations again.
    """

    # The table carries two raw UUID columns (the ids the follow-up command
    # needs) plus identity columns — wider than the 80-column non-tty width Rich
    # falls back to, which would ellipsize the UUIDs these tests assert on.
    @pytest.fixture(autouse=True)
    def _wide_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLUMNS", "200")

    @staticmethod
    def _listing(consecutive_misses: int = 0) -> NegativeListing:
        return NegativeListing(
            connector_track_id=uuid7(),
            connector_name="spotify",
            connector_track_identifier="sp_123",
            track_id=uuid7(),
            track_title="Some Song",
            consecutive_misses=consecutive_misses,
        )

    @pytest.mark.parametrize("command", ["rejections", "dead-ids"])
    def test_renders_the_listing(self, command: str) -> None:
        listing = self._listing()

        with patch(
            RUN_LIST_NEGATIVES,
            return_value=ListResolutionNegativesResult(listings=[listing]),
        ):
            result = runner.invoke(app, ["tracks", command])

        assert result.exit_code == 0
        assert "spotify" in result.output
        assert "sp_123" in result.output
        assert "Some Song" in result.output
        assert str(listing.connector_track_id) in result.output
        assert str(listing.track_id) in result.output
        assert "Traceback" not in result.output

    @pytest.mark.parametrize(
        ("command", "empty_message"),
        [
            ("rejections", "No active rejections"),
            ("dead-ids", "No identifiers look dead"),
        ],
    )
    def test_empty_listing_says_so(self, command: str, empty_message: str) -> None:
        with patch(
            RUN_LIST_NEGATIVES,
            return_value=ListResolutionNegativesResult(listings=[]),
        ):
            result = runner.invoke(app, ["tracks", command])

        assert result.exit_code == 0
        assert empty_message in result.output

    @pytest.mark.parametrize(
        ("command", "error_message"),
        [
            ("rejections", "Failed to list rejections"),
            ("dead-ids", "Failed to list dead ids"),
        ],
    )
    def test_reports_failures_without_a_traceback(
        self, command: str, error_message: str
    ) -> None:
        with patch(
            RUN_LIST_NEGATIVES,
            side_effect=NotFoundError("Connector track deadbeef not found"),
        ):
            result = runner.invoke(app, ["tracks", command, str(uuid7())])

        assert result.exit_code == 1
        assert error_message in result.output
        assert "Traceback" not in result.output

    def test_rejections_points_at_the_command_that_withdraws_one(self) -> None:
        with patch(
            RUN_LIST_NEGATIVES,
            return_value=ListResolutionNegativesResult(listings=[self._listing()]),
        ):
            result = runner.invoke(app, ["tracks", "rejections"])

        assert "unreject" in result.output

    def test_dead_ids_shows_the_miss_count(self) -> None:
        """The count is what separates "worth a look" from "asked once"."""
        with patch(
            RUN_LIST_NEGATIVES,
            return_value=ListResolutionNegativesResult(listings=[self._listing(7)]),
        ):
            result = runner.invoke(app, ["tracks", "dead-ids"])

        assert "7" in result.output
        assert "relink" in result.output
