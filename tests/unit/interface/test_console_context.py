"""Unit tests for the progress-display console context.

``SimpleConsoleContext`` (no broker) is covered through the real factory in
``tests/integration/test_progress_coordination.py``.
"""

from unittest.mock import Mock

from rich.console import Console

from src.interface.cli.console import ProgressDisplayContext


class TestProgressDisplayContext:
    def test_uses_the_provider_console_and_exposes_the_broker(self):
        """Output goes through the subscriber's console so logs layer above bars."""
        provider_console = Console()
        provider = Mock()
        provider.get_console.return_value = provider_console
        broker = Mock()

        context = ProgressDisplayContext(provider, broker)

        assert context.console is provider_console
        assert context.get_progress_broker() is broker
