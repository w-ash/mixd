#!/usr/bin/env python3
"""Report canonical tracks that share a normalized artist+title.

Finding C4: canonical reuse used to gate on ISRC, and a remaster never shares an
ISRC with its original — so the same recording could land twice as two canonical
tracks. Creation is fixed on both paths; the rows already written are not. This
reports them.

Read-only by construction. Every statement is a SELECT and there is no
``--apply``: merging is ``mixd track merge``'s job, which already carries a
confirmation gate and an identity check. A bulk merger here would be a second,
weaker copy of that.

Each group of tracks equal under ``(title_normalized, artist_normalized)`` is
classified by duration spread:

* **duration-compatible** — every duration within ``SUSPECT_DURATION_DIFF_MS``
  (10s, the same threshold that separates ISRC_EXACT from ISRC_SUSPECT). These
  are genuine duplicates and get a merge command.
* **distinct recordings** — a wider spread, or a duration missing so the spread
  can't be established. A live take, an extended mix, and a radio edit share an
  artist and a title while being different recordings; merging them would
  destroy real data. Reported and deliberately skipped.

Survivor rule (the user's decision): **the track whose ISRC is the newer
release wins.** Recency comes from the ISRC's year of reference — characters 6-7
of a CC-XXX-YY-NNNNN code — with the two-digit year pivoted on the current
century. When the year is absent on either side, equal across the group, or
unparseable, the rule falls back to the newer ``created_at``. Every group prints
which of the two rules decided it.

Usage:
    uv run python scripts/find_duplicate_canonicals.py [--user-id <id>]
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import setup_script_logger
from src.config.constants import BusinessLimits
from src.config.settings import settings
from src.domain.matching.isrc_validation import SUSPECT_DURATION_DIFF_MS
from src.infrastructure.persistence.database import get_session

# An ISRC is CC-XXX-YY-NNNNN once punctuation is stripped: 2-char country,
# 3-char registrant, 2-digit year of reference, 5-digit designation code.
_ISRC_LENGTH = 12
_ISRC_YEAR_START = 5
_ISRC_YEAR_END = 7

# Groups equal under the normalized columns, with everything the survivor rule
# and the duration classification need. Ordered so a group's rows arrive
# together, oldest first.
_GROUPS_QUERY = """
WITH grouped AS (
    SELECT title_normalized, artist_normalized
    FROM tracks
    WHERE user_id = :user_id
      AND title_normalized IS NOT NULL
      AND artist_normalized IS NOT NULL
    GROUP BY 1, 2
    HAVING count(*) > 1
)
SELECT t.id,
       t.title,
       t.artists_text,
       t.title_normalized,
       t.artist_normalized,
       t.isrc,
       t.duration_ms,
       t.created_at,
       (SELECT count(*) FROM track_plays p WHERE p.track_id = t.id) AS play_count
FROM tracks t
JOIN grouped g
  ON g.title_normalized = t.title_normalized
 AND g.artist_normalized = t.artist_normalized
