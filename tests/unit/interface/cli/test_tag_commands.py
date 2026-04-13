"""Tests for `mixd tag …` CLI commands.

Focus: the *command-level* contract — invalid input produces a clean
error (not a stack trace), and valid input delegates to the use case
with the expected arguments. Use-case behavior itself is covered by
unit tests elsewhere.
"""

from unittest.mock import patch
from uuid import uuid7

import attrs
from typer.testing import CliRunner

from src.application.use_cases.batch_tag_tracks import BatchTagTracksResult
from src.application.use_cases.list_tags import ListTagsResult
from src.application.use_cases.tag_track import TagTrackResult
from src.application.use_cases.untag_track import UntagTrackResult
from src.domain.entities import Playlist, Track
from src.interface.cli.app import app
from tests.fixtures import make_playlist_with_entries, make_track

runner = CliRunner()


def _patched_tag_env(
    *,
    tag_result: TagTrackResult | None = None,
    untag_result: UntagTrackResult | None = None,
    resolved_track: Track | None = None,
):
    """Patch the shared CLI helpers + use-case runners so commands run without a DB.

    Returns ``(stack, track)`` — a list of context managers to enter and the
    resolved track. Centralizes the patch-target strings so a module rename
    only needs to be fixed in one place.
    """
    track = resolved_track or make_track()
    stack = [
        patch(
            "src.interface.cli.tag_commands.resolve_track_ref",
            return_value=track,
        ),
    ]
    if tag_result is not None:
        stack.append(
            patch(
                "src.application.use_cases.tag_track.run_tag_track",
                return_value=tag_result,
            )
        )
    if untag_result is not None:
        stack.append(
            patch(
                "src.application.use_cases.untag_track.run_untag_track",
                return_value=untag_result,
            )
        )
    return stack, track


# ---------------------------------------------------------------------------
# `mixd tag add`
# ---------------------------------------------------------------------------


class TestAddTag:
    def test_invalid_tag_prints_clean_error(self) -> None:
        # 65-char tag trips the length rule in normalize_tag.
        result = runner.invoke(app, ["tag", "add", str(uuid7()), "a" * 65])

        assert result.exit_code == 2
        assert "64 characters" in result.output
        assert "Traceback" not in result.output

    def test_invalid_chars_prints_clean_error(self) -> None:
        result = runner.invoke(app, ["tag", "add", str(uuid7()), "mood/chill"])

        assert result.exit_code == 2
        assert "invalid characters" in result.output
        assert "Traceback" not in result.output

    def test_valid_tag_delegates_to_use_case(self) -> None:
        track = make_track()
        stack, _ = _patched_tag_env(
            resolved_track=track,
            tag_result=TagTrackResult(
                track_id=track.id, tag="mood:chill", changed=True
            ),
        )
        with stack[0], stack[1] as run_tag:
            result = runner.invoke(app, ["tag", "add", str(track.id), "mood:chill"])

        assert result.exit_code == 0
        assert "Added tag" in result.output
        assert "mood:chill" in result.output
        kwargs = run_tag.call_args.kwargs
        assert kwargs["track_id"] == track.id
        assert kwargs["raw_tag"] == "mood:chill"

    def test_already_tagged_reports_no_change(self) -> None:
        track = make_track()
        stack, _ = _patched_tag_env(
            resolved_track=track,
            tag_result=TagTrackResult(
                track_id=track.id, tag="mood:chill", changed=False
            ),
        )
        with stack[0], stack[1]:
            result = runner.invoke(app, ["tag", "add", str(track.id), "mood:chill"])

        assert result.exit_code == 0
        assert "already on" in result.output


# ---------------------------------------------------------------------------
# `mixd tag remove`
# ---------------------------------------------------------------------------


class TestRemoveTag:
    def test_removes_via_use_case(self) -> None:
        track = make_track()
        stack, _ = _patched_tag_env(
            resolved_track=track,
            untag_result=UntagTrackResult(
                track_id=track.id, tag="mood:chill", changed=True
            ),
        )
        with stack[0], stack[1] as run_untag:
            result = runner.invoke(app, ["tag", "remove", str(track.id), "mood:chill"])

        assert result.exit_code == 0
        assert "Removed tag" in result.output
        assert run_untag.call_args.kwargs["raw_tag"] == "mood:chill"

    def test_missing_tag_reports_no_change(self) -> None:
        track = make_track()
        stack, _ = _patched_tag_env(
            resolved_track=track,
            untag_result=UntagTrackResult(
                track_id=track.id, tag="mood:chill", changed=False
            ),
        )
        with stack[0], stack[1]:
            result = runner.invoke(app, ["tag", "remove", str(track.id), "mood:chill"])

        assert result.exit_code == 0
        assert "not on" in result.output


# ---------------------------------------------------------------------------
# `mixd tag list`
# ---------------------------------------------------------------------------


