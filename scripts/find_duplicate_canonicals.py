#!/usr/bin/env python3
"""Report every canonical track pair that should be merged, in one batch.

The v0.10.3 audit left two populations of canonical tracks that describe one
recording twice. They arrived by different routes and are reported here
together, because the operator reviewing them is approving one list of merges,
not two:

**Same-user duplicates (finding C4).** Canonical reuse used to gate on ISRC, and
a remaster never shares an ISRC with its original — so the same recording could
land twice under one user as two canonical tracks. Creation is fixed on both
paths; the rows already written are not.

**Cross-tenant collisions (finding C3).** An early mistenanted poller run created
canonicals under ``"default"``; a later import running as the real user found
and reused them, because the cross-tenant read that should have been impossible
succeeded. ``scripts/reown_cross_tenant_tracks.py`` re-owns those it can, and
*refuses* the ones where the target user already owns a colliding track —
``uq_tracks_user_isrc`` would fire on the UPDATE. A refusal there is a duplicate
here: two rows for one recording, one of which the user's play history hangs off
but does not own. Merging is the remedy; this script reports them.

Read-only by construction. Every statement is a SELECT and there is no
``--apply``: merging is ``mixd tracks merge``'s job, which already carries a
confirmation gate and an identity check. A bulk merger here would be a second,
weaker copy of that.

One caveat the cross-tenant section prints in full: ``MergeTracksUseCase``
resolves both ids with a user-scoped ``get_track_by_id``, so a loser owned by
another tenant is ``NotFoundError`` and the printed command cannot run until the
two rows sit under one tenant. The commands are still the right remedy and the
right winner/loser assignment — they name work the CLI has to be taught to
accept, not work this script should do itself.

One definition of "same recording"
----------------------------------
Both populations are classified by the same predicate the creation paths now
gate on — ``describes_same_recording`` over ``describe_track``, from
``src.domain.matching.recording_identity``. Normalized title+artist equality
plus a duration within ``SUSPECT_DURATION_DIFF_MS`` (10s, the line between
ISRC_EXACT and ISRC_SUSPECT). A second, subtly-different copy of the question
here is exactly how a report comes to disagree with the code that prevents the
next duplicate.

Pairs the predicate refuses are reported and deliberately skipped:

* **distinct recordings** — a live take, an extended mix and a radio edit share
  an artist and a title while being different recordings. Merging them would
  destroy real data.
* **duration unknown** — with a duration missing there is nothing left to accept
  on, so the spread can't be established either way.

A cross-tenant pair that fails the predicate is louder than a same-user one: it
means two tenants hold genuinely *different* recordings that a unique constraint
happens to collide on, which is an identity conflict needing a decision, not a
duplicate. Those print under CONFLICT and get no merge command.

Survivor rules — one per population, and the difference is the point
--------------------------------------------------------------------
**Same-user groups: the newer ISRC release wins.** Recency comes from the ISRC's
year of reference — characters 6-7 of a CC-XXX-YY-NNNNN code — with the
two-digit year pivoted on the current century. When the year is absent on either
side, equal across the group, or unparseable, the rule falls back to the newer
``created_at``. Every group prints which of the two rules decided it.

**Cross-tenant pairs: the track the target user owns wins — always.** Ownership
decides here, not the ISRC, and the ISRC rule above is deliberately not applied.
The defect being repaired is a user whose listening history hangs off a row
another tenant owns; merging *into* the other tenant's track because its ISRC
looks newer would leave the history hanging off a row the user still does not
own — the same defect, now with the duplicate cleaned up around it. The survivor
must be the user's own row even when the foreign one is the newer release.

Usage:
    uv run python scripts/find_duplicate_canonicals.py [--user-id <id>]
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations, starmap
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import setup_script_logger
from src.config.constants import BusinessLimits
from src.config.settings import settings
from src.domain.entities.track import Artist, Track
from src.domain.matching.isrc_validation import SUSPECT_DURATION_DIFF_MS
from src.domain.matching.recording_identity import (
    describe_track,
    describes_same_recording,
)
from src.infrastructure.persistence.database import get_session

# An ISRC is CC-XXX-YY-NNNNN once punctuation is stripped: 2-char country,
# 3-char registrant, 2-digit year of reference, 5-digit designation code.
_ISRC_LENGTH = 12
_ISRC_YEAR_START = 5
_ISRC_YEAR_END = 7

# Both queries select the same per-track column set, aliased by a prefix so one
# row can carry two tracks. They are written out rather than interpolated from a
# shared template: an f-string here is a hardcoded-SQL-expression finding, and
# the alternative to spelling the columns twice is a suppression.
# ``artists->'names'->>0`` is the credit-order primary artist — the one field
# ``describe_track`` compares on, because featured-artist billing differs
# between services for the same recording.

# Groups equal under the normalized columns, with everything the survivor rule
# and the same-recording predicate need. Ordered so a group's rows arrive
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
SELECT
    t.title_normalized,
    t.id AS id,
    t.title AS title,
    t.artists_text AS artists_text,
    t.artists->'names'->>0 AS primary_artist,
    t.artist_normalized AS artist_normalized,
    t.isrc AS isrc,
    t.spotify_id AS spotify_id,
    t.mbid AS mbid,
    t.duration_ms AS duration_ms,
    t.created_at AS created_at,
    (SELECT count(*) FROM track_plays p WHERE p.track_id = t.id) AS play_count
FROM tracks t
JOIN grouped g
  ON g.title_normalized = t.title_normalized
 AND g.artist_normalized = t.artist_normalized
WHERE t.user_id = :user_id
ORDER BY t.artist_normalized, t.title_normalized, t.created_at, t.id
"""

