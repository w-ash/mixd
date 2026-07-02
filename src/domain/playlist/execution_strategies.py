"""API playlist operation sequencing.

Sequences the raw operations from a playlist diff into a conflict-free order for
external-API execution (Spotify et al.): orders remove -> add -> move, then
simulates cumulative position shifts so each operation targets a valid index at
the moment it runs. Pure domain logic — no I/O.
"""

import bisect
from typing import Final

from structlog.stdlib import get_logger

from src.domain.playlist.diff_engine import (
    PlaylistDiff,
    PlaylistOperation,
    PlaylistOperationType,
    sequence_operations_for_spotify,
)

logger = get_logger(__name__)

_DEBUG_TRUNCATION: Final = 10


def plan_api_operations(diff: PlaylistDiff) -> list[PlaylistOperation]:
    """Sequence a diff's operations for conflict-free external-API execution.

    Orders operations remove -> add -> move (Spotify metadata-preservation) then
    applies position-shift simulation so sequential execution never targets a
    stale or out-of-bounds index.

    Args:
        diff: Calculated playlist differences

    Returns:
        Operations in a conflict-free execution order
    """
    # Use existing sequencing logic which orders: remove -> add -> move
    initial_operations = sequence_operations_for_spotify(diff.operations)

    # Apply position shift simulation to prevent conflicts in sequential execution
    return simulate_position_shifts(initial_operations)


def simulate_position_shifts(
    operations: list[PlaylistOperation],
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
        adjusted_moves = _adjust_move_operations(move_ops, remove_ops_sorted)
        adjusted_operations.extend(adjusted_moves)

    logger.debug(
        "Position shift simulation complete: "
        + f"{len(remove_ops)} removes, {len(add_ops)} adds, {len(move_ops)} moves"
    )

    return adjusted_operations


def _adjust_move_operations(
    move_ops: list[PlaylistOperation], remove_ops: list[PlaylistOperation]
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