class TestListTags:
    def test_empty_without_query_shows_message(self) -> None:
        with patch(
            "src.application.use_cases.list_tags.run_list_tags",
            return_value=ListTagsResult(tags=[]),
        ):
            result = runner.invoke(app, ["tag", "list"])

        assert result.exit_code == 0
        assert "No tags yet" in result.output

    def test_empty_with_query_shows_query_in_message(self) -> None:
        with patch(
            "src.application.use_cases.list_tags.run_list_tags",
            return_value=ListTagsResult(tags=[]),
        ):
            result = runner.invoke(app, ["tag", "list", "--query", "chi"])

        assert result.exit_code == 0
        assert "No tags matching 'chi'" in result.output

    def test_renders_tags_with_counts(self) -> None:
        tags = [("mood:chill", 12), ("banger", 5)]
        with patch(
            "src.application.use_cases.list_tags.run_list_tags",
            return_value=ListTagsResult(tags=tags),
        ) as run_list:
            result = runner.invoke(app, ["tag", "list", "--limit", "10"])

        assert result.exit_code == 0
        assert "mood:chill" in result.output
        assert "banger" in result.output
        assert "12" in result.output
        assert "5" in result.output
        kwargs = run_list.call_args.kwargs
        assert kwargs["limit"] == 10
        assert kwargs["query"] is None


# ---------------------------------------------------------------------------
# `mixd tag tracks <tag>`
# ---------------------------------------------------------------------------


class TestTracksForTag:
    def test_invalid_tag_prints_clean_error(self) -> None:
        result = runner.invoke(app, ["tag", "tracks", ":leading-colon"])
        assert result.exit_code == 2
        assert "start or end with ':'" in result.output
        assert "Traceback" not in result.output

    def test_empty_result_shows_message(self) -> None:
        with patch("src.application.runner.execute_use_case", return_value=[]):
            result = runner.invoke(app, ["tag", "tracks", "mood:chill"])

        assert result.exit_code == 0
        assert "No tracks tagged 'mood:chill'" in result.output

    def test_renders_track_listing(self) -> None:
        tracks = [make_track(title=f"Song {i}") for i in range(2)]
        with patch("src.application.runner.execute_use_case", return_value=tracks):
            result = runner.invoke(app, ["tag", "tracks", "mood:chill"])

        assert result.exit_code == 0
        assert "Song 0" in result.output
        assert "Song 1" in result.output
        assert "mood:chill" in result.output


# ---------------------------------------------------------------------------
# `mixd tag batch <tag> --playlist <name>`
# ---------------------------------------------------------------------------


class TestBatchTag:
    def test_invalid_tag_prints_clean_error(self) -> None:
        result = runner.invoke(
            app, ["tag", "batch", "bad/tag", "--playlist", "My Playlist"]
        )
        assert result.exit_code == 2
        assert "invalid characters" in result.output
        assert "Traceback" not in result.output

    def test_empty_playlist_is_no_op(self) -> None:
        # make_playlist_with_entries clobbers track_ids=[] with defaults
        # (`[] or [...]` picks the fallback), so construct empty directly.
        playlist = Playlist(name="Empty", entries=[])
        with patch(
            "src.interface.cli.tag_commands.resolve_playlist_ref",
            return_value=playlist,
        ):
            result = runner.invoke(
                app,
                ["tag", "batch", "mood:chill", "--playlist", str(playlist.id)],
            )

        assert result.exit_code == 0
        assert "no tracks" in result.output

    def test_uses_already_loaded_entries_without_refetch(self) -> None:
        # make_playlist_with_entries leaves track_count at its default (0);
        # set it to match the number of loaded entries so batch_tag sees
        # this as a fully-hydrated playlist, not a lightweight stub.
        playlist = make_playlist_with_entries()
        playlist = attrs.evolve(playlist, track_count=len(playlist.entries))
        track_ids = [entry.track.id for entry in playlist.entries]
        batch_result = BatchTagTracksResult(
            tag="mood:chill", requested=len(track_ids), tagged=2
        )

        with (
            patch(
                "src.interface.cli.tag_commands.resolve_playlist_ref",
                return_value=playlist,
            ),
            patch(
                "src.application.runner.execute_use_case",
            ) as execute_use_case,
            patch(
                "src.application.use_cases.batch_tag_tracks.run_batch_tag_tracks",
                return_value=batch_result,
            ) as run_batch,
        ):
            result = runner.invoke(
                app,
                ["tag", "batch", "mood:chill", "--playlist", str(playlist.id)],
            )

        assert result.exit_code == 0
        # UUID-resolved playlists come back with entries loaded — batch_tag
        # must use them directly instead of issuing a redundant re-fetch.
        execute_use_case.assert_not_called()
        # Summary: succeeded=2, skipped=(requested - tagged)=1.
        assert "Succeeded" in result.output
        assert "Skipped" in result.output
        assert "Failed" in result.output
        assert "Total" in result.output
        kwargs = run_batch.call_args.kwargs
        assert kwargs["raw_tag"] == "mood:chill"
        assert list(kwargs["track_ids"]) == track_ids