# Tracks another tenant owns that the target user's plays depend on, paired with
# the track the user *already owns* that a re-own would collide with. This is
# ``reown_cross_tenant_tracks.py``'s refusal set, restated as merge candidates:
# the collision flags are the same four, computed the same way, so the two
# scripts cannot disagree about which tracks are re-ownable and which are not.
_CROSS_TENANT_QUERY = """
WITH depended AS (
    SELECT tp.track_id AS id FROM track_plays tp WHERE tp.user_id = :user_id
    UNION
    SELECT cp.resolved_track_id AS id FROM connector_plays cp
     WHERE cp.user_id = :user_id AND cp.resolved_track_id IS NOT NULL
),
foreign_tracks AS (
    SELECT t.*
      FROM tracks t
      JOIN depended d ON d.id = t.id
     WHERE t.user_id <> :user_id
),
clashes AS (
    SELECT f.*,
      (SELECT o.id FROM tracks o
        WHERE o.user_id = :user_id AND f.isrc IS NOT NULL AND o.isrc = f.isrc
        LIMIT 1) AS clash_isrc,
      (SELECT o.id FROM tracks o
        WHERE o.user_id = :user_id AND f.spotify_id IS NOT NULL
          AND o.spotify_id = f.spotify_id LIMIT 1) AS clash_spotify_id,
      (SELECT o.id FROM tracks o
        WHERE o.user_id = :user_id AND f.mbid IS NOT NULL AND o.mbid = f.mbid
        LIMIT 1) AS clash_mbid,
      (SELECT o.id FROM tracks o
        WHERE o.user_id = :user_id
          AND f.title_normalized IS NOT NULL AND f.artist_normalized IS NOT NULL
          AND o.title_normalized = f.title_normalized
          AND o.artist_normalized = f.artist_normalized LIMIT 1) AS clash_normalized
    FROM foreign_tracks f
)
SELECT
    c.user_id AS foreign_owner,
    c.clash_isrc,
    c.clash_spotify_id,
    c.clash_mbid,
    c.clash_normalized,
    (SELECT count(*) FROM track_plays tp
      WHERE tp.user_id = :user_id AND tp.track_id = c.id) AS dep_track_plays,
    (SELECT count(*) FROM connector_plays cp
      WHERE cp.user_id = :user_id AND cp.resolved_track_id = c.id)
      AS dep_connector_plays,
    c.id AS foreign_id,
    c.title AS foreign_title,
    c.artists_text AS foreign_artists_text,
    c.artists->'names'->>0 AS foreign_primary_artist,
    c.artist_normalized AS foreign_artist_normalized,
    c.isrc AS foreign_isrc,
    c.spotify_id AS foreign_spotify_id,
    c.mbid AS foreign_mbid,
    c.duration_ms AS foreign_duration_ms,
    c.created_at AS foreign_created_at,
    (SELECT count(*) FROM track_plays p WHERE p.track_id = c.id)
      AS foreign_play_count,
    o.id AS owned_id,
    o.title AS owned_title,
    o.artists_text AS owned_artists_text,
    o.artists->'names'->>0 AS owned_primary_artist,
    o.artist_normalized AS owned_artist_normalized,
    o.isrc AS owned_isrc,
    o.spotify_id AS owned_spotify_id,
    o.mbid AS owned_mbid,
    o.duration_ms AS owned_duration_ms,
    o.created_at AS owned_created_at,
    (SELECT count(*) FROM track_plays p WHERE p.track_id = o.id)
      AS owned_play_count
FROM clashes c
JOIN tracks o
  ON o.id = coalesce(c.clash_isrc, c.clash_spotify_id, c.clash_mbid,
                     c.clash_normalized)
ORDER BY c.artist_normalized, c.title_normalized, c.id
"""

