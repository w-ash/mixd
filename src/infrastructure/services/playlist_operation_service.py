"""Playlist operation optimization service for efficient API batching.

Provides intelligent grouping and sequencing of playlist operations to minimize
API calls while preserving user context. Extracted from connector implementations
to maintain clean separation between API clients and business logic.
"""

from typing import Any

from attrs import define

from src.config import get_logger, settings
from src.domain.playlist import PlaylistOperation, PlaylistOperationType

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class OptimizedOperationBatch:
    """Represents a batch of operations optimized for efficient API execution."""

    batch_type: str  # "add", "remove", "move"
    operations: list[PlaylistOperation]
    api_params: dict[str, Any]  # Parameters ready for API call


@define(slots=True)
class PlaylistOperationService:
    """Service for optimizing playlist operations for efficient API execution.

    Provides intelligent batching, grouping, and sequencing strategies to minimize
    API calls while preserving user context and maintaining operation correctness.
    """

    def optimize_operations(
        self, operations: list[PlaylistOperation], connector: str = "spotify"
    ) -> list[OptimizedOperationBatch]:
        """Optimize a list of operations for efficient API execution.

        Args:
            operations: Raw playlist operations to optimize
            connector: Target connector name for service-specific optimization

        Returns:
            List of optimized operation batches ready for API execution
        """
        if not operations:
            return []

        # Group operations by type
        operations_by_type = self._group_operations_by_type(operations)

        # Apply service-specific optimization strategies
        optimized_batches = []

        # Optimal sequence: REMOVE → MOVE → ADD (for stable positioning)
        for op_type in [
            PlaylistOperationType.REMOVE,
            PlaylistOperationType.MOVE,
            PlaylistOperationType.ADD,
        ]:
            ops = operations_by_type.get(op_type, [])
            if ops:
                batches = self._optimize_operations_by_type(ops, op_type, connector)
                optimized_batches.extend(batches)

        logger.debug(
            f"Optimized {len(operations)} operations into {len(optimized_batches)} batches",
            original_operations=len(operations),
            optimized_batches=len(optimized_batches),
            connector=connector,
        )

        return optimized_batches

    def _group_operations_by_type(
        self, operations: list[PlaylistOperation]
    ) -> dict[PlaylistOperationType, list[PlaylistOperation]]:
        """Group operations by their type for separate optimization."""
        groups = {}
        for op in operations:
            if op.operation_type not in groups:
                groups[op.operation_type] = []
            groups[op.operation_type].append(op)
        return groups

    def _optimize_operations_by_type(
        self,
        operations: list[PlaylistOperation],
        op_type: PlaylistOperationType,
        connector: str,
    ) -> list[OptimizedOperationBatch]:
        """Apply type-specific optimization strategies."""
        _ = connector  # Keep for future connector-specific optimizations
        if op_type == PlaylistOperationType.MOVE:
            return self._optimize_move_operations(operations)
        elif op_type == PlaylistOperationType.ADD:
            return self._optimize_add_operations(operations)
        elif op_type == PlaylistOperationType.REMOVE:
            return self._optimize_remove_operations(operations)
        else:
            # Fallback: individual operations
            return [
                OptimizedOperationBatch(
                    batch_type="individual",
                    operations=[op],
                    api_params={"operation": op},
                )
                for op in operations
            ]

    def _optimize_move_operations(
        self, move_ops: list[PlaylistOperation]
    ) -> list[OptimizedOperationBatch]:
        """Group consecutive move operations into block moves."""
        if not move_ops:
            return []

        # Sort moves by old_position to find consecutive ranges
        # Filter out None values first, then sort by guaranteed int values
        moves_with_position = [op for op in move_ops if op.old_position is not None]
        sorted_moves = sorted(
            moves_with_position,
            key=lambda op: op.old_position
            or 0,  # Guaranteed non-None, but satisfy type checker
        )

        if not sorted_moves:
            return []

        batches = []
        current_block_ops = [sorted_moves[0]]

        for op in sorted_moves[1:]:
            # Check if this operation extends the current consecutive block
            last_op = current_block_ops[-1]
            # These are guaranteed to be non-None since we filtered them
            expected_old_pos = (last_op.old_position or 0) + len(current_block_ops)
            expected_new_pos = (last_op.position or 0) + len(current_block_ops)

            if (
                op.old_position == expected_old_pos
                and op.position == expected_new_pos
                and len(current_block_ops) < settings.api.spotify_large_batch_size
            ):  # API limit
                # Extend current block
                current_block_ops.append(op)
            else:
                # Complete current block and start new one
                batches.append(self._create_move_batch(current_block_ops))
                current_block_ops = [op]

        # Add the final block
        batches.append(self._create_move_batch(current_block_ops))

        return batches

    def _create_move_batch(
        self, operations: list[PlaylistOperation]
    ) -> OptimizedOperationBatch:
        """Create a move batch with API parameters."""
        first_op = operations[0]
        return OptimizedOperationBatch(
            batch_type="move",
            operations=operations,
            api_params={
                "range_start": first_op.old_position,
                "insert_before": first_op.position,
                "range_length": len(operations),
            },
        )

    def _optimize_add_operations(
        self, add_ops: list[PlaylistOperation]
    ) -> list[OptimizedOperationBatch]:
        """Group consecutive add operations into bulk adds."""
        if not add_ops:
            return []

        # Sort adds by position and filter valid operations
        sorted_adds = sorted(
            [op for op in add_ops if op.spotify_uri and op.position is not None],
            key=lambda op: op.position,
        )

        if not sorted_adds:
            return []

        batches = []
        current_batch_ops = [sorted_adds[0]]

        for op in sorted_adds[1:]:
            # Check if this add extends the current consecutive batch
            expected_position = sorted_adds[0].position + len(current_batch_ops)

            if (
                op.position == expected_position
                and len(current_batch_ops) < settings.api.spotify_large_batch_size
            ):  # API limit
                # Extend current batch
                current_batch_ops.append(op)
            else:
                # Complete current batch and start new one
                batches.append(self._create_add_batch(current_batch_ops))
                current_batch_ops = [op]

        # Add the final batch
        batches.append(self._create_add_batch(current_batch_ops))

        return batches

    def _create_add_batch(
        self, operations: list[PlaylistOperation]
    ) -> OptimizedOperationBatch:
        """Create an add batch with API parameters."""
        return OptimizedOperationBatch(
            batch_type="add",
            operations=operations,
            api_params={
                "position": operations[0].position,
                "uris": [op.spotify_uri for op in operations],
            },
        )

    def _optimize_remove_operations(
        self, remove_ops: list[PlaylistOperation]
    ) -> list[OptimizedOperationBatch]:
        """Group remove operations by track URI for efficient batching."""
        if not remove_ops:
            return []

        # Group removes by track URI
        tracks_to_remove = {}
        for op in remove_ops:
            if op.spotify_uri:
                if op.spotify_uri not in tracks_to_remove:
                    tracks_to_remove[op.spotify_uri] = []
                if op.old_position is not None:
                    tracks_to_remove[op.spotify_uri].append(op.old_position)

        # Create batches of up to 100 items each
        items_to_remove = []
        for uri, positions in tracks_to_remove.items():
            if positions:
                items_to_remove.append({"uri": uri, "positions": positions})
            else:
                items_to_remove.append({"uri": uri})

        # Split into batches of 100
        batches = []
        for i in range(0, len(items_to_remove), 100):
            batch_items = items_to_remove[i : i + 100]
            batches.append(
                OptimizedOperationBatch(
                    batch_type="remove",
                    operations=remove_ops[i : i + 100],  # Approximate mapping
                    api_params={"items": batch_items},
                )
            )

        return batches
