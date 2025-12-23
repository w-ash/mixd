"""Unit tests for repository decorator functionality.

Tests the db_operation decorator to ensure it:
- Correctly identifies async functions
- Rejects non-async functions with clear error messages
- Maintains function behavior after decoration

Also tests base_repo utilities that use iscoroutinefunction.
"""

import asyncio
import inspect

import pytest

from src.infrastructure.persistence.repositories.repo_decorator import db_operation


class TestDbOperationDecorator:
    """Test the db_operation decorator behavior."""

    def test_decorator_accepts_async_function(self):
        """Decorator should accept async functions without error."""
        # This should NOT raise TypeError
        @db_operation("test_operation")
        async def valid_async_function():
            return "success"

        # If we get here without TypeError, the test passes
        assert valid_async_function is not None

    def test_decorator_rejects_sync_function(self):
        """Decorator should raise TypeError for non-async functions."""
        with pytest.raises(
            TypeError,
            match=r"db_operation can only be used with async functions.*",
        ):

            @db_operation("test_operation")
            def invalid_sync_function():
                return "this should fail"

    def test_decorator_rejects_sync_function_with_custom_name(self):
        """Decorator should raise TypeError with operation name in message."""
        with pytest.raises(
            TypeError,
            match=r".*custom_op_name is not async",
        ):

            @db_operation("custom_op_name")
            def another_sync_function():
                return "fail"

    @pytest.mark.asyncio
    async def test_decorated_function_executes_correctly(self):
        """Decorated async function should execute normally."""

        @db_operation("test_execution")
        async def async_function_with_return():
            return "test_result"

        result = await async_function_with_return()
        assert result == "test_result"

    @pytest.mark.asyncio
    async def test_decorated_function_passes_arguments(self):
        """Decorated function should correctly pass through arguments."""

        @db_operation("test_args")
        async def async_function_with_args(a: int, b: str, c: bool = False):
            return f"{a}-{b}-{c}"

        result = await async_function_with_args(42, "test", c=True)
        assert result == "42-test-True"

    @pytest.mark.asyncio
    async def test_decorated_method_in_class(self):
        """Decorator should work on class methods (repository pattern)."""

        class MockRepository:
            @db_operation("get_item")
            async def get_by_id(self, item_id: int):
                return f"item_{item_id}"

        repo = MockRepository()
        result = await repo.get_by_id(123)
        assert result == "item_123"


class TestInspectIscoroutinefunction:
    """Test that inspect.iscoroutinefunction works correctly.

    These tests verify that using inspect.iscoroutinefunction instead of
    asyncio.iscoroutinefunction provides identical behavior for our use cases.
    """

    def test_inspect_identifies_async_function(self):
        """inspect.iscoroutinefunction should identify async functions."""

        async def async_func():
            return "async"

        # Both should return True
        assert inspect.iscoroutinefunction(async_func)
        assert asyncio.iscoroutinefunction(async_func)

    def test_inspect_rejects_sync_function(self):
        """inspect.iscoroutinefunction should reject sync functions."""

        def sync_func():
            return "sync"

        # Both should return False
        assert not inspect.iscoroutinefunction(sync_func)
        assert not asyncio.iscoroutinefunction(sync_func)

    def test_inspect_identifies_async_method(self):
        """inspect.iscoroutinefunction should identify async methods."""

        class TestClass:
            async def async_method(self):
                return "async"

        # Both should return True
        assert inspect.iscoroutinefunction(TestClass.async_method)
        assert asyncio.iscoroutinefunction(TestClass.async_method)

    def test_inspect_vs_iscoroutine_for_results(self):
        """Demonstrate difference between iscoroutinefunction and iscoroutine."""

        async def async_func():
            return "result"

        # Function itself
        assert inspect.iscoroutinefunction(async_func)
        assert not asyncio.iscoroutine(async_func)  # Not a coroutine, it's a function

        # Called result
        coro = async_func()
        assert not inspect.iscoroutinefunction(coro)  # Result is not a function
        assert asyncio.iscoroutine(coro)  # Result IS a coroutine

        # Clean up
        coro.close()
