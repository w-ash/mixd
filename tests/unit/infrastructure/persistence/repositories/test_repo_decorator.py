"""Unit tests for the ``db_operation`` repository decorator.

The decorator must:
- reject non-async functions at decoration time, naming the operation
- pass arguments through to the wrapped coroutine and return its result
- keep working on methods, where ``self`` is the first argument
"""

import pytest

from src.infrastructure.persistence.repositories.repo_decorator import db_operation


class TestDbOperationDecorator:
    """Test the db_operation decorator behavior."""

    def test_decorator_rejects_sync_function_naming_the_operation(self):
        """A sync function raises TypeError whose message names the operation."""
        with pytest.raises(
            TypeError,
            match=(
                r"^db_operation can only be used with async functions, "
                r"but custom_op_name is not async$"
            ),
        ):

            @db_operation("custom_op_name")
            def another_sync_function():
                return "fail"

    async def test_decorated_function_passes_arguments(self):
        """Positional and keyword arguments reach the function; its result returns."""

        @db_operation("test_args")
        async def async_function_with_args(a: int, b: str, c: bool = False):
            return f"{a}-{b}-{c}"

        result = await async_function_with_args(42, "test", c=True)
        assert result == "42-test-True"

    async def test_decorated_method_in_class(self):
        """Decorator should work on class methods (repository pattern)."""

        class MockRepository:
            @db_operation("get_item")
            async def get_by_id(self, item_id: int):
                return f"item_{item_id}"

        repo = MockRepository()
        result = await repo.get_by_id(123)
        assert result == "item_123"
