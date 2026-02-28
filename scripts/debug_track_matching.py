#!/usr/bin/env python3
"""Debug script to test track matching pipeline for a specific canonical track ID.

This script replicates the exact same matching flow that the workflow enricher uses,
allowing us to debug why specific tracks are failing to match with external services.
"""

import asyncio
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
import typer

from src.application.use_cases.match_and_identify_tracks import (
    MatchAndIdentifyTracksCommand,
    MatchAndIdentifyTracksUseCase,
)
from src.config import get_logger
from src.domain.entities.track import TrackList
from src.infrastructure.connectors.lastfm import LastFMConnector
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

console = Console()
logger = get_logger(__name__)


async def debug_track_matching(track_id: int, connector_name: str = "lastfm") -> None:
    """Debug track matching for a specific canonical track ID."""
    console.print(
        f"\n[bold blue]🔍 Debugging track matching for ID {track_id} with {connector_name}[/bold blue]\n"
    )

    async with get_session() as session:
        uow = get_unit_of_work(session)

        # Step 1: Load the track from database
        console.print("[yellow]Step 1: Loading track from database...[/yellow]")
        try:
            track_repo = uow.get_track_repository()
            track = await track_repo.get_by_id(track_id)

            console.print(
                f"✅ Found track: [green]{track.title}[/green] by [green]{', '.join(a.name for a in track.artists)}[/green]"
            )
            if track.album:
                console.print(f"   Album: [dim]{track.album}[/dim]")
            if track.duration_ms:
                console.print(f"   Duration: [dim]{track.duration_ms}ms[/dim]")

            # Show existing connector mappings
            if track.connector_track_ids:
                console.print("   Existing connectors:")
                for connector, ext_id in track.connector_track_ids.items():
                    console.print(f"     • [cyan]{connector}[/cyan]: {ext_id}")
            else:
                console.print("   [dim]No existing connector mappings[/dim]")

        except Exception as e:
            console.print(f"❌ Failed to load track: [red]{e}[/red]")
            return

        # Step 2: Initialize connector
        console.print(
            f"\n[yellow]Step 2: Initializing {connector_name} connector...[/yellow]"
        )
        try:
            if connector_name == "lastfm":
                connector_instance = LastFMConnector()
            else:
                console.print(f"❌ Unsupported connector: {connector_name}")
                return

            console.print(f"✅ {connector_name} connector initialized")

        except Exception as e:
            console.print(f"❌ Failed to initialize connector: [red]{e}[/red]")
            return

        # Step 3: Create tracklist and command
        console.print("\n[yellow]Step 3: Setting up matching command...[/yellow]")
        try:
            tracklist = TrackList(tracks=[track])
            command = MatchAndIdentifyTracksCommand(
                tracklist=tracklist,
                connector=connector_name,
                connector_instance=connector_instance,
                max_age_hours=None,  # Accept any cached data
            )
            console.print("✅ Command created")

        except Exception as e:
            console.print(f"❌ Failed to create command: [red]{e}[/red]")
            return

        # Step 4: Execute matching (same as workflow)
        console.print("\n[yellow]Step 4: Executing identity resolution...[/yellow]")
        try:
            use_case = MatchAndIdentifyTracksUseCase()
            result = await use_case.execute(command, uow)

            # Display results
            console.print("\n[bold green]📊 Results Summary[/bold green]")
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Metric")
            table.add_column("Value")

            table.add_row("Tracks Processed", str(result.track_count))
            table.add_row("Successfully Resolved", str(result.resolved_count))
            table.add_row("Execution Time", f"{result.execution_time_ms}ms")
            table.add_row("Errors", str(len(result.errors)))

            console.print(table)

            # Show errors if any
            if result.errors:
                console.print("\n[red]❌ Errors:[/red]")
                for error in result.errors:
                    console.print(f"   • {error}")

            # Show identity mappings
            if result.identity_mappings:
                console.print("\n[green]✅ Identity Mappings Found:[/green]")
                for matched_id, match_result in result.identity_mappings.items():
                    console.print(f"   Track {matched_id}:")
                    console.print(
                        f"     • Success: [green]{match_result.success}[/green]"
                    )
                    console.print(
                        f"     • Connector ID: [cyan]{match_result.connector_id}[/cyan]"
                    )
                    console.print(
                        f"     • Confidence: [yellow]{match_result.confidence}[/yellow]"
                    )
                    console.print(
                        f"     • Match Method: [blue]{match_result.match_method}[/blue]"
                    )

                    if hasattr(match_result, "evidence") and match_result.evidence:
                        console.print("     • Evidence:")
                        evidence = match_result.evidence
                        if hasattr(evidence, "title_similarity"):
                            console.print(
                                f"       - Title similarity: {evidence.title_similarity:.3f}"
                            )
                        if hasattr(evidence, "artist_similarity"):
                            console.print(
                                f"       - Artist similarity: {evidence.artist_similarity:.3f}"
                            )
                        if hasattr(evidence, "duration_diff_ms"):
                            console.print(
                                f"       - Duration diff: {evidence.duration_diff_ms}ms"
                            )

                    if (
                        hasattr(match_result, "service_data")
                        and match_result.service_data
                    ):
                        console.print("     • Service Data:")
                        service_data = match_result.service_data
                        if "title" in service_data:
                            console.print(
                                f"       - Title: [green]{service_data['title']}[/green]"
                            )
                        if "artist" in service_data:
                            console.print(
                                f"       - Artist: [green]{service_data['artist']}[/green]"
                            )
                        if "lastfm_global_playcount" in service_data:
                            console.print(
                                f"       - Global plays: [yellow]{service_data['lastfm_global_playcount']}[/yellow]"
                            )
            else:
                console.print("\n[red]❌ No identity mappings found[/red]")

        except Exception as e:
            console.print(f"❌ Matching failed: [red]{e}[/red]")
            import traceback

            console.print(f"[dim]Traceback: {traceback.format_exc()}[/dim]")
            return


def main(
    track_id: int = typer.Argument(..., help="Canonical track ID to test matching for"),
    connector: str = typer.Option("lastfm", help="Connector to test (default: lastfm)"),
) -> None:
    """Debug track matching pipeline for a specific canonical track ID."""
    asyncio.run(debug_track_matching(track_id, connector))


if __name__ == "__main__":
    typer.run(main)