# Clash column → the constraint it names. Same wording as
# ``reown_cross_tenant_tracks._CLASH_REASONS`` so a pair can be traced between
# the two reports without translating.
_COLLISION_LABELS: tuple[tuple[str, str], ...] = (
    ("clash_isrc", "uq_tracks_user_isrc"),
    ("clash_spotify_id", "uq_tracks_user_spotify_id"),
    ("clash_mbid", "uq_tracks_user_mbid"),
    ("clash_normalized", "normalization-equal (no constraint — same title+artist)"),
)


@dataclass(frozen=True)
class _Candidate:
    """One canonical track, on either side of either population."""

    track_id: UUID
    title: str
    artist: str
    primary_artist: str
    isrc: str | None
    duration_ms: int | None
    created_at: datetime
    play_count: int
    spotify_id: str | None = None
    mbid: str | None = None

    @property
    def isrc_year(self) -> int | None:
        """Four-digit year of reference from the ISRC, if it carries one."""
        return _isrc_year(self.isrc)

    def as_track(self) -> Track:
        """The row as the domain entity ``describe_track`` asks about.

        Only the three fields the same-recording question turns on are real;
        the rest are the entity's own defaults. Building it is what lets the
        predicate stay the single definition rather than being re-implemented
        over raw columns here.
        """
        return Track(
            title=self.title,
            artists=[Artist(name=self.primary_artist)],
            duration_ms=self.duration_ms,
            isrc=self.isrc,
            id=self.track_id,
        )

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


def same_recording(a: _Candidate, b: _Candidate) -> bool:
    """Do two rows describe one recording? The domain predicate, unmodified."""
    return describes_same_recording(
        describe_track(a.as_track()), describe_track(b.as_track())
    )


@dataclass(frozen=True)
class _Group:
    """A set of canonical tracks one user owns, equal under the normalized columns."""

    members: list[_Candidate]

    @property
    def duration_spread_ms(self) -> int | None:
        """Widest duration gap in the group; None if any duration is missing."""
        durations = [m.duration_ms for m in self.members if m.duration_ms]
        if len(durations) != len(self.members):
            return None
        return max(durations) - min(durations)

    @property
    def is_mergeable(self) -> bool:
        """Every pair in the group is the same recording under the predicate."""
        return all(starmap(same_recording, combinations(self.members, 2)))

    @property
    def skip_reason(self) -> str:
        spread = self.duration_spread_ms
        if spread is None:
            return "a duration is missing — the spread can't be established"
        if spread > SUSPECT_DURATION_DIFF_MS:
            return (
                f"durations spread {spread / 1000:.1f}s, over the "
                f"{SUSPECT_DURATION_DIFF_MS / 1000:.0f}s threshold — "
                f"different recordings"
            )
        return (
            "the normalized title/artist stored on the rows no longer matches "
            "what normalization produces from the display text — the grouping "
            "columns are stale"
        )


