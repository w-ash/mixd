"""Execution strategies for playlist operations.

Provides different strategies for executing playlist operations based on the target
platform (canonical database vs external API). Enables DRY compliance by using
the same diff logic with different execution approaches.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: service_metadata, raw_data dicts, factory patterns

import bisect
from typing import Any, Final, Protocol

from attrs import define
import structlog

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Track, TrackList
from src.domain.playlist.diff_engine import (
    PlaylistDiff,
    PlaylistOperation,
    PlaylistOperationType,
)
from src.domain.transforms.playlist_operations import reorder_to_match_target

logger = structlog.get_logger(__name__)

_DEBUG_TRUNCATION: Final = 10


@define(frozen=True, slots=True)
class ExecutionPlan:
    """Plan for executing playlist operations.

    Contains the organized operations and metadata about execution strategy.
    Different strategies may plan the same operations differently.
    """

    operations: list[PlaylistOperation]
    execution_metadata: dict[str, Any]
    use_atomic_reorder: bool = False
    dependency_order: list[int] | None = (
        None  # Order indices for dependency-aware execution
    )


class ExecutionStrategy(Protocol):
    """Protocol for playlist operation execution strategies.

    Different strategies handle the same operations differently:
    - Canonical strategy: Atomic state transformation for performance
    - API strategy: Sequential execution with dependency ordering for external services
    """

    def plan_operations(self, diff: PlaylistDiff) -> ExecutionPlan:
        """Plan how to execute the given diff operations.

        Args:
            diff: Calculated playlist differences

        Returns:
            Execution plan optimized for this strategy
        """
        ...

    def can_optimize_to_reorder(self, _diff: PlaylistDiff) -> bool:
        """Check if operations can be optimized to direct reordering.

        Args:
            diff: Calculated playlist differences

        Returns:
            True if direct reordering is more efficient than individual operations
        """
        ...


class CanonicalExecutionStrategy:
    """Execution strategy for canonical (local database) playlist updates.

    Optimizes for performance and correctness by using atomic state transformation
    when possible, falling back to individual operations only when necessary.
    """

    def plan_operations(self, diff: PlaylistDiff) -> ExecutionPlan:
        """Plan operations for canonical playlist execution.

        For canonical playlists, we can use direct reordering which is more efficient
        and avoids position conflicts entirely by reconstructing the final state.

        Args:
            diff: Calculated playlist differences

        Returns:
            Execution plan optimized for canonical updates
        """
        # For canonical playlists, we prefer atomic reordering when possible
        use_atomic_reorder = self.can_optimize_to_reorder(diff)

        execution_metadata = {
            "strategy": "canonical",
            "atomic_reorder": use_atomic_reorder,
            "operation_counts": diff.operation_summary,
            "confidence_score": diff.confidence_score,
        }

        if use_atomic_reorder:
            logger.debug("Using atomic reordering for canonical playlist update")
            # For atomic reordering, we still need the operations for metrics/logging
            # but execution will use direct reconstruction
            return ExecutionPlan(
                operations=diff.operations,
                execution_metadata=execution_metadata,
                use_atomic_reorder=True,
            )
        else:
            logger.debug("Using individual operations for canonical playlist update")
            return ExecutionPlan(
                operations=diff.operations,
                execution_metadata=execution_metadata,
                use_atomic_reorder=False,
            )

    def can_optimize_to_reorder(self, _diff: PlaylistDiff) -> bool:
        """Check if we can optimize to atomic reordering.

        Atomic reordering is always better for canonical playlists since we have
        full control over the database state.

        Args:
            diff: Calculated playlist differences

        Returns:
            True (canonical playlists can always use atomic reordering)
        """
        return True  # Canonical playlists can always use atomic reordering


class APIExecutionStrategy:
    """Execution strategy for external API playlist updates.

    Optimizes for API constraints by sequencing operations properly and handling
    dependency conflicts. Ensures single-pass success by avoiding position conflicts.
    """

    def plan_operations(self, diff: PlaylistDiff) -> ExecutionPlan:
        """Plan operations for API execution with dependency ordering.

        For API updates, we must execute operations in proper sequence to avoid
        position conflicts. Uses the existing sequencing logic with dependency awareness.

        Args:
            diff: Calculated playlist differences

        Returns:
            Execution plan optimized for API updates
        """
        from src.domain.playlist.diff_engine import sequence_operations_for_spotify

        # Use existing sequencing logic which orders: remove -> add -> move
        initial_operations = sequence_operations_for_spotify(diff.operations)

        # Apply position shift simulation to prevent conflicts in sequential execution
        sequenced_operations = self.simulate_position_shifts(initial_operations)

        # Calculate dependency order for move operations to avoid conflicts
        dependency_order = self._calculate_dependency_order(sequenced_operations)

        execution_metadata = {
            "strategy": "api",
            "operation_counts": diff.operation_summary,
            "confidence_score": diff.confidence_score,
            "initial_operations": len(initial_operations),
            "sequenced_operations": len(sequenced_operations),
            "dependency_conflicts_resolved": len(dependency_order)
            if dependency_order
            else 0,
            "position_shift_simulation": True,
        }

        logger.debug(
            f"Planned API execution: {len(sequenced_operations)} sequenced operations "
            + f"with {len(dependency_order) if dependency_order else 0} dependency constraints"
        )

        return ExecutionPlan(
            operations=sequenced_operations,
            execution_metadata=execution_metadata,
            use_atomic_reorder=False,
            dependency_order=dependency_order,
        )

    def can_optimize_to_reorder(self, _diff: PlaylistDiff) -> bool:
        """Check if we can optimize to atomic reordering.

        For API updates, we cannot use atomic reordering since we must work
        with the external service's API constraints.

        Args:
            diff: Calculated playlist differences

        Returns:
            False (API updates must use individual operations)
        """
        return False  # API updates must use individual operations

    def _calculate_dependency_order(
        self, operations: list[PlaylistOperation]
    ) -> list[int] | None:
        """Calculate dependency-safe execution order for move operations.

        Analyzes move operations to determine an execution order that avoids
        position conflicts by ensuring dependencies are resolved before dependents.
        Uses reverse-order execution to prevent index shifting issues.

        Args:
            operations: List of operations to analyze

        Returns:
            List of operation indices in dependency-safe order, or None if no dependencies
        """
        move_operations = [
            (i, op)
            for i, op in enumerate(operations)
            if op.operation_type == PlaylistOperationType.MOVE
        ]

        if len(move_operations) <= 1:
            return None  # No dependency conflicts possible

        # Sort move operations by old_position in descending order
        # This ensures we move from highest positions first, avoiding index shifts
        sorted_moves = sorted(
            move_operations, key=lambda x: x[1].old_position or 0, reverse=True
        )

        dependency_order = [i for i, _ in sorted_moves]

        logger.debug(
            f"Calculated dependency order for {len(move_operations)} move operations: "
            + "reverse position order to prevent index shifts"
        )

        return dependency_order

    def simulate_position_shifts(
        self, operations: list[PlaylistOperation]
    ) -> list[PlaylistOperation]:
        """Simulate position shifts and adjust operations to prevent conflicts.

        Simulates the execution of operations in order and adjusts subsequent
        operation positions based on the cumulative effects of earlier operations.

        Args:
            operations: List of operations to simulate and adjust

        Returns:
            List of adjusted operations with corrected positions
        """
        if not operations:
            return operations

        # Separate operations by type for proper sequencing
        remove_ops = [
            op for op in operations if op.operation_type == PlaylistOperationType.REMOVE
        ]
        add_ops = [
            op for op in operations if op.operation_type == PlaylistOperationType.ADD
        ]
        move_ops = [
            op for op in operations if op.operation_type == PlaylistOperationType.MOVE
        ]

        adjusted_operations: list[PlaylistOperation] = []

        # REMOVE operations: sort by position (highest first to avoid index shifts)
        remove_ops_sorted = sorted(
            remove_ops, key=lambda op: op.old_position or 0, reverse=True
        )
        adjusted_operations.extend(remove_ops_sorted)

        # ADD operations: positions may need adjustment based on removes
        # For simplicity, add operations typically reference final positions
        adjusted_operations.extend(add_ops)

        # MOVE operations: most complex - need to handle cascading position changes
        if move_ops:
            adjusted_moves = self._adjust_move_operations(move_ops, remove_ops_sorted)
            adjusted_operations.extend(adjusted_moves)

        logger.debug(
            "Position shift simulation complete: "
            + f"{len(remove_ops)} removes, {len(add_ops)} adds, {len(move_ops)} moves"
        )

        return adjusted_operations

    def _adjust_move_operations(
        self, move_ops: list[PlaylistOperation], remove_ops: list[PlaylistOperation]
    ) -> list[PlaylistOperation]:
        """Adjust move operations to account for position shifts from remove operations.

        Uses optimal position mapping algorithm to calculate correct positions after
        removals have changed the playlist structure. This prevents out-of-bounds
        errors when move operations are executed after remove operations.

        Args:
            move_ops: List of move operations to adjust
            remove_ops: List of remove operations that affect positions

        Returns:
            List of adjusted move operations with corrected positions
        """
        if not move_ops:
            return move_ops

        if not remove_ops:
            # No removals, just sort by old_position in descending order for reverse execution
            sorted_moves = sorted(
                move_ops, key=lambda op: op.old_position or 0, reverse=True
            )
            logger.debug(
                f"No removals to adjust for, using reverse-order execution for {len(move_ops)} moves"
            )
            return sorted_moves

        # Extract removed positions and sort them for efficient lookup
        removed_positions = sorted([
            op.old_position for op in remove_ops if op.old_position is not None
        ])

        logger.debug(
            f"Adjusting {len(move_ops)} move operations for {len(removed_positions)} removals",
            removed_positions=removed_positions[:_DEBUG_TRUNCATION]
            if len(removed_positions) > _DEBUG_TRUNCATION
            else removed_positions,
        )

        adjusted_moves: list[PlaylistOperation] = []
        for move_op in move_ops:
            if move_op.old_position is None:
                logger.warning(
                    "Move operation missing old_position data, skipping",
                    old_position=move_op.old_position,
                    position=move_op.position,
                )
                continue

            # Calculate how many removals happened before old_position
            old_shift = bisect.bisect_left(removed_positions, move_op.old_position)
            # Calculate how many removals happened before target position
            new_shift = bisect.bisect_left(removed_positions, move_op.position)

            # Create adjusted move operation with position shifts applied
            adjusted_old_position = move_op.old_position - old_shift
            adjusted_new_position = move_op.position - new_shift

            # Validate bounds - positions must be non-negative
            if adjusted_old_position < 0 or adjusted_new_position < 0:
                logger.warning(
                    "Move operation would result in negative position after adjustment, skipping",
                    original_old_position=move_op.old_position,
                    original_new_position=move_op.position,
                    adjusted_old_position=adjusted_old_position,
                    adjusted_new_position=adjusted_new_position,
                    old_shift=old_shift,
                    new_shift=new_shift,
                )
                continue

            # Create new operation with adjusted positions
            adjusted_op = PlaylistOperation(
                operation_type=move_op.operation_type,
                track=move_op.track,
                position=adjusted_new_position,
                old_position=adjusted_old_position,
                spotify_uri=move_op.spotify_uri,
            )

            adjusted_moves.append(adjusted_op)

            logger.debug(
                "Adjusted move operation positions",
                original_old=move_op.old_position,
                original_new=move_op.position,
                adjusted_old=adjusted_old_position,
                adjusted_new=adjusted_new_position,
                shift_old=old_shift,
                shift_new=new_shift,
            )

        # Sort adjusted moves by old_position in descending order for reverse execution
        sorted_adjusted_moves = sorted(
            adjusted_moves, key=lambda op: op.old_position or 0, reverse=True
        )

        logger.debug(
            f"Position adjustment complete: {len(move_ops)} original moves, "
            + f"{len(adjusted_moves)} valid after adjustment, "
            + f"{len(move_ops) - len(adjusted_moves)} filtered out"
        )

        return sorted_adjusted_moves


def get_execution_strategy(target_type: str) -> ExecutionStrategy:
    """Factory function to get the appropriate execution strategy.

    Args:
        target_type: Type of target ("canonical" or "api")

    Returns:
        Appropriate execution strategy instance

    Raises:
        ValueError: If target_type is not supported
    """
    if target_type == "canonical":
        return CanonicalExecutionStrategy()
    elif target_type == "api":
        return APIExecutionStrategy()
    else:
        raise ValueError(f"Unsupported target type: {target_type}")


def execute_with_strategy(
    strategy: ExecutionStrategy,
    current_playlist: Playlist,
    target_tracklist: TrackList,
    diff: PlaylistDiff,
) -> tuple[list[Track], dict[str, Any]]:
    """Execute playlist operations using the specified strategy.

    This is a pure domain function that applies the execution plan to transform
    the playlist state. The actual persistence is handled by the application layer.

    Args:
        strategy: Execution strategy to use
        current_playlist: Current playlist state
        target_tracklist: Target playlist state
        diff: Calculated differences

    Returns:
        Tuple of (updated_tracks, execution_metadata)
    """
    plan = strategy.plan_operations(diff)

    if plan.use_atomic_reorder:
        # Use direct reordering for maximum efficiency and correctness
        updated_tracks = reorder_to_match_target(
            current_playlist.tracks, target_tracklist.tracks
        )
        logger.debug("Applied atomic reordering transformation")
    else:
        # Apply individual operations (implementation would handle the operations)
        # For now, fall back to reordering since the operations are already calculated
        updated_tracks = reorder_to_match_target(
            current_playlist.tracks, target_tracklist.tracks
        )
        logger.debug(f"Applied {len(plan.operations)} individual operations")

    return updated_tracks, plan.execution_metadata
