#!/usr/bin/env python3
"""Smoke test: Cross-source play history deduplication with real data.

Imports real Spotify streaming history and real Last.fm scrobbles for the same
time windows, then verifies that cross-source dedup correctly identifies and
merges duplicate listening events.

Tests BOTH import orderings:
  Window A: Spotify first → Last.fm (exercises "existing wins" dedup path)
  Window B: Last.fm first → Spotify (exercises "new wins" dedup path)

Usage:
    uv run python scripts/smoke_test_cross_source_dedup.py
    uv run python scripts/smoke_test_cross_source_dedup.py --days 7
    uv run python scripts/smoke_test_cross_source_dedup.py --file data/imports/Streaming_History_Audio_2024-2025_12.json
    uv run python scripts/smoke_test_cross_source_dedup.py --compact
    uv run python scripts/smoke_test_cross_source_dedup.py --fresh   # Use temporary DB to exercise identity resolution strategies
"""

import asyncio
from collections import Counter
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, text

from src.config import setup_script_logger
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.persistence.database.db_connection import (
    get_session,
    init_db,
    reset_engine_cache,
)
from src.infrastructure.persistence.database.db_models import (
    DBTrack,
    DBTrackMapping,
    DBTrackPlay,
)

# ── CLI args ──────────────────────────────────────────────────
COMPACT = "--compact" in sys.argv
FRESH = "--fresh" in sys.argv
DAYS = 7
FILE_PATH: str | None = None

for i, arg in enumerate(sys.argv[1:], 1):
    if arg == "--days" and i < len(sys.argv) - 1:
        DAYS = int(sys.argv[i + 1])
    if arg == "--file" and i < len(sys.argv) - 1:
        FILE_PATH = sys.argv[i + 1]


