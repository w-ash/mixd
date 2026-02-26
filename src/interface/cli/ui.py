"""Shared UI utilities for interface layer - works for CLI and future web interface."""

import json
from typing import Any, cast

from rich.console import Console
from rich.table import Table

from src.domain.entities import OperationResult
from src.domain.entities.summary_metrics import SummaryMetricFormat

console = Console()


def _format_metric_value(value: float, format: SummaryMetricFormat) -> str:
    """Format metric value based on format hint.

    Args:
        value: The metric value to format
        format: Format type ("count", "percent", "duration")

    Returns:
        Formatted string representation of the value
    """
    match format:
        case "percent":
            return f"{value:.1f}%"
        case "duration":
            return f"{value:.1f}s"
        case "count":
            return (
                str(int(value))
                if isinstance(value, float) and value.is_integer()
                else str(value)
            )


def _is_play_import_operation(result: OperationResult) -> bool:
    """Check if this is a play import operation that shouldn't show track details."""
    operation_name = result.operation_name or ""
    return any(
        keyword in operation_name.lower()
        for keyword in ["spotify import", "lastfm import", "play import"]
    )


def display_operation_result(
    result: OperationResult,
    output_format: str = "table",
    title: str | None = None,
    next_step_message: str | None = None,
) -> None:
    """Display operation result in the specified format.

    This is the unified display function that handles all operation results,
    providing rich formatting for workflow results with track listings and metrics.

    Args:
        result: The operation result to display
        output_format: Format to use ("table" or "json")
        title: Optional title override
        next_step_message: Optional follow-up message
    """
    if output_format == "json":
        _display_json_result(result)
    else:
        _display_table_result(result, title, next_step_message)


def _display_table_result(
    result: OperationResult,
    title: str | None = None,
    next_step_message: str | None = None,
) -> None:
    """Display result as formatted tables with summary metrics and track details."""
    # Display title
    display_title = title or result.operation_name or "Operation Results"
    console.print(f"\n[bold blue]{display_title}[/bold blue]")

    # Create summary table
    summary_table = Table(show_header=False, box=None, padding=(0, 2))
    summary_table.add_column(style="cyan")
    summary_table.add_column(style="green bold")

    # Display all summary metrics in sorted order (by significance)
    for metric in result.summary_metrics.sorted():
        formatted_value = _format_metric_value(metric.value, metric.format)
        summary_table.add_row(metric.label, formatted_value)

    # Add execution time if present
    if result.execution_time > 0:
        summary_table.add_row("Duration", f"{result.execution_time:.1f}s")

    console.print(summary_table)

    # Track details table if we have tracks - skip for play import operations
    if result.tracks and not _is_play_import_operation(result):
        console.print()
        details_table = Table(title="Track Details")
        details_table.add_column("#", style="dim", justify="right")
        details_table.add_column("Artist", style="cyan")
        details_table.add_column("Track", style="green")
        details_table.add_column("Source", style="blue")

        # Add metric columns - sorted for consistent display
        metric_columns = sorted(result.metrics.keys()) if result.metrics else []
        for metric_name in metric_columns:
            display_name = metric_name.replace("_", " ").title()
            details_table.add_column(display_name, style="yellow", justify="right")

        # Get fresh metric IDs from tracklist metadata (for cached vs fresh styling)
        fresh_metric_ids: dict[str, list[int]] = (
            result.tracklist.metadata.get("fresh_metric_ids", {})
            if hasattr(result, "tracklist") and result.tracklist
            else {}
        )

        # Add track rows
        for i, track in enumerate(result.tracks, 1):
            artist_name = track.artists[0].name if track.artists else "Unknown"

            # Get source information from tracklist metadata
            source_info = "Unknown"
            track_sources: dict[int, dict[str, Any]] = cast(
                dict[int, dict[str, Any]],
                result.tracklist.metadata.get("track_sources", {})
                if hasattr(result, "tracklist") and result.tracklist
                else {},
            )
            if track.id and track.id in track_sources:
                source_data: dict[str, Any] = track_sources[track.id]
                source_info = source_data.get("playlist_name", "Unknown")

            row: list[str] = [str(i), artist_name, track.title, source_info]

            # Add metric values for this track
            for metric_name in metric_columns:
                value = result.get_metric(track.id, metric_name, "—")
                # Check if this metric was freshly fetched or from cache
                is_fresh = track.id in fresh_metric_ids.get(metric_name, [])

                match (metric_name, value):
                    case ("sync_status", str() as status):
                        emoji = {
                            "imported": "✅",
                            "exported": "📤",
                            "skipped": "⚠️",
                            "error": "❌",
                        }.get(status, "❓")
                        row.append(f"{emoji} {status}")
                    case (_, float() as num):
                        formatted = f"{int(num)}"
                        row.append(formatted if is_fresh else f"[dim]{formatted}[/dim]")
                    case _:
                        formatted = str(value)
                        # Only dim numeric-like values, not placeholders
                        if value != "—" and not is_fresh:
                            row.append(f"[dim]{formatted}[/dim]")
                        else:
                            row.append(formatted)

            details_table.add_row(*row)

        console.print(details_table)

    # Next step message
    if next_step_message:
        console.print(f"\n[yellow]{next_step_message}[/yellow]")

    console.print()


def _display_json_result(result: OperationResult) -> None:
    """Display result as JSON."""
    result_dict = (
        result.to_dict() if hasattr(result, "to_dict") else _extract_result_data(result)
    )
    console.print_json(json.dumps(result_dict, indent=2))


def _extract_result_data(result: Any) -> dict[str, Any]:
    """Extract displayable data from result object."""
    if hasattr(result, "__dict__"):
        return {
            k: v
            for k, v in result.__dict__.items()
            if not k.startswith("_") and isinstance(v, (int, float, str, bool, list))
        }
    return {"result": str(result)}