@dataclass(frozen=True)
class _CrossTenantPair:
    """A track another tenant owns, beside the colliding track the user owns.

    ``owned`` is the survivor by construction — see the module docstring. It is
    never chosen by ISRC, because the whole point of the repair is that the
    user's history must end up on a row the user owns.
    """

    owned: _Candidate
    foreign: _Candidate
    foreign_owner: str
    dep_track_plays: int
    dep_connector_plays: int
    collisions: tuple[str, ...]
    elsewhere: tuple[tuple[str, UUID], ...] = field(default_factory=tuple)

    @property
    def is_mergeable(self) -> bool:
        return same_recording(self.owned, self.foreign)

    @property
    def dependent_plays(self) -> int:
        return self.dep_track_plays + self.dep_connector_plays

    @property
    def conflict_reason(self) -> str:
        diff = _duration_diff_ms(self.owned, self.foreign)
        if diff is None:
            return (
                "a duration is missing on one side — the two rows collide on a "
                "unique constraint with no evidence they are one recording"
            )
        if diff > SUSPECT_DURATION_DIFF_MS:
            return (
                f"durations differ by {diff / 1000:.1f}s, over the "
                f"{SUSPECT_DURATION_DIFF_MS / 1000:.0f}s threshold — a genuine "
                f"identity conflict, not a duplicate: two different recordings "
                f"share an identifier across the two tenants"
            )
        return (
            "normalized title/artist differ between the two rows — they collide "
            "on an identifier while describing different recordings"
        )


def _duration_diff_ms(a: _Candidate, b: _Candidate) -> int | None:
    if a.duration_ms and b.duration_ms:
        return abs(a.duration_ms - b.duration_ms)
    return None


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
    """Choose the keeper for a same-user group, and name the rule that chose it.

    Newer ISRC release year wins. Falls back to the newer ``created_at`` when no
    member carries a parseable year, or when the highest year is tied.

    Deliberately *not* used for cross-tenant pairs: there the survivor is the
    track the target user owns, whatever the ISRCs say.
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


def collisions_for(row: dict[str, object], owned_id: UUID) -> tuple[str, ...]:
    """The constraints this cross-tenant pair collides on.

    Pure over the discovery row so the contract is unit-testable: a clash column
    naming the paired owned track always yields its constraint label, and the
    tuple is never empty for a row the query returned (the JOIN is on the
    coalesce of exactly these columns).
    """
    return tuple(
        label
        for column, label in _COLLISION_LABELS
        if row.get(column) is not None and _as_uuid(row[column]) == owned_id
    )


def collisions_elsewhere(
    row: dict[str, object], owned_id: UUID
) -> tuple[tuple[str, UUID], ...]:
    """Clashes naming a *third* track the user owns, not the paired survivor.

    Rare and worth seeing: it means the foreign track collides with two
    different rows of the user's, so one merge does not clear it.
    """
    return tuple(
        (label, _as_uuid(row[column]))
        for column, label in _COLLISION_LABELS
        if row.get(column) is not None and _as_uuid(row[column]) != owned_id
    )


async def load_groups(session: AsyncSession, user_id: str) -> list[_Group]:
    """Fetch every same-user duplicate group, oldest member first."""
    result = await session.execute(text(_GROUPS_QUERY), {"user_id": user_id})
    buckets: dict[tuple[str, str], list[_Candidate]] = {}
    for row in result.mappings().all():
        values: dict[str, object] = dict(row)
        key = (str(values["artist_normalized"]), str(values["title_normalized"]))
        buckets.setdefault(key, []).append(_candidate(values, prefix=""))
    return [_Group(members=members) for members in buckets.values()]


async def load_cross_tenant_pairs(
    session: AsyncSession, user_id: str
) -> list[_CrossTenantPair]:
    """Fetch every cross-tenant collision the target user's plays depend on."""
    result = await session.execute(text(_CROSS_TENANT_QUERY), {"user_id": user_id})
    pairs: list[_CrossTenantPair] = []
    for row in result.mappings().all():
        values: dict[str, object] = dict(row)
        owned = _candidate(values, prefix="owned_")
        pairs.append(
            _CrossTenantPair(
                owned=owned,
                foreign=_candidate(values, prefix="foreign_"),
                foreign_owner=str(values["foreign_owner"]),
                dep_track_plays=_as_int_or_none(values["dep_track_plays"]) or 0,
                dep_connector_plays=_as_int_or_none(values["dep_connector_plays"]) or 0,
                collisions=collisions_for(values, owned.track_id),
                elsewhere=collisions_elsewhere(values, owned.track_id),
            )
        )
    return pairs


