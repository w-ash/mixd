"""Shared UI utilities for interface layer - works for CLI and future web interface."""

import json
from typing import Any

from rich.console import Console
from rich.table import Table

from src.domain.entities import OperationResult

console = Console()


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
    """Display result as formatted tables with track details and metrics."""
    # Display title
    display_title = title or result.operation_name or "Operation Results"
    console.print(f"\n[bold blue]{display_title}[/bold blue]")

    # Summary statistics
    summary_data = []

    # Play-based operations show both plays and tracks
    if hasattr(result, "plays_processed") and result.plays_processed > 0:
        summary_data.append(("Plays Processed", str(result.plays_processed)))
        summary_data.append(("Tracks Affected", str(len(result.tracks))))

        # Add play-level metrics
        if hasattr(result, "play_metrics"):
            for metric_name, metric_value in result.play_metrics.items():
                display_name = metric_name.replace("_", " ").title()
                summary_data.append((display_name, str(metric_value)))
    else:
        summary_data.append(("Tracks Processed", str(len(result.tracks))))

    # Add operation-specific summaries only if they have been set (not None)
    # Only show sync/import metrics if they are actually relevant to this operation
    if (hasattr(result, "imported_count") and result.imported_count is not None) or (
        hasattr(result, "exported_count") and result.exported_count is not None
    ):
        # Get values, treating None as not set (don't display)
        imported = result.imported_count
        exported = result.exported_count
        skipped = result.skipped_count
        errors = result.error_count
        total = result.total_processed
        already_liked = result.already_liked
        candidates = result.candidates
        success_rate = result.success_rate
        efficiency_rate = result.efficiency_rate

        # Show intelligence first (most important insight) if meaningful
        if already_liked is not None and already_liked > 0 and candidates is not None:
            summary_data.extend([
                ("Total Tracks", str(total or 0)),
                (
                    "Already Liked ✅",
                    f"{already_liked} ({efficiency_rate:.1f}%)"
                    if efficiency_rate
                    else str(already_liked),
                ),
                ("Candidates", str(candidates)),
            ])

        # Only show sync metrics that have been set
        if imported is not None:
            summary_data.append(("Imported", str(imported)))
        if exported is not None:
            summary_data.append(("Exported", str(exported)))
        if skipped is not None:
            summary_data.append(("Skipped", str(skipped)))
        if errors is not None:
            summary_data.append(("Errors", str(errors)))
        if success_rate is not None:
            summary_data.append(("Success Rate", f"{success_rate:.1f}%"))

    if hasattr(result, "execution_time") and result.execution_time > 0:
        summary_data.append(("Duration", f"{result.execution_time:.1f}s"))

    # Create summary table
    summary_table = Table(show_header=False, box=None, padding=(0, 2))
    summary_table.add_column(style="cyan")
    summary_table.add_column(style="green bold")

    for metric, value in summary_data:
        summary_table.add_row(metric, value)

    console.print(summary_table)

    # Track details table if we have tracks
    if result.tracks:
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
            details_table.add_column(display_name, style="yellow")

        # Add track rows
        for i, track in enumerate(result.tracks, 1):
            artist_name = track.artists[0].name if track.artists else "Unknown"

            # Get source information from tracklist metadata
            source_info = "Unknown"
            track_sources = (
                result.tracklist.metadata.get("track_sources", {})
                if hasattr(result, "tracklist") and result.tracklist
                else {}
            )
            if track.id and track.id in track_sources:
                source_data = track_sources[track.id]
                source_info = source_data.get("playlist_name", "Unknown")

            row = [str(i), artist_name, track.title, source_info]

            # Add metric values for this track
            for metric_name in metric_columns:
                value = result.get_metric(track.id, metric_name, "—")
                if metric_name == "sync_status" and isinstance(value, str):
                    # Add emoji for sync status
                    emoji = {
                        "imported": "✅",
                        "exported": "📤",
                        "skipped": "⚠️",
                        "error": "❌",
                    }.get(value, "❓")
                    row.append(f"{emoji} {value}")
                elif isinstance(value, float):
                    row.append(f"{value:.1f}")
                else:
                    row.append(str(value))

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