WHERE t.user_id = :user_id
ORDER BY t.artist_normalized, t.title_normalized, t.created_at, t.id
"""


@dataclass(frozen=True)
class _Candidate:
    """One canonical track inside a duplicate group."""

    track_id: UUID
    title: str
    artist: str
    isrc: str | None
    duration_ms: int | None
    created_at: datetime
    play_count: int

    @property
    def isrc_year(self) -> int | None:
        """Four-digit year of reference from the ISRC, if it carries one."""
        return _isrc_year(self.isrc)

    def describe(self) -> str:
        duration = (
            f"{self.duration_ms / 1000:.1f}s"
            if self.duration_ms
            else "duration unknown"
        )
        year = self.isrc_year
        isrc = f"{self.isrc} (year {year})" if year else (self.isrc or "no ISRC")
        return (
            f"{self.track_id}  {self.artist} — {self.title}  "
            f"[{duration}, {isrc}, {self.play_count} plays, "
            f"created {self.created_at:%Y-%m-%d}]"
        )


@dataclass(frozen=True)
class _Group:
    """A set of canonical tracks equal under the normalized columns."""

    members: list[_Candidate]

    @property
    def duration_spread_ms(self) -> int | None:
        """Widest duration gap in the group; None if any duration is missing."""
        durations = [m.duration_ms for m in self.members if m.duration_ms]
        if len(durations) != len(self.members):
            return None
        return max(durations) - min(durations)

    @property
    def is_duration_compatible(self) -> bool:
        spread = self.duration_spread_ms
        return spread is not None and spread <= SUSPECT_DURATION_DIFF_MS

    @property
    def skip_reason(self) -> str:
        spread = self.duration_spread_ms
        if spread is None:
            return "a duration is missing — the spread can't be established"
        return (
            f"durations spread {spread / 1000:.1f}s, over the "
            f"{SUSPECT_DURATION_DIFF_MS / 1000:.0f}s threshold — different recordings"
        )


def _isrc_year(isrc: str | None) -> int | None:
    """Four-digit year of reference encoded in an ISRC, or None.

    The code carries only two digits, so the century is pivoted on today: a
    value at or below the current two-digit year reads as 20xx, anything above
    it as 19xx. ISRC allocation began in the 1980s, so no ambiguity survives
    that pivot in practice.
    """
    if not isrc:
        return None
    compact = "".join(c for c in isrc if c.isalnum()).upper()
    if len(compact) != _ISRC_LENGTH:
        return None
    digits = compact[_ISRC_YEAR_START:_ISRC_YEAR_END]
    if not digits.isdigit():
        return None
    two_digit = int(digits)
    pivot = datetime.now(UTC).year % 100
    return 2000 + two_digit if two_digit <= pivot else 1900 + two_digit


def pick_survivor(group: _Group) -> tuple[_Candidate, str]:
    """Choose the keeper for a duplicate group, and name the rule that chose it.

    Newer ISRC release year wins. Falls back to the newer ``created_at`` when no
    member carries a parseable year, or when the highest year is tied.
    """
    years = [(m, m.isrc_year) for m in group.members]
    dated = [(m, year) for m, year in years if year is not None]
    if dated:
        newest = max(year for _, year in dated)
        leaders = [m for m, year in dated if year == newest]
        if len(leaders) == 1:
            return leaders[0], f"isrc_year ({newest})"
    return max(
        group.members, key=lambda m: m.created_at
    ), "created_at (ISRC year absent, tied, or unparseable)"


async def load_groups(session: AsyncSession, user_id: str) -> list[_Group]:
    """Fetch every duplicate group for one user, oldest member first."""
    result = await session.execute(text(_GROUPS_QUERY), {"user_id": user_id})
    buckets: dict[tuple[str, str], list[_Candidate]] = {}
    for row in result.mappings().all():
        values: dict[str, object] = dict(row)
        key = (str(values["artist_normalized"]), str(values["title_normalized"]))
        buckets.setdefault(key, []).append(
            _Candidate(
                track_id=_as_uuid(values["id"]),
                title=str(values["title"]),
                artist=str(values["artists_text"] or values["artist_normalized"]),
                isrc=_as_str_or_none(values["isrc"]),
                duration_ms=_as_int_or_none(values["duration_ms"]),
                created_at=_as_datetime(values["created_at"]),
                play_count=_as_int_or_none(values["play_count"]) or 0,
            )
        )
    return [_Group(members=members) for members in buckets.values()]


def _as_uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _as_str_or_none(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _as_int_or_none(value: object) -> int | None:
    return (
        int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    )


def _as_datetime(value: object) -> datetime:
    return value if isinstance(value, datetime) else datetime.now(UTC)


def _render(groups: list[_Group], user_id: str) -> None:
    """Print the report: mergeable groups with commands, then the skipped ones."""
    mergeable = [g for g in groups if g.is_duration_compatible]
    distinct = [g for g in groups if not g.is_duration_compatible]
    rule_counts: dict[str, int] = {}

    print(f"\n=== duplicate canonicals for user {user_id} ===")
    print(f"{len(groups)} groups share a normalized artist+title\n")

    print(f"--- MERGEABLE: {len(mergeable)} duration-compatible groups ---")
    for group in mergeable:
        survivor, rule = pick_survivor(group)
        rule_key = rule.split(" ")[0]
        rule_counts[rule_key] = rule_counts.get(rule_key, 0) + 1
        spread = group.duration_spread_ms or 0
        print(f"\n  group: {survivor.artist} — {survivor.title} (spread {spread}ms)")
        print(f"    decided by: {rule}")
        print(f"    SURVIVOR  {survivor.describe()}")
        for loser in group.members:
            if loser.track_id == survivor.track_id:
                continue
            print(f"    loser     {loser.describe()}")
            print(
                f"      mixd track merge --winner-id {survivor.track_id} "
                f"--loser-id {loser.track_id}"
            )

    print(f"\n--- SKIPPED: {len(distinct)} groups are distinct recordings ---")
    for group in distinct:
        first = group.members[0]
        print(f"\n  group: {first.artist} — {first.title}")
        print(f"    reason: {group.skip_reason}")
        for member in group.members:
            print(f"    keep      {member.describe()}")

    print("\n=== summary ===")
    print(f"  {len(mergeable)} groups mergeable (duration-compatible)")
    print(f"  {len(distinct)} groups skipped as distinct recordings")
    for rule, count in sorted(rule_counts.items()):
        print(f"  survivor decided by {rule}: {count} groups")
    print(
        "  nothing was changed — run the printed `mixd track merge` commands "
        "to act on any of it"
    )


async def main() -> None:
    setup_script_logger("find_duplicate_canonicals")

    args = sys.argv[1:]
    user_id = settings.cli.user_id or BusinessLimits.DEFAULT_USER_ID
    if "--user-id" in args:
        user_id = args[args.index("--user-id") + 1]

    async with get_session() as session:
        groups = await load_groups(session, user_id)

    if not groups:
        print(f"No duplicate canonicals for user {user_id}.")
        print("Pass --user-id <id> if you meant a different user.")
        return

    _render(groups, user_id)


if __name__ == "__main__":
    asyncio.run(main())