def _candidate(values: dict[str, object], *, prefix: str) -> _Candidate:
    """One track's columns out of a row, under the alias prefix they carry."""
    artists_text = _as_str_or_none(values[f"{prefix}artists_text"])
    primary = _as_str_or_none(values[f"{prefix}primary_artist"])
    normalized = _as_str_or_none(values.get(f"{prefix}artist_normalized"))
    return _Candidate(
        track_id=_as_uuid(values[f"{prefix}id"]),
        title=str(values[f"{prefix}title"]),
        artist=artists_text or primary or normalized or "unknown artist",
        primary_artist=primary or "",
        isrc=_as_str_or_none(values[f"{prefix}isrc"]),
        duration_ms=_as_int_or_none(values[f"{prefix}duration_ms"]),
        created_at=_as_datetime(values[f"{prefix}created_at"]),
        play_count=_as_int_or_none(values[f"{prefix}play_count"]) or 0,
        spotify_id=_as_str_or_none(values[f"{prefix}spotify_id"]),
        mbid=_as_str_or_none(values[f"{prefix}mbid"]),
    )


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


def _render_same_user(groups: list[_Group]) -> tuple[int, dict[str, int]]:
    """Print the same-user population. Returns its merge count and rule tally."""
    mergeable = [g for g in groups if g.is_mergeable]
    distinct = [g for g in groups if not g.is_mergeable]
    rule_counts: dict[str, int] = {}
    merges = 0

    print("\n############  POPULATION 1: same-user duplicate canonicals  ############")
    print(f"{len(groups)} groups share a normalized artist+title")
    print("survivor rule: the newer ISRC release year wins (created_at as fallback)\n")

    print(f"--- MERGEABLE: {len(mergeable)} same-recording groups ---")
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
            merges += 1
            print(f"    loser     {loser.describe()}")
            print(
                f"      mixd tracks merge --winner-id {survivor.track_id} "
                f"--loser-id {loser.track_id}"
            )

    print(f"\n--- SKIPPED: {len(distinct)} groups are distinct recordings ---")
    for group in distinct:
        first = group.members[0]
        print(f"\n  group: {first.artist} — {first.title}")
        print(f"    reason: {group.skip_reason}")
        for member in group.members:
            print(f"    keep      {member.describe()}")

    return merges, rule_counts


