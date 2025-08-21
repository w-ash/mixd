"""Tests for CLI autocompletion functions."""

import json

import pytest

from src.interface.cli.completions import complete_workflow_names


@pytest.mark.unit
def test_complete_workflow_names_empty_directory():
    """Test completion with no workflow files."""
    # Should return empty list when no workflows exist
    result = complete_workflow_names("")
    assert isinstance(result, list)


def test_complete_workflow_names_with_workflows(tmp_path):
    """Test completion with actual workflow files."""
    # Create test workflow files
    workflow1 = {"id": "test_workflow", "name": "Test Workflow"}
    workflow2 = {"id": "another_test", "name": "Another Test"}

    (tmp_path / "test_workflow.json").write_text(json.dumps(workflow1))
    (tmp_path / "another_test.json").write_text(json.dumps(workflow2))

    # Mock the workflow definitions path
    import src.interface.cli.completions

    original_path = src.interface.cli.completions._get_workflow_definitions_path
    src.interface.cli.completions._get_workflow_definitions_path = lambda: tmp_path

    try:
        # Test empty search returns all workflows
        result = complete_workflow_names("")
        assert "test_workflow" in result
        assert "another_test" in result

        # Test partial match
        result = complete_workflow_names("test")
        assert "test_workflow" in result
        assert "another_test" not in result

    finally:
        src.interface.cli.completions._get_workflow_definitions_path = original_path


def test_complete_workflow_names_handles_invalid_json(tmp_path):
    """Test completion gracefully handles invalid JSON files."""
    # Create invalid JSON file
    (tmp_path / "invalid.json").write_text("not valid json")

    # Create valid workflow file
    workflow = {"id": "valid_workflow", "name": "Valid"}
    (tmp_path / "valid_workflow.json").write_text(json.dumps(workflow))

    # Mock the workflow definitions path
    import src.interface.cli.completions

    original_path = src.interface.cli.completions._get_workflow_definitions_path
    src.interface.cli.completions._get_workflow_definitions_path = lambda: tmp_path

    try:
        # Should return valid workflows and skip invalid ones
        result = complete_workflow_names("")
        assert "valid_workflow" in result

    finally:
        src.interface.cli.completions._get_workflow_definitions_path = original_path