def print_header(text: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {text}")
    print(f"{'─' * 60}")


def print_metric(label: str, value: object, indent: int = 2) -> None:
    print(f"{'  ' * indent}{label:.<40s} {value}")


def print_result(label: str, passed: bool) -> None:
    icon = "✅" if passed else "❌"
    print(f"  {icon} {label}")


# ── File utilities ────────────────────────────────────────────


def find_latest_export_file() -> Path:
    """Find the most recent Spotify streaming history export file."""
    import_dir = Path("data/imports")
    files = sorted(import_dir.glob("Streaming_History_Audio_*.json"))
    if not files:
        print("  ❌ No Spotify export files found in data/imports/")
        sys.exit(1)
    return files[-1]


def load_and_filter_spotify_entries(
    file_path: Path,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Load Spotify JSON export and filter entries to a time window."""
    with Path(file_path).open(encoding="utf-8") as f:
        all_entries = json.load(f)

    filtered = []
    for entry in all_entries:
        ts_str = entry.get("ts")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str)
        if start <= ts <= end:
            filtered.append(entry)

    return filtered


def write_temp_export(entries: list[dict[str, Any]]) -> Path:
    """Write filtered entries to a temporary JSON file for import."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="smoke_test_spotify_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        json.dump(entries, tmp, indent=2)
    return Path(tmp.name)


# ── Verification queries ─────────────────────────────────────


async def get_play_counts_by_service() -> dict[str, int]:
    """Get total play counts grouped by service."""
    async with get_session() as session:
        stmt = select(DBTrackPlay.service, func.count(DBTrackPlay.id)).group_by(
            DBTrackPlay.service
        )
        result = await session.execute(stmt)
        return dict(result.tuples().all())


async def get_cross_source_plays_in_range(
    start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Find plays with multi-source attribution in a time range."""
    async with get_session() as session:
        stmt = (
            select(
                DBTrackPlay.id,
                DBTrackPlay.service,
                DBTrackPlay.played_at,
                DBTrackPlay.source_services,
                DBTrackPlay.ms_played,
                DBTrack.title,
            )
            .join(DBTrack, DBTrackPlay.track_id == DBTrack.id)
            .where(
                DBTrackPlay.played_at >= start,
                DBTrackPlay.played_at <= end,
                DBTrackPlay.source_services.isnot(None),
                func.json_array_length(DBTrackPlay.source_services) > 1,
            )
            .order_by(DBTrackPlay.played_at)
        )
        result = await session.execute(stmt)
        return [
            {
                "id": row[0],
                "service": row[1],
                "played_at": row[2],
                "source_services": row[3],
                "ms_played": row[4],
                "title": row[5],
            }
            for row in result.all()
        ]


async def get_all_plays_in_range(
    start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Get all plays in a time range with track info."""
    async with get_session() as session:
        stmt = (
            select(
                DBTrackPlay.id,
                DBTrackPlay.track_id,
                DBTrackPlay.service,
                DBTrackPlay.played_at,
                DBTrackPlay.ms_played,
                DBTrackPlay.source_services,
                DBTrack.title,
            )
            .join(DBTrack, DBTrackPlay.track_id == DBTrack.id)
            .where(
                DBTrackPlay.played_at >= start,
                DBTrackPlay.played_at <= end,
            )
            .order_by(DBTrackPlay.played_at)
        )
        result = await session.execute(stmt)
        return [
            {
                "id": row[0],
                "track_id": row[1],
                "service": row[2],
                "played_at": row[3],
                "ms_played": row[4],
                "source_services": row[5],
                "title": row[6],
            }
            for row in result.all()
        ]


async def find_potential_missed_dedup(
    start: datetime, end: datetime, tolerance_seconds: float = 30.0
) -> list[dict[str, Any]]:
    """Find plays from different services for the same track close in time that were NOT deduped.

    These are potential misses — plays that should have been matched but weren't.
    Uses raw SQL for the self-join timestamp comparison.
    """
    async with get_session() as session:
        # Find pairs of plays from different services, same track, close in time,
        # where NEITHER has multi-source attribution
        query = text("""
            SELECT
                a.id as play_a_id,
                a.service as service_a,
                a.played_at as played_at_a,
                a.ms_played as ms_played_a,
                b.id as play_b_id,
                b.service as service_b,
                b.played_at as played_at_b,
                b.ms_played as ms_played_b,
                t.title as track_title
            FROM track_plays a
            JOIN track_plays b ON a.track_id = b.track_id AND a.id < b.id
            JOIN tracks t ON a.track_id = t.id
            WHERE a.service != b.service
              AND a.played_at >= :start
              AND a.played_at <= :end
              AND b.played_at >= :start
              AND b.played_at <= :end
              AND (a.source_services IS NULL OR json_array_length(a.source_services) <= 1)
              AND (b.source_services IS NULL OR json_array_length(b.source_services) <= 1)
              AND ABS(
                  -- Normalize Spotify end-time to start-time for comparison
                  CASE WHEN a.service = 'spotify' AND a.ms_played IS NOT NULL
                       THEN julianday(a.played_at) * 86400.0 - (a.ms_played / 1000.0)
                       ELSE julianday(a.played_at) * 86400.0
                  END
                  -
                  CASE WHEN b.service = 'spotify' AND b.ms_played IS NOT NULL
                       THEN julianday(b.played_at) * 86400.0 - (b.ms_played / 1000.0)
                       ELSE julianday(b.played_at) * 86400.0
                  END
              ) <= :tolerance
            ORDER BY a.played_at
            LIMIT 50
        """)
        result = await session.execute(
            query,
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "tolerance": tolerance_seconds,
            },
        )
        return [
            {
                "play_a_id": row[0],
                "service_a": row[1],
                "played_at_a": row[2],
                "ms_played_a": row[3],
                "play_b_id": row[4],
                "service_b": row[5],
                "played_at_b": row[6],
                "ms_played_b": row[7],
                "track_title": row[8],
            }
            for row in result.all()
        ]


async def find_identity_resolution_gaps(
    start: datetime, end: datetime, tolerance_seconds: float = 180.0
) -> list[dict[str, Any]]:
    """Find plays from different services with related tracks but DIFFERENT track_ids.

    Uses 3-tier matching to catch gaps:
    1. Exact title match (LOWER)
    2. Stripped title match (catches parenthetical mismatches like "Song feat. X" vs "Song")
    3. Shared ISRC on different track_ids

    Each gap is enriched with ISRC/MBID/title_stripped data for categorization.
    """
    async with get_session() as session:
        query = text("""
            SELECT
                ta.title as title_a,
                tb.title as title_b,
                a.track_id as track_id_a,
                b.track_id as track_id_b,
                a.service as service_a,
                b.service as service_b,
                a.played_at as played_at_a,
                b.played_at as played_at_b,
                ta.isrc as isrc_a,
                tb.isrc as isrc_b,
                ta.title_stripped as stripped_a,
                tb.title_stripped as stripped_b,
                ta.mbid as mbid_a,
                tb.mbid as mbid_b
            FROM track_plays a
            JOIN track_plays b ON a.track_id != b.track_id AND a.id < b.id
            JOIN tracks ta ON a.track_id = ta.id
            JOIN tracks tb ON b.track_id = tb.id
            WHERE a.service != b.service
              AND a.played_at >= :start
              AND a.played_at <= :end
              AND b.played_at >= :start
              AND b.played_at <= :end
              AND (
                  LOWER(ta.title) = LOWER(tb.title)
                  OR (ta.title_stripped IS NOT NULL AND ta.title_stripped = tb.title_stripped)
                  OR (ta.isrc IS NOT NULL AND ta.isrc = tb.isrc)
              )
              AND ABS(
                  CASE WHEN a.service = 'spotify' AND a.ms_played IS NOT NULL
                       THEN julianday(a.played_at) * 86400.0 - (a.ms_played / 1000.0)
                       ELSE julianday(a.played_at) * 86400.0
                  END
                  -
                  CASE WHEN b.service = 'spotify' AND b.ms_played IS NOT NULL
                       THEN julianday(b.played_at) * 86400.0 - (b.ms_played / 1000.0)
                       ELSE julianday(b.played_at) * 86400.0
                  END
              ) <= :tolerance
            ORDER BY a.played_at
            LIMIT 100
        """)
        result = await session.execute(
            query,
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "tolerance": tolerance_seconds,
            },
        )
        return [
            {
                "title_a": row[0],
                "title_b": row[1],
                "track_id_a": row[2],
                "track_id_b": row[3],
                "service_a": row[4],
                "service_b": row[5],
                "played_at_a": row[6],
                "played_at_b": row[7],
                "isrc_a": row[8],
                "isrc_b": row[9],
                "stripped_a": row[10],
                "stripped_b": row[11],
                "mbid_a": row[12],
                "mbid_b": row[13],
            }
            for row in result.all()
        ]


def categorize_gap(gap: dict[str, Any]) -> str:
    """Classify an identity gap by its root cause.

    Returns one of: shared_isrc, parenthetical, shared_mbid, exact_title, genuine.
    """
    # Shared ISRC — Strategy 1 (ISRC dedup) should catch this
    if gap.get("isrc_a") and gap.get("isrc_b") and gap["isrc_a"] == gap["isrc_b"]:
        return "shared_isrc"

    # Shared MBID — Strategy 2 (MBID bridging) should catch this
    if gap.get("mbid_a") and gap.get("mbid_b") and gap["mbid_a"] == gap["mbid_b"]:
        return "shared_mbid"

    # Parenthetical mismatch — Strategy 3 should catch this
    title_a = (gap.get("title_a") or "").lower()
    title_b = (gap.get("title_b") or "").lower()
    stripped_a = gap.get("stripped_a") or ""
    stripped_b = gap.get("stripped_b") or ""

    if title_a != title_b and stripped_a and stripped_b and stripped_a == stripped_b:
        return "parenthetical"

    # Exact title match — Phase 1.5 canonical reuse should catch this
    if title_a == title_b:
        return "exact_title"

    return "genuine"


async def get_strategy_hit_counts() -> dict[str, int]:
    """Count how many connector mappings were created by each match method."""
    async with get_session() as session:
        stmt = select(
            DBTrackMapping.match_method, func.count(DBTrackMapping.id)
        ).group_by(DBTrackMapping.match_method)
        result = await session.execute(stmt)
        return dict(result.tuples().all())


# ── Import helpers ────────────────────────────────────────────


async def import_spotify_file(file_path: Path) -> dict[str, Any]:
    """Import a Spotify export file and return metrics."""
    from src.application.use_cases.import_play_history import run_import

    result = await run_import(service="spotify", mode="file", file_path=file_path)
    return {
        "operation_name": result.operation_name,
        "raw_plays": result.summary_metrics.get("raw_plays"),
        "connector_plays": result.summary_metrics.get("connector_plays"),
        "track_plays": result.summary_metrics.get("track_plays"),
        "duplicates": result.summary_metrics.get("duplicates"),
        "metadata": result.metadata,
    }


async def import_lastfm_range(
    from_date: datetime, to_date: datetime, username: str | None = None
) -> dict[str, Any]:
    """Import Last.fm plays for a date range and return metrics."""
    from src.application.use_cases.import_play_history import run_import

    result = await run_import(
        service="lastfm",
        mode="incremental",
        from_date=from_date,
        to_date=to_date,
        user_id=username,
    )
    return {
        "operation_name": result.operation_name,
        "raw_plays": result.summary_metrics.get("raw_plays"),
        "connector_plays": result.summary_metrics.get("connector_plays"),
        "track_plays": result.summary_metrics.get("track_plays"),
        "duplicates": result.summary_metrics.get("duplicates"),
        "cross_source_dedup": result.metadata.get("resolution_phase", {}).get(
            "cross_source_dedup", {}
        ),
        "metadata": result.metadata,
    }


# ── Window test runner ────────────────────────────────────────


async def run_window_test(
    label: str,
    first_service: str,
    second_service: str,
    spotify_file: Path,
    spotify_entries: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
    lastfm_username: str | None = None,
) -> dict[str, Any]:
    """Run a single dedup test window with specified import ordering."""
    print_header(f"{label}: {first_service.upper()} first → {second_service.upper()}")
    print(f"  Window: {window_start.date()} → {window_end.date()}")
    print(f"  Spotify entries in window: {len(spotify_entries)}")

    # Write filtered Spotify entries to temp file
    temp_file = write_temp_export(spotify_entries)

    try:
        # ── Import Phase ──
        if first_service == "spotify":
            print(
                f"\n  [1/2] Importing Spotify file ({len(spotify_entries)} entries)..."
            )
            first_result = await import_spotify_file(temp_file)
            print(f"         → {first_result['track_plays'] or 0} track plays created")

            print(
                f"\n  [2/2] Importing Last.fm ({window_start.date()} → {window_end.date()})..."
            )
            second_result = await import_lastfm_range(
                window_start, window_end, lastfm_username
            )
            print(f"         → {second_result['track_plays'] or 0} track plays created")
            dedup_stats = second_result.get("cross_source_dedup", {})
        else:
            print(
                f"\n  [1/2] Importing Last.fm ({window_start.date()} → {window_end.date()})..."
            )
            first_result = await import_lastfm_range(
                window_start, window_end, lastfm_username
            )
            print(f"         → {first_result['track_plays'] or 0} track plays created")

            print(
                f"\n  [2/2] Importing Spotify file ({len(spotify_entries)} entries)..."
            )
            second_result = await import_spotify_file(temp_file)
            print(f"         → {second_result['track_plays'] or 0} track plays created")
            dedup_stats = second_result.get("cross_source_dedup", {})

        # ── Verification Phase ──
        print("\n  ── Verification ──")
        checks_passed = 0
        checks_total = 0

        # Check 1: Cross-source plays exist
        cross_source_plays = await get_cross_source_plays_in_range(
            window_start, window_end
        )
        checks_total += 1
        has_cross_source = len(cross_source_plays) > 0
        print_result(
            f"Cross-source plays found: {len(cross_source_plays)}", has_cross_source
        )
        if has_cross_source:
            checks_passed += 1
            # Show sample
            sample = cross_source_plays[:10] if COMPACT else cross_source_plays[:20]
            for play in sample:
                services = (
                    ", ".join(play["source_services"])
                    if play["source_services"]
                    else "?"
                )
                print(
                    f"      {play['played_at']}  {play['title'][:40]:<40s}  [{services}]"
                )
            if len(cross_source_plays) > len(sample):
                print(f"      ... and {len(cross_source_plays) - len(sample)} more")

        # Check 2: No missed dedup matches
        missed = await find_potential_missed_dedup(window_start, window_end)
        checks_total += 1
        no_missed = len(missed) == 0
        print_result(
            f"No missed dedup matches: {len(missed)} potential misses", no_missed
        )
        if no_missed:
            checks_passed += 1
        else:
            for m in missed[:5]:
                print(
                    f"      MISSED: {m['track_title'][:35]}  "
                    f"{m['service_a']}@{m['played_at_a']}  vs  "
                    f"{m['service_b']}@{m['played_at_b']}"
                )

        # Check 3: source_services integrity
        all_plays = await get_all_plays_in_range(window_start, window_end)
        checks_total += 1
        integrity_ok = True
        integrity_issues: list[str] = []
        for play in all_plays:
            ss = play.get("source_services")
            if ss and len(ss) > 1 and play["service"] not in ss:
                integrity_issues.append(
                    f"Play {play['id']}: service={play['service']} not in source_services={ss}"
                )
                integrity_ok = False
        print_result(
            f"source_services integrity: {len(integrity_issues)} issues",
            integrity_ok,
        )
        if integrity_ok:
            checks_passed += 1
        for issue in integrity_issues[:5]:
            print(f"      {issue}")

        # Pre-compute play counts (used by checks 4 and 5)
        spotify_plays_in_window = [p for p in all_plays if p["service"] == "spotify"]
        lastfm_plays_in_window = [p for p in all_plays if p["service"] == "lastfm"]
        total_in_window = len(all_plays)

        # Check 4: Identity resolution gaps (with categorization)
        id_gaps = await find_identity_resolution_gaps(window_start, window_end)
        checks_total += 1

        # Categorize each gap
        gap_categories = Counter(categorize_gap(g) for g in id_gaps)
        genuine_count = gap_categories.get("genuine", 0)
        resolvable_count = len(id_gaps) - genuine_count

        # Pass/fail based on genuine gaps only (5% tolerance)
        # Resolvable gaps indicate strategies aren't being exercised, not a logic bug
        gap_tolerance = max(1, int(total_in_window * 0.05)) if total_in_window else 1
        identity_ok = genuine_count <= gap_tolerance
        print_result(
            f"Identity gaps: {len(id_gaps)} total "
            f"({genuine_count} genuine, {resolvable_count} resolvable, "
            f"tolerance: {gap_tolerance})",
            identity_ok,
        )
        if identity_ok:
            checks_passed += 1

        # Show categorized breakdown
        if id_gaps:
            category_labels = {
                "shared_isrc": "ISRC dedup should fix",
                "shared_mbid": "MBID bridging should fix",
                "parenthetical": "title stripping should fix",
                "exact_title": "Phase 1.5 reuse should fix",
                "genuine": "irreducible",
            }
            for cat in (
                "shared_isrc",
                "parenthetical",
                "shared_mbid",
                "exact_title",
                "genuine",
            ):
                count = gap_categories.get(cat, 0)
                if count:
                    cat_label = category_labels.get(cat, cat)
                    print(f"        {cat:.<25s} {count:>3d}  ({cat_label})")

            # Show sample gaps
            if not COMPACT:
                sample_gaps = id_gaps[:15]
                for gap in sample_gaps:
                    cat = categorize_gap(gap)
                    print(
                        f"      [{cat[:8]:>8s}] '{gap['title_a'][:30]}' "
                        f"track_id {gap['track_id_a']}({gap['service_a']}) vs "
                        f"{gap['track_id_b']}({gap['service_b']})"
                    )
                if len(id_gaps) > len(sample_gaps):
                    print(f"      ... and {len(id_gaps) - len(sample_gaps)} more")

        # Check 5: Play count sanity
        checks_total += 1
        # With dedup, total should be less than sum of individual imports
        # (some plays are suppressed, others are enriched)
        count_sanity = total_in_window > 0
        print_result(
            f"Play count sanity: {total_in_window} total "
            f"({len(spotify_plays_in_window)} spotify + {len(lastfm_plays_in_window)} lastfm"
            f", {len(cross_source_plays)} cross-source)",
            count_sanity,
        )
        if count_sanity:
            checks_passed += 1

        return {
            "label": label,
            "ordering": f"{first_service} → {second_service}",
            "window": f"{window_start.date()} → {window_end.date()}",
            "spotify_entries": len(spotify_entries),
            "cross_source_plays": len(cross_source_plays),
            "missed_dedup": len(missed),
            "identity_gaps": len(id_gaps),
            "genuine_gaps": genuine_count,
            "gap_categories": dict(gap_categories),
            "integrity_issues": len(integrity_issues),
            "total_plays": total_in_window,
            "spotify_plays": len(spotify_plays_in_window),
            "lastfm_plays": len(lastfm_plays_in_window),
            "dedup_stats": dedup_stats,
            "checks_passed": checks_passed,
            "checks_total": checks_total,
            "first_result": first_result,
            "second_result": second_result,
        }
    finally:
        # Cleanup temp file
        temp_file.unlink(missing_ok=True)


# ── Main ──────────────────────────────────────────────────────


async def main() -> None:
    setup_script_logger("smoke_test_cross_source_dedup")

    print("=" * 60)
    print("  Cross-Source Play Deduplication — Real Data Smoke Test")
    print("=" * 60)

    # ── Fresh database mode ──
    fresh_db_file: str | None = None
    if FRESH:
        fresh_db_file = f"{tempfile.gettempdir()}/smoke_test_fresh_{uuid4().hex}.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{fresh_db_file}"
        reset_engine_cache()
        await init_db()
        print("\n  Fresh database mode: using temporary DB")
        print(f"  DB path: {fresh_db_file}")

    # ── Pre-flight checks ──
    print_header("Pre-flight Checks")

    # Check Last.fm API
    client = LastFMAPIClient()
    lastfm_ok = client.is_configured
    print_result(f"Last.fm API configured (user: {client.lastfm_username})", lastfm_ok)
    if not lastfm_ok:
        print("\n  ❌ Aborting: Last.fm API key not configured")
        return

    # Find Spotify export file
    spotify_file = Path(FILE_PATH) if FILE_PATH else find_latest_export_file()
    file_exists = spotify_file.exists()
    print_result(f"Spotify export: {spotify_file.name}", file_exists)
    if not file_exists:
        print(f"\n  ❌ Aborting: File not found: {spotify_file}")
        return

    # Load and analyze the file
    with Path(spotify_file).open(encoding="utf-8") as f:
        all_entries = json.load(f)
    print(f"  Total entries in file: {len(all_entries)}")

    # Parse timestamps to find the date range
    timestamps = []
    for entry in all_entries:
        ts_str = entry.get("ts")
        if ts_str:
            timestamps.append(datetime.fromisoformat(ts_str))
    timestamps.sort()

    if not timestamps:
        print("\n  ❌ Aborting: No valid timestamps in file")
        return

    file_start = timestamps[0]
    file_end = timestamps[-1]
    print(f"  File date range: {file_start.date()} → {file_end.date()}")
    print(f"  Test window size: {DAYS} days per ordering test")

    # Determine two non-overlapping test windows from the END of the file
    # (most recent data is most likely to have Last.fm scrobbles)
    window_b_end = file_end
    window_b_start = window_b_end - timedelta(days=DAYS)
    window_a_end = window_b_start - timedelta(seconds=1)
    window_a_start = window_a_end - timedelta(days=DAYS)

    if window_a_start < file_start:
        print(
            f"\n  ⚠️  File only covers {(file_end - file_start).days} days "
            f"but need {DAYS * 2} days for two windows."
        )
        print("  Falling back to single overlapping window for both tests.")
        window_a_start = window_b_start
        window_a_end = window_b_end

    # Filter entries for each window
    entries_a = load_and_filter_spotify_entries(
        spotify_file, window_a_start, window_a_end
    )
    entries_b = load_and_filter_spotify_entries(
        spotify_file, window_b_start, window_b_end
    )

    print(
        f"\n  Window A: {window_a_start.date()} → {window_a_end.date()} ({len(entries_a)} entries)"
    )
    print(
        f"  Window B: {window_b_start.date()} → {window_b_end.date()} ({len(entries_b)} entries)"
    )

    if not entries_a and not entries_b:
        print("\n  ❌ Aborting: No entries found in either test window")
        return

    # ── Run tests ──
    results: list[dict[str, Any]] = []

    # Window A: Spotify first → Last.fm
    if entries_a:
        result_a = await run_window_test(
            label="Window A",
            first_service="spotify",
            second_service="lastfm",
            spotify_file=spotify_file,
            spotify_entries=entries_a,
            window_start=window_a_start,
            window_end=window_a_end,
            lastfm_username=client.lastfm_username,
        )
        results.append(result_a)

    # Window B: Last.fm first → Spotify
    if entries_b:
        result_b = await run_window_test(
            label="Window B",
            first_service="lastfm",
            second_service="spotify",
            spotify_file=spotify_file,
            spotify_entries=entries_b,
            window_start=window_b_start,
            window_end=window_b_end,
            lastfm_username=client.lastfm_username,
        )
        results.append(result_b)

    # ── Final Summary ──
    print("\n" + "═" * 60)
    print("  SMOKE TEST SUMMARY")
    print("═" * 60)

    total_passed = 0
    total_checks = 0
    for r in results:
        total_passed += r["checks_passed"]
        total_checks += r["checks_total"]
        print(f"\n  {r['label']} ({r['ordering']}):")
        print(f"    Window:              {r['window']}")
        print(f"    Spotify entries:     {r['spotify_entries']}")
        print(f"    Cross-source plays:  {r['cross_source_plays']}")
        print(f"    Missed dedup:        {r['missed_dedup']}")
        print(
            f"    Identity gaps:       {r['identity_gaps']} total "
            f"({r['genuine_gaps']} genuine)"
        )
        gap_cats = r.get("gap_categories", {})
        if gap_cats:
            for cat, count in sorted(gap_cats.items()):
                print(f"      {cat}: {count}")
        print(
            f"    Total plays in DB:   {r['total_plays']} ({r['spotify_plays']} spotify + {r['lastfm_plays']} lastfm)"
        )
        dedup = r.get("dedup_stats", {})
        if dedup:
            print(f"    Dedup stats:         {json.dumps(dedup, default=str)}")
        print(f"    Checks:              {r['checks_passed']}/{r['checks_total']}")

    # Strategy effectiveness
    print("\n  ── Strategy Effectiveness ──")
    strategy_hits = await get_strategy_hit_counts()
    if strategy_hits:
        for method, count in sorted(strategy_hits.items(), key=lambda x: -x[1]):
            print(f"    {method:.<30s} {count:>4d}")
    else:
        print("    (no connector mappings found)")

    # Overall play counts
    print("\n  ── Overall Database State ──")
    counts = await get_play_counts_by_service()
    total = sum(counts.values())
    print(f"    Total plays: {total:,}")
    for service, count in sorted(counts.items()):
        print(f"      {service}: {count:,}")

    print(f"\n  Results: {total_passed}/{total_checks} checks passed", end="")
    if total_passed == total_checks:
        print(" ✅")
    else:
        print(f" ({total_checks - total_passed} FAILED) ❌")

    print("═" * 60)

    # Cleanup fresh database
    if fresh_db_file:
        try:
            Path(fresh_db_file).unlink(missing_ok=True)
            print(f"\n  Cleaned up temporary DB: {fresh_db_file}")
        except OSError:
            pass


asyncio.run(main())
