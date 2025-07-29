"""Tests for application layer progress utilities.

Validates Clean Architecture compliance - no external dependencies.
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from src.application.utilities.progress import (
    NoOpProgressProvider,
    ProgressOperation,
    ProgressProvider,
    create_operation,
    get_progress_provider,
    set_progress_provider,
)
from src.application.utilities.progress_integration import (
    DatabaseProgressContext,
    batch_progress_wrapper,
    with_progress,
)


class TestProgressOperation:
    """Test ProgressOperation domain entity."""

    def test_create_operation(self):
        """Test creating progress operations."""
        operation = ProgressOperation(
            description="Test operation", total_items=100, current_items=25
        )

        assert operation.description == "Test operation"
        assert operation.total_items == 100
        assert operation.current_items == 25
        assert not operation.is_indeterminate
        assert operation.progress_percentage == 25.0
        assert not operation.is_complete

    def test_indeterminate_operation(self):
        """Test indeterminate (spinner-only) operations."""
        operation = ProgressOperation(description="Searching...", total_items=None)

        assert operation.is_indeterminate
        assert operation.progress_percentage == 0.0
        assert not operation.is_complete

    def test_complete_operation(self):
        """Test completed operations."""
        operation = ProgressOperation(
            description="Done", total_items=50, current_items=50
        )

        assert operation.is_complete
        assert operation.progress_percentage == 100.0

    def test_over_complete_operation(self):
        """Test operations that exceed total."""
        operation = ProgressOperation(
            description="Done", total_items=50, current_items=75
        )

        assert operation.is_complete
        assert operation.progress_percentage == 100.0  # Capped at 100%

    def test_zero_total_items(self):
        """Test edge case with zero total items."""
        operation = ProgressOperation(
            description="Empty", total_items=0, current_items=0
        )

        assert not operation.is_indeterminate
        assert operation.progress_percentage == 0.0
        assert operation.is_complete

    def test_factory_function(self):
        """Test create_operation factory function."""
        operation = create_operation(
            "Test factory", total_items=200, batch_id="test-123"
        )

        assert operation.description == "Test factory"
        assert operation.total_items == 200
        assert operation.metadata["batch_id"] == "test-123"
        assert operation.current_items == 0
        assert isinstance(operation.start_time, datetime)


class TestNoOpProgressProvider:
    """Test NoOpProgressProvider implementation."""

    def test_no_op_provider(self):
        """Test no-op provider does nothing but satisfies interface."""
        provider = NoOpProgressProvider()
        operation = create_operation("Test")

        # Should return operation ID
        operation_id = provider.start_operation(operation)
        assert operation_id == operation.operation_id

        # Should not raise errors
        provider.update_progress(operation_id, 10, 100, "Updated")
        provider.set_description(operation_id, "New description")
        provider.complete_operation(operation_id)

        # Should always return False for long running
        assert not provider.is_long_running_operation(operation)


class TestProgressProviderManagement:
    """Test global progress provider management."""

    def test_default_provider(self):
        """Test default no-op provider."""
        # Reset to default
        set_progress_provider(None)
        provider = get_progress_provider()
        assert isinstance(provider, NoOpProgressProvider)

    def test_custom_provider(self):
        """Test setting custom provider."""
        mock_provider = Mock(spec=ProgressProvider)
        set_progress_provider(mock_provider)

        provider = get_progress_provider()
        assert provider is mock_provider

        # Reset for other tests
        set_progress_provider(None)


class TestWithProgressDecorator:
    """Test with_progress decorator."""

    @pytest.mark.asyncio
    async def test_basic_async_decorator(self):
        """Test basic async function decoration."""
        mock_provider = Mock(spec=ProgressProvider)
        mock_provider.start_operation.return_value = "test-op-id"

        @with_progress("Testing async", progress_provider_factory=lambda: mock_provider)
        async def test_func():
            return "success"

        result = await test_func()

        assert result == "success"
        mock_provider.start_operation.assert_called_once()
        mock_provider.complete_operation.assert_called_once_with("test-op-id")

    @pytest.mark.asyncio
    async def test_with_total_estimation(self):
        """Test decorator with total estimation."""
        mock_provider = Mock(spec=ProgressProvider)
        mock_provider.start_operation.return_value = "test-op-id"

        @with_progress(
            "Processing items",
            estimate_total=lambda items: len(items),
            progress_provider_factory=lambda: mock_provider,
        )
        async def process_items(items):
            return f"processed {len(items)} items"

        result = await process_items([1, 2, 3, 4, 5])

        assert result == "processed 5 items"
        # Verify operation was created with estimated total
        call_args = mock_provider.start_operation.call_args[0][0]
        assert call_args.total_items == 5

    @pytest.mark.asyncio
    async def test_with_console_output(self):
        """Test decorator with console output."""
        mock_console = Mock()
        mock_provider = Mock(spec=ProgressProvider)
        mock_provider.start_operation.return_value = "test-op-id"

        @with_progress(
            "Testing console",
            success_text="All done!",
            console=mock_console,
            progress_provider_factory=lambda: mock_provider,
        )
        async def test_func():
            return "result"

        result = await test_func()

        assert result == "result"
        mock_console.print.assert_called_once_with("[green]✓ All done![/green]")

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        """Test decorator cleans up on exceptions."""
        mock_provider = Mock(spec=ProgressProvider)
        mock_provider.start_operation.return_value = "test-op-id"

        @with_progress(
            "Testing exception", progress_provider_factory=lambda: mock_provider
        )
        async def failing_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            await failing_func()

        # Should still call complete_operation on failure
        mock_provider.complete_operation.assert_called_once_with("test-op-id")


class TestDatabaseProgressContext:
    """Test DatabaseProgressContext async context manager."""

    @pytest.mark.asyncio
    async def test_database_progress_context(self):
        """Test database progress context manager with dependency injection."""
        mock_session = Mock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_repositories = Mock()
        mock_result = Mock()

        mock_console = Mock()
        mock_ui = Mock()

        session_factory = Mock(return_value=mock_session)
        repository_factory = Mock(return_value=mock_repositories)

        async def test_operation(repositories):
            assert repositories is mock_repositories
            return mock_result

        async with DatabaseProgressContext(
            description="Testing DB operation",
            success_text="DB operation complete!",
            console=mock_console,
            ui_provider=mock_ui,
        ) as progress:
            result = await progress.run_with_repositories(
                test_operation, session_factory, repository_factory
            )

        assert result is mock_result
        # Verify console output
        mock_console.print.assert_called_once_with(
            "[green]✓ DB operation complete![/green]"
        )

        # Verify UI display was called
        mock_ui.display_operation_result.assert_called_once()


class TestBatchProgressWrapper:
    """Test batch_progress_wrapper utility."""

    @pytest.mark.asyncio
    async def test_batch_processing_with_progress(self):
        """Test batch processing with progress tracking."""
        items = list(range(10))  # 0-9
        mock_provider = Mock(spec=ProgressProvider)
        mock_provider.start_operation.return_value = "batch-op-id"

        async def mock_process_func(batch):
            return {i: f"processed-{i}" for i in batch}

        wrapper = batch_progress_wrapper(
            items,
            mock_process_func,
            operation_description="Processing numbers",
            batch_size=3,
            progress_provider=mock_provider,
        )

        result = await wrapper()

        # Should process all items
        assert len(result) == 10
        assert result[0] == "processed-0"
        assert result[9] == "processed-9"

        # Should track progress
        mock_provider.start_operation.assert_called_once()
        assert mock_provider.update_progress.call_count > 0
        mock_provider.complete_operation.assert_called_once_with("batch-op-id")

    @pytest.mark.asyncio
    async def test_empty_batch_processing(self):
        """Test batch processing with empty items list."""
        mock_provider = Mock(spec=ProgressProvider)

        async def mock_process_func(batch):
            return {}

        wrapper = batch_progress_wrapper(
            [], mock_process_func, progress_provider=mock_provider
        )

        result = await wrapper()

        assert result == {}
        # Should not start operation for empty list
        mock_provider.start_operation.assert_not_called()


class TestCleanArchitectureCompliance:
    """Test Clean Architecture compliance - no external dependencies."""

    def test_no_external_imports(self):
        """Verify progress modules have no external dependencies."""
        import sys

        sys.path.insert(0, "src")

        # This should work without any narada.* imports
        from application.utilities.progress import (
            NoOpProgressProvider,
            create_operation,
        )

        # Verify we can create instances without external dependencies
        operation = create_operation("Test", 100)
        assert operation.description == "Test"

        provider = NoOpProgressProvider()
        assert provider.start_operation(operation) == operation.operation_id

    def test_protocol_interfaces(self):
        """Test that Protocol interfaces enforce contracts."""
        from application.utilities.progress import ProgressProvider
        from application.utilities.progress_integration import Console, OperationResult

        # Verify protocols define expected methods
        assert hasattr(ProgressProvider, "start_operation")
        assert hasattr(ProgressProvider, "update_progress")
        assert hasattr(ProgressProvider, "complete_operation")
        assert hasattr(Console, "print")
        assert hasattr(OperationResult, "to_dict")

        # Should be able to create mock implementations
        class MockConsole:
            def print(self, text: str):
                pass

        class MockResult:
            def to_dict(self):
                return {}

        # These should satisfy the protocol contracts
        console: Console = MockConsole()
        result: OperationResult = MockResult()

        console.print("test")
        assert result.to_dict() == {}
