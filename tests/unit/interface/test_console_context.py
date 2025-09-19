"""Unit tests for console context classes.

Tests the explicit SimpleConsoleContext and ProgressDisplayContext classes
that replaced anonymous nested classes for better code organization.
"""

from unittest.mock import Mock

from rich.console import Console

from src.interface.cli.console import ProgressDisplayContext, SimpleConsoleContext


class TestSimpleConsoleContext:
    """Test SimpleConsoleContext for commands without progress tracking."""

    def test_initialization(self):
        """Test that SimpleConsoleContext initializes correctly."""
        console = Console()
        context = SimpleConsoleContext(console)

        assert context.console is console
        assert context.live_console is console  # Backward compatibility
        assert context.get_progress_manager() is None

    def test_console_access(self):
        """Test that console can be used for output."""
        console = Console()
        context = SimpleConsoleContext(console)

        # Should be able to access console methods
        assert hasattr(context.console, 'print')
        assert hasattr(context.console, 'status')

    def test_no_progress_manager(self):
        """Test that progress manager is None when not needed."""
        console = Console()
        context = SimpleConsoleContext(console)

        progress_manager = context.get_progress_manager()
        assert progress_manager is None


class TestProgressDisplayContext:
    """Test ProgressDisplayContext for commands with progress tracking."""

    def test_initialization(self):
        """Test that ProgressDisplayContext initializes correctly."""
        mock_provider = Mock()
        mock_console = Console()
        mock_provider.get_console.return_value = mock_console
        mock_manager = Mock()

        context = ProgressDisplayContext(mock_provider, mock_manager)

        assert context.provider is mock_provider
        assert context.console is mock_console
        assert context.live_console is mock_console  # Backward compatibility
        assert context.progress_manager is mock_manager

    def test_console_from_provider(self):
        """Test that console is obtained from provider."""
        mock_provider = Mock()
        mock_console = Console()
        mock_provider.get_console.return_value = mock_console
        mock_manager = Mock()

        context = ProgressDisplayContext(mock_provider, mock_manager)

        # Verify provider.get_console() was called
        mock_provider.get_console.assert_called_once()
        assert context.console is mock_console

    def test_progress_manager_access(self):
        """Test that progress manager is available."""
        mock_provider = Mock()
        mock_provider.get_console.return_value = Console()
        mock_manager = Mock()

        context = ProgressDisplayContext(mock_provider, mock_manager)

        progress_manager = context.get_progress_manager()
        assert progress_manager is mock_manager

    def test_backward_compatibility(self):
        """Test that backward compatibility properties work."""
        mock_provider = Mock()
        mock_console = Console()
        mock_provider.get_console.return_value = mock_console
        mock_manager = Mock()

        context = ProgressDisplayContext(mock_provider, mock_manager)

        # Verify backward compatibility properties
        assert context.live_console is context.console
        assert context.live_console is mock_console


class TestContextClassComparison:
    """Test the differences between context classes."""

    def test_simple_vs_progress_context_differences(self):
        """Test key differences between simple and progress contexts."""
        console = Console()
        simple_context = SimpleConsoleContext(console)

        mock_provider = Mock()
        mock_provider.get_console.return_value = console
        mock_manager = Mock()
        progress_context = ProgressDisplayContext(mock_provider, mock_manager)

        # Both should have console access
        assert hasattr(simple_context, 'console')
        assert hasattr(progress_context, 'console')

        # Both should have get_progress_manager method
        assert hasattr(simple_context, 'get_progress_manager')
        assert hasattr(progress_context, 'get_progress_manager')

        # But only progress context should have actual progress manager
        assert simple_context.get_progress_manager() is None
        assert progress_context.get_progress_manager() is not None

        # Progress context should have provider reference
        assert hasattr(progress_context, 'provider')
        assert not hasattr(simple_context, 'provider')

    def test_both_contexts_support_console_operations(self):
        """Test that both contexts support basic console operations."""
        console = Console()

        simple_context = SimpleConsoleContext(console)

        mock_provider = Mock()
        mock_provider.get_console.return_value = console
        mock_manager = Mock()
        progress_context = ProgressDisplayContext(mock_provider, mock_manager)

        # Both should support console operations
        contexts = [simple_context, progress_context]

        for context in contexts:
            assert hasattr(context.console, 'print')
            assert hasattr(context.console, 'status')
            assert hasattr(context.console, 'rule')
            # Test backward compatibility
            assert context.live_console is context.console