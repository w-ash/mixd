#!/usr/bin/env python3
"""
Data integrity audit script for finding connector tracks with multiple canonical mappings.

This script identifies violations of the fundamental data model constraint:
- RULE: connector_track → canonical_track should be many-to-one (never one-to-many)
- VIOLATION: Single connector track mapped to multiple canonical tracks

Created: 2025-08-13
Purpose: Investigate root cause of playlist backup idempotency issues
"""

from datetime import UTC, datetime
from pathlib import Path
import sqlite3


def find_duplicate_canonical_mappings(db_path: str = "data/db/narada.db"):
    """Find connector tracks that map to multiple canonical tracks.

    Args:
        db_path: Path to SQLite database file

    Returns:
        List of violation records with details
    """

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Find connector tracks with multiple canonical mappings
    query = """
    SELECT 
        ct.connector_name,
        ct.connector_track_id,
        COUNT(DISTINCT tm.track_id) as canonical_count,
        GROUP_CONCAT(tm.track_id) as canonical_ids,
        GROUP_CONCAT(tm.created_at) as mapping_created_times,
        ct.title as track_title,
        MAX(tm.created_at) as latest_mapping
    FROM connector_tracks ct
    JOIN track_mappings tm ON ct.id = tm.connector_track_id
    WHERE tm.is_deleted = 0
    GROUP BY ct.connector_name, ct.connector_track_id
    HAVING canonical_count > 1
    ORDER BY canonical_count DESC, latest_mapping DESC;
    """

    cursor.execute(query)
    violations = cursor.fetchall()

    conn.close()

    return violations


def analyze_violation_patterns(violations):
    """Analyze patterns in the violations to identify root causes."""

    print("🔍 VIOLATION PATTERN ANALYSIS")
    print("=" * 50)

    # Group by connector
    by_connector = {}
    for violation in violations:
        connector = violation[0]
        if connector not in by_connector:
            by_connector[connector] = []
        by_connector[connector].append(violation)

    print("Connectors affected:")
    for connector, conn_violations in by_connector.items():
        print(f"  {connector}: {len(conn_violations)} tracks with multiple mappings")

    print()

    # Analyze canonical count distribution
    canonical_counts = [v[2] for v in violations]
    from collections import Counter

    count_dist = Counter(canonical_counts)

    print("Distribution of canonical tracks per connector track:")
    for count, frequency in sorted(count_dist.items()):
        print(f"  {count} canonical tracks: {frequency} connector tracks")

    print()


def show_detailed_examples(violations, limit=5):
    """Show detailed examples of violations for investigation."""

    print(
        f"🚨 DETAILED VIOLATION EXAMPLES (showing {min(limit, len(violations))} of {len(violations)})"
    )
    print("=" * 80)

    for i, violation in enumerate(violations[:limit]):
        (
            connector_name,
            connector_track_id,
            canonical_count,
            canonical_ids,
            created_times,
            title,
            latest,
        ) = violation

        print(f"\nVIOLATION #{i + 1}:")
        print(f"  Connector: {connector_name}")
        print(f"  Track ID: {connector_track_id}")
        print(f"  Title: {title}")
        print(f"  Canonical tracks: {canonical_count} ({canonical_ids})")
        print(f"  Mapping created times: {created_times}")
        print(f"  Latest mapping: {latest}")

        # Analyze timing
        if created_times:
            times = created_times.split(",")
            if len(times) > 1:
                print("  ⚠️  Time gap analysis:")
                for j, time_str in enumerate(times):
                    print(f"    Mapping {j + 1}: {time_str}")


def main():
    """Main audit execution."""

    print("🚨 DATA INTEGRITY AUDIT: DUPLICATE CANONICAL MAPPINGS")
    print("=" * 60)
    print(f"Audit started: {datetime.now(UTC).isoformat()}")
    print()

    # Check if database exists
    db_path = Path("data/db/narada.db")
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        print("Make sure you're running this from the project root directory.")
        return

    # Find violations
    print("Scanning database for connector tracks with multiple canonical mappings...")
    violations = find_duplicate_canonical_mappings(str(db_path))

    if not violations:
        print(
            "✅ No violations found! All connector tracks map to exactly one canonical track."
        )
        return

    print(
        f"🚨 CRITICAL: Found {len(violations)} connector tracks with multiple canonical mappings!"
    )
    print()

    # Analyze patterns
    analyze_violation_patterns(violations)

    # Show detailed examples
    show_detailed_examples(violations, limit=10)

    print()
    print("💡 INVESTIGATION RECOMMENDATIONS:")
    print("1. Review track creation logs around the 'latest_mapping' timestamps")
    print("2. Check what processes were creating tracks during those periods")
    print("3. Investigate if recent architectural changes bypassed deduplication")
    print("4. Consider using TrackMergeService to consolidate duplicate tracks")
    print("5. Add database constraints to prevent future violations")

    print()
    print(f"Audit completed: {datetime.now(UTC).isoformat()}")


if __name__ == "__main__":
    main()
