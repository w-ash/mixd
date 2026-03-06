"""Tests for workflow definition loader.

Tests JSON parsing into typed WorkflowDef entities, directory listing,
and error handling for invalid/missing files.
"""

import json

import pytest

from src.application.workflows.workflow_loader import list_workflow_defs, load_workflow_def
from src.domain.entities.workflow import WorkflowDef


class TestLoadWorkflowDef:
    """Tests for load_workflow_def single file parsing."""

    def test_loads_valid_json(self, tmp_path):
        """Valid JSON file parses into WorkflowDef."""
        data = {
            "id": "test_wf",
            "name": "Test Workflow",
            "description": "A test",
            "version": "1.0",
            "tasks": [
                {"id": "src_1", "type": "source.playlist", "config": {"playlist_id": "abc"}},
                {"id": "dest_1", "type": "destination.create_playlist", "config": {"name": "Out"}, "upstream": ["src_1"]},
            ],
        }
        path = tmp_path / "test_wf.json"
        path.write_text(json.dumps(data))

        result = load_workflow_def(path)

        assert isinstance(result, WorkflowDef)
        assert result.id == "test_wf"
        assert result.name == "Test Workflow"
        assert len(result.tasks) == 2
        assert result.tasks[0].config == {"playlist_id": "abc"}
        assert result.tasks[1].upstream == ["src_1"]

    def test_defaults_id_from_stem(self, tmp_path):
        """If JSON has no 'id' field, uses filename stem."""
        data = {"name": "No ID", "tasks": [{"id": "t1", "type": "source.liked_tracks"}]}
        path = tmp_path / "my_workflow.json"
        path.write_text(json.dumps(data))

        result = load_workflow_def(path)
        assert result.id == "my_workflow"

    def test_invalid_json_raises(self, tmp_path):
        """Invalid JSON raises JSONDecodeError."""
        path = tmp_path / "bad.json"
        path.write_text("not json {{{")

        with pytest.raises(json.JSONDecodeError):
            load_workflow_def(path)

    def test_missing_file_raises(self, tmp_path):
        """Non-existent path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_workflow_def(tmp_path / "nonexistent.json")

    def test_result_key_parsed(self, tmp_path):
        """result_key field on tasks is correctly parsed."""
        data = {
            "name": "RK",
            "tasks": [{"id": "t1", "type": "source.playlist", "result_key": "my_result"}],
        }
        path = tmp_path / "rk.json"
        path.write_text(json.dumps(data))

        result = load_workflow_def(path)
        assert result.tasks[0].result_key == "my_result"


class TestListWorkflowDefs:
    """Tests for list_workflow_defs directory scanning."""

    def test_lists_all_json_files(self, tmp_path):
        """All valid .json files in directory are loaded."""
        for i in range(3):
            data = {"name": f"WF {i}", "tasks": [{"id": "t1", "type": "source.liked_tracks"}]}
            (tmp_path / f"wf_{i}.json").write_text(json.dumps(data))

        result = list_workflow_defs(tmp_path)
        assert len(result) == 3
        assert all(isinstance(wf, WorkflowDef) for wf in result)

    def test_skips_invalid_files(self, tmp_path):
        """Invalid JSON files are skipped, valid ones still loaded."""
        (tmp_path / "good.json").write_text(
            json.dumps({"name": "Good", "tasks": [{"id": "t1", "type": "source.liked_tracks"}]})
        )
        (tmp_path / "bad.json").write_text("not json")

        result = list_workflow_defs(tmp_path)
        assert len(result) == 1
        assert result[0].name == "Good"

    def test_nonexistent_directory_returns_empty(self, tmp_path):
        """Non-existent directory returns empty list."""
        result = list_workflow_defs(tmp_path / "nonexistent")
        assert result == []

    def test_empty_directory_returns_empty(self, tmp_path):
        """Empty directory returns empty list."""
        result = list_workflow_defs(tmp_path)
        assert result == []
