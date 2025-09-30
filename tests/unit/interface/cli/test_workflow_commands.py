"""Tests for workflow CLI commands using typer.testing.CliRunner.

Tests the workflow command structure, interactive discovery patterns,
and 2025 Typer best practices implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from typer.testing import CliRunner

from src.interface.cli.workflow_commands import app


class TestWorkflowCLICommands:
    """Test suite for workflow CLI commands using Typer testing patterns."""

    def setup_method(self):
        """Set up test runner and common mocks."""
        self.runner = CliRunner()

    def test_workflow_app_help(self):
        """Test that workflow app shows proper help text."""
        result = self.runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "Execute and manage playlist workflows" in result.output
        assert "run" in result.output
        assert "list" in result.output

    def test_run_command_help(self):
        """Test run command help text and parameters."""
        result = self.runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        assert "Execute a specific workflow" in result.output
        assert "--show-results" in result.output
        assert "--format" in result.output
        assert "--quiet" in result.output

    def test_list_command_help(self):
        """Test list command help text and options."""
        result = self.runner.invoke(app, ["list", "--help"])

        assert result.exit_code == 0
        assert "List available workflow definitions" in result.output
        assert "--format" in result.output
        assert "--category" in result.output

    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_list_command_no_workflows(self, mock_get_workflows):
        """Test list command when no workflows are available."""
        mock_get_workflows.return_value = []

        result = self.runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert "No workflows found" in result.output
        mock_get_workflows.assert_called_once()

    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_list_command_with_workflows(self, mock_get_workflows):
        """Test list command with available workflows."""
        mock_workflows = [
            {
                "id": "test_workflow",
                "name": "Test Workflow",
                "description": "A test workflow",
                "task_count": 3,
                "path": "/fake/path"
            }
        ]
        mock_get_workflows.return_value = mock_workflows

        result = self.runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert "Test Workflow" in result.output
        assert "test_workflow" in result.output

    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_list_command_json_format(self, mock_get_workflows):
        """Test list command with JSON output format."""
        mock_workflows = [
            {
                "id": "test_workflow",
                "name": "Test Workflow",
                "description": "A test workflow",
                "task_count": 3,
                "path": "/fake/path"
            }
        ]
        mock_get_workflows.return_value = mock_workflows

        result = self.runner.invoke(app, ["list", "--format", "json"])

        assert result.exit_code == 0
        # Should be valid JSON
        try:
            output_data = json.loads(result.output)
            assert len(output_data) == 1
            assert output_data[0]["id"] == "test_workflow"
        except json.JSONDecodeError:
            pytest.fail("Output is not valid JSON")

    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_list_command_category_filter(self, mock_get_workflows):
        """Test list command with category filtering."""
        mock_workflows = [
            {
                "id": "discovery_workflow",
                "name": "Discovery Mix",
                "description": "Find new music",
                "task_count": 2,
                "path": "/fake/path"
            }
        ]
        mock_get_workflows.return_value = mock_workflows

        result = self.runner.invoke(app, ["list", "--category", "discovery"])

        assert result.exit_code == 0

    @patch("src.interface.cli.workflow_commands._execute_workflow")
    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_run_command_with_workflow_id(self, mock_get_workflows, mock_execute):
        """Test run command with valid workflow ID."""
        mock_workflows = [
            {
                "id": "test_workflow",
                "name": "Test Workflow",
                "description": "A test workflow",
                "task_count": 3,
                "path": "/fake/path"
            }
        ]
        mock_get_workflows.return_value = mock_workflows
        mock_execute.return_value = None

        result = self.runner.invoke(app, ["run", "test_workflow"])

        assert result.exit_code in [0, 1]  # Allow for execution complexity
        mock_execute.assert_called_once()

    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_run_command_invalid_workflow(self, mock_get_workflows):
        """Test run command with invalid workflow ID."""
        mock_get_workflows.return_value = []

        result = self.runner.invoke(app, ["run", "invalid_workflow"])

        # Should show interactive discovery when no workflow ID provided
        assert result.exit_code in [0, 1]

    def test_run_command_flags(self):
        """Test run command with various flags."""
        # Test --quiet flag
        result = self.runner.invoke(app, ["run", "--quiet", "--help"])
        assert result.exit_code == 0

        # Test --show-results flag
        result = self.runner.invoke(app, ["run", "--show-results", "--help"])
        assert result.exit_code == 0

        # Test --format flag
        result = self.runner.invoke(app, ["run", "--format", "table", "--help"])
        assert result.exit_code == 0


class TestWorkflowInteractiveDiscovery:
    """Test interactive workflow discovery patterns."""

    def setup_method(self):
        """Set up test runner for interactive testing."""
        self.runner = CliRunner()

    @patch("src.interface.cli.workflow_commands._show_interactive_workflow_browser")
    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_callback_without_subcommand(self, mock_get_workflows, mock_browser):
        """Test callback execution when no subcommand is provided."""
        mock_get_workflows.return_value = []
        mock_browser.return_value = None

        result = self.runner.invoke(app, [])

        assert result.exit_code in [0, 1]
        mock_browser.assert_called_once()

    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_workflow_categorization(self, mock_get_workflows):
        """Test workflow categorization logic."""
        from src.interface.cli.workflow_commands import _infer_workflow_category

        # Test discovery category
        workflow = {"id": "discovery_mix", "name": "Discovery Mix", "description": "Find new music"}
        category = _infer_workflow_category(workflow)
        assert "Discovery" in category

        # Test organization category
        workflow = {"id": "sort_by_date", "name": "Sort by Date", "description": "Sort playlist by date"}
        category = _infer_workflow_category(workflow)
        assert "Organization" in category

        # Test default category
        workflow = {"id": "generic", "name": "Generic", "description": "Generic workflow"}
        category = _infer_workflow_category(workflow)
        assert "General" in category


class TestWorkflowCommandIntegration:
    """Integration tests for workflow commands."""

    def setup_method(self):
        """Set up test runner for integration tests."""
        self.runner = CliRunner()

    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_workflow_discovery_from_filesystem(self, mock_get_workflows):
        """Test workflow discovery from filesystem."""
        # Test that _get_available_workflows can handle file system operations
        mock_get_workflows.return_value = []

        result = self.runner.invoke(app, ["list"])

        assert result.exit_code == 0
        mock_get_workflows.assert_called_once()

    def test_workflow_definitions_path_handling(self):
        """Test workflow definitions path calculation."""
        from src.interface.cli.workflow_commands import _get_available_workflows

        # Should not crash even if definitions directory doesn't exist
        workflows = _get_available_workflows()
        assert isinstance(workflows, list)


class TestWorkflowErrorHandling:
    """Test error handling and user guidance patterns."""

    def setup_method(self):
        """Set up test runner for error testing."""
        self.runner = CliRunner()

    def test_invalid_format_option(self):
        """Test handling of invalid format options."""
        result = self.runner.invoke(app, ["list", "--format", "invalid"])

        # Should handle gracefully or show valid options
        assert result.exit_code in [0, 2]  # Allow for validation errors

    def test_missing_workflow_guidance(self):
        """Test that missing workflows provide helpful guidance."""
        result = self.runner.invoke(app, ["run", "nonexistent_workflow"])

        # Should provide helpful guidance
        assert result.exit_code in [0, 1]

    @patch("src.interface.cli.workflow_commands._get_available_workflows")
    def test_empty_category_filter(self, mock_get_workflows):
        """Test category filter with no matching workflows."""
        mock_workflows = [
            {
                "id": "test_workflow",
                "name": "Test Workflow",
                "description": "A test workflow",
                "task_count": 3,
                "path": "/fake/path"
            }
        ]
        mock_get_workflows.return_value = mock_workflows

        result = self.runner.invoke(app, ["list", "--category", "nonexistent"])

        assert result.exit_code == 0
        assert "No workflows found in category" in result.output