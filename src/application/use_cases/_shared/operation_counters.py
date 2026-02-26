"""Utilities for counting playlist operations.

Provides helper functions for analyzing playlist diff operations and
calculating operation statistics (adds, removes, moves).
"""

from src.application.use_cases._shared.playlist_results import OperationCounts
from src.domain.playlist import PlaylistOperation, PlaylistOperationType


def count_operation_types(operations: list[PlaylistOperation]) -> OperationCounts:
    """Count add/remove/move operations from diff operations list.

    Analyzes a list of playlist operations and returns typed counts
    of each operation type for reporting and validation.

    Args:
        operations: List of PlaylistOperation objects with operation_type field.

    Returns:
        OperationCounts with added, removed, and moved counts.

    Example:
        >>> ops = [
        ...     PlaylistOperation(operation_type=PlaylistOperationType.ADD),
        ...     PlaylistOperation(operation_type=PlaylistOperationType.ADD),
        ...     PlaylistOperation(operation_type=PlaylistOperationType.REMOVE),
        ... ]
        >>> counts = count_operation_types(ops)
        >>> counts.added
        2
        >>> counts.removed
        1
    """
    added = sum(
        1 for op in operations if op.operation_type == PlaylistOperationType.ADD
    )
    removed = sum(
        1 for op in operations if op.operation_type == PlaylistOperationType.REMOVE
    )
    moved = sum(
        1 for op in operations if op.operation_type == PlaylistOperationType.MOVE
    )
    return OperationCounts(added=added, removed=removed, moved=moved)
