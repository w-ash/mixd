#!/usr/bin/env python3
"""Lightweight script to find potential duplicate canonical tracks.

Uses connector_tracks as source of truth to find canonical tracks that share
the same external IDs, indicating they might be duplicates that should be merged.
"""

import asyncio
from collections import defaultdict

from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)

console = Console()


async def find_duplicate_candidates() -> None:
    """Find canonical tracks that share connector track external IDs."""
    console.print("🔍 [blue]Searching for potential duplicate tracks...[/blue]")
    
    async with get_session() as session:
        
        # Query for all active connector tracks with their canonical track mappings
        stmt = (
            select(
                DBTrack.id.label("track_id"),
                DBTrack.title,
                DBTrack.artists,
                DBConnectorTrack.connector_name,
                DBConnectorTrack.connector_track_id,
                DBConnectorTrack.isrc,
            )
            .select_from(DBTrackMapping)
            .join(DBTrack, DBTrackMapping.track_id == DBTrack.id)
            .join(DBConnectorTrack, DBTrackMapping.connector_track_id == DBConnectorTrack.id)
            .where(
                DBTrackMapping.is_deleted == False,  # noqa: E712
                DBTrack.is_deleted == False,  # noqa: E712
                DBConnectorTrack.is_deleted == False,  # noqa: E712
            )
        )
        
        result = await session.execute(stmt)
        rows = result.fetchall()
        
        # Group by external identifiers
        external_id_groups = defaultdict(list)
        
        for row in rows:
            # Group by (connector_name, connector_track_id) pairs
            key = (row.connector_name, row.connector_track_id)
            external_id_groups[key].append(row)
            
            # Also group by ISRC if available
            if row.isrc:
                isrc_key = ("isrc", row.isrc)
                external_id_groups[isrc_key].append(row)
        
        # Find groups with multiple canonical tracks
        duplicates_found = []
        seen_track_pairs = set()
        
        for external_id, tracks in external_id_groups.items():
            # Get unique canonical track IDs
            unique_tracks = {}
            for track in tracks:
                if track.track_id not in unique_tracks:
                    unique_tracks[track.track_id] = track
            
            if len(unique_tracks) > 1:
                # Found potential duplicates
                track_list = list(unique_tracks.values())
                for i in range(len(track_list)):
                    for j in range(i + 1, len(track_list)):
                        track_pair = tuple(sorted([track_list[i].track_id, track_list[j].track_id]))
                        if track_pair not in seen_track_pairs:
                            duplicates_found.append((
                                external_id,
                                track_list[i],
                                track_list[j]
                            ))
                            seen_track_pairs.add(track_pair)
        
        if not duplicates_found:
            console.print("✅ [green]No duplicate tracks found![/green]")
            return
        
        console.print(f"\n⚠️  [yellow]Found {len(duplicates_found)} potential duplicate pairs[/yellow]")
        
        # Display results in a table
        table = Table(title="Potential Duplicate Tracks")
        table.add_column("External ID", style="dim")
        table.add_column("Track 1", style="cyan")
        table.add_column("Track 2", style="cyan")
        table.add_column("Similarity", justify="center")
        
        for external_id, track1, track2 in duplicates_found:
            # Format external ID
            if external_id[0] == "isrc":
                ext_id_str = f"ISRC: {external_id[1]}"
            else:
                ext_id_str = f"{external_id[0]}: {external_id[1]}"
            
            # Format track info
            track1_info = f"ID {track1.track_id}: {track1.title}"
            if track1.artists and "names" in track1.artists:
                track1_info += f" - {', '.join(track1.artists['names'])}"
            
            track2_info = f"ID {track2.track_id}: {track2.title}"  
            if track2.artists and "names" in track2.artists:
                track2_info += f" - {', '.join(track2.artists['names'])}"
            
            # Simple similarity check
            similarity = "🟢 High" if track1.title.lower() == track2.title.lower() else "🟡 Medium"
            
            table.add_row(ext_id_str, track1_info, track2_info, similarity)
        
        console.print(table)
        
        # Show merge commands
        console.print("\n📋 [bold blue]Merge Commands:[/bold blue]")
        console.print("Review the duplicates above and merge them using:")
        console.print()
        
        for _, track1, track2 in duplicates_found[:5]:  # Show first 5
            # Suggest lower ID as winner (older track)
            winner_id = min(track1.track_id, track2.track_id)
            loser_id = max(track1.track_id, track2.track_id)
            console.print(f"  narada tracks merge --winner-id {winner_id} --loser-id {loser_id}")
        
        if len(duplicates_found) > 5:
            console.print(f"  ... and {len(duplicates_found) - 5} more")
            
        console.print()
        console.print("💡 [dim]Tip: Use 'narada tracks show <track-id>' to inspect tracks before merging[/dim]")


async def main() -> None:
    """Main entry point."""
    try:
        await find_duplicate_candidates()
    except KeyboardInterrupt:
        console.print("\n❌ [yellow]Cancelled by user[/yellow]")
    except Exception as e:
        console.print(f"❌ [red]Error: {e}[/red]")
        raise


if __name__ == "__main__":
    asyncio.run(main())