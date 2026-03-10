"""Batch processing utilities for repository operations."""


def chunked[T](items: list[T], size: int) -> list[list[T]]:
    """Split items into fixed-size chunks for batched DB queries."""
    return [items[i : i + size] for i in range(0, len(items), size)]