def _render_cross_tenant(pairs: list[_CrossTenantPair], user_id: str) -> int:
    """Print the cross-tenant population. Returns its merge count."""
    mergeable = [p for p in pairs if p.is_mergeable]
    conflicts = [p for p in pairs if not p.is_mergeable]

    print("\n\n############  POPULATION 2: cross-tenant collisions  ############")
    print(
        f"{len(pairs)} tracks another tenant owns back this user's plays *and* "
        f"collide with a track the user already owns"
    )
    owners: dict[str, int] = {}
    for pair in pairs:
        owners[pair.foreign_owner] = owners.get(pair.foreign_owner, 0) + 1
    for owner, count in sorted(owners.items()):
        print(f"  foreign tenant {owner!r}: {count} tracks")
    print(
        f"survivor rule: the track {user_id!r} OWNS wins — always. Ownership "
        f"decides, not the ISRC:"
    )
    print(
        "  merging into the other tenant's row would leave this user's history "
        "on a row they do not own, which is the defect being repaired."
    )
    print(
        "  these are exactly reown_cross_tenant_tracks.py's refusals — the "
        "unique constraint makes a re-own impossible, so a merge is the remedy."
    )
    print(
        "PRECONDITION: `mixd tracks merge` verifies BOTH ids under the CLI user "
        "(MergeTracksUseCase calls get_track_by_id twice, and that read is "
        "user-scoped), so the commands below raise NotFoundError on the loser "
        "until the two rows sit under one tenant. A cross-tenant merge needs "
        "the CLI to accept a loser owned elsewhere; nothing in this population "
        "is actionable through the existing gate as it stands."
    )

    print(f"\n--- MERGEABLE: {len(mergeable)} same-recording pairs ---")
    for pair in mergeable:
        _render_pair(pair)
        print(
            f"      mixd tracks merge --winner-id {pair.owned.track_id} "
            f"--loser-id {pair.foreign.track_id}"
        )

    print(
        f"\n--- CONFLICT: {len(conflicts)} pairs collide but are NOT the same "
        f"recording ---"
    )
    for pair in conflicts:
        _render_pair(pair)
        print(f"    NOT MERGEABLE: {pair.conflict_reason}")

    return len(mergeable)


def _render_pair(pair: _CrossTenantPair) -> None:
    """Both sides of a cross-tenant pair with the evidence for the decision."""
    print(f"\n  pair: {pair.owned.artist} — {pair.owned.title}")
    print(f"    collides on: {', '.join(pair.collisions)}")
    print(f"    SURVIVOR  (owned)   {pair.owned.describe()}")
    print(f"    loser     (owner={pair.foreign_owner}) {pair.foreign.describe()}")
    print(
        f"      this user's plays on the loser: {pair.dep_track_plays} "
        f"track_plays + {pair.dep_connector_plays} connector_plays"
    )
    for label, other_id in pair.elsewhere:
        print(
            f"      ALSO collides with a different owned track on {label}: {other_id}"
        )


def _render(groups: list[_Group], pairs: list[_CrossTenantPair], user_id: str) -> None:
    """Print both populations, then the one combined summary."""
    print(f"\n=== canonical duplicates for user {user_id} ===")

    same_user_merges, rule_counts = _render_same_user(groups)
    cross_tenant_merges = _render_cross_tenant(pairs, user_id)

    mergeable_groups = sum(1 for g in groups if g.is_mergeable)
    distinct_groups = len(groups) - mergeable_groups
    conflict_pairs = len(pairs) - cross_tenant_merges

    print("\n\n=== combined summary ===")
    print(f"  {mergeable_groups} same-user groups mergeable")
    print(f"  {cross_tenant_merges} cross-tenant pairs mergeable")
    print(f"  {distinct_groups} same-user groups skipped as distinct recordings")
    print(f"  {conflict_pairs} cross-tenant pairs skipped as identity conflicts")
    for rule, count in sorted(rule_counts.items()):
        print(f"  same-user survivor decided by {rule}: {count} groups")
    print(
        f"  TOTAL MERGES TO APPROVE: {same_user_merges + cross_tenant_merges} "
        f"({same_user_merges} same-user + {cross_tenant_merges} cross-tenant)"
    )
    print(
        "  nothing was changed — run the printed `mixd tracks merge` commands "
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
        pairs = await load_cross_tenant_pairs(session, user_id)

    if not groups and not pairs:
        print(f"No duplicate canonicals for user {user_id}.")
        print("Pass --user-id <id> if you meant a different user.")
        return

    _render(groups, pairs, user_id)


if __name__ == "__main__":
    asyncio.run(main())
