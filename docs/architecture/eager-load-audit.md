# Eager-Load Hardening Audit (v0.8.6, Story 1)

> **Status: APPLIED 2026-06-17.** All 31 relationships now carry an explicit
> `lazy=` — 24 newly guarded with `raise_on_sql` (the prior 7 unchanged) — and the
> 6 delete-orphan relationships that lacked it gained `passive_deletes=True`.
> `DBWorkflowRun.nodes` was verified (all 3 `include_nodes=True` paths eager-load
> via `selectinload` or `session.refresh`) and guarded. Full suite incl.
> slow/integration green (**3215 passed, 2 skipped**) with the global guard on; a
> guard-fires proof (`tests/integration/repositories/test_eager_load_guard.py`)
> confirms a forgotten eager-load raises `InvalidRequestError` (generic + the
> `nodes` case). The inventory, flip plan, and findings below are retained as the
> rationale of record.

## Why this exists

The cycle's persistence refactor made the mapper read primitives `loaded_list()`
/ `loaded_one()` deliberately **no-I/O**: a forgotten `selectinload` degrades to
`[]`/`None` rather than crashing — graceful but **silent**, so a wrong playlist
surfaces days later instead of at the failing query. `lazy="raise_on_sql"` makes
the *accidental* lazy load fail loud in dev/test (permits zero-IO identity-map
hits, forbids SQL-emitting loads). The v0.8.x refactor scoped the guard to **7**
mapper-traversed relationships; this epic decides the disposition of the other 24.

## Two findings that reshape the epic

1. **Cascade-delete is already safe by construction.** Every delete-orphan parent
   is deleted via **Core bulk `delete(Model)` + DB `ON DELETE CASCADE`** (every
   child FK carries `ondelete="CASCADE"`), never ORM `session.delete(parent)`. So
   `raise_on_sql` cannot block a cascade — the ORM never loads these collections
   on delete. `passive_deletes=True` is therefore **belt-and-braces** (guards a
   *future* accidental switch to `session.delete`), not a hard precondition. The
   two already-guarded delete-orphan rels (`DBTrack.likes`, `DBPlaylist.tracks`)
   cleared the bar this way, not via `passive_deletes`.
2. **Most of the graph has no reader.** ~21 of 31 relationships are never read
   through the ORM attribute (data is reached via separate repos / raw queries).
   Guarding them is free (nothing to break) and catches a future reader that
   forgets to eager-load. The one exception is the risk below.

## ⚠ The one risk: `DBWorkflowRun.nodes`

Read by **direct attribute access** in `workflow/run_mapper.py:76` (`db.nodes`),
currently unguarded (lazy default). It works today because the repo eager-loads
it (`selectinload(DBWorkflowRun.nodes)` at `workflow/runs.py:340,359` when
`include_nodes=True`). Guarding it with `raise_on_sql` is safe **only if** every
path that maps a run with nodes eager-loads first — must be verified (and a
regression test added) before flipping. This is the single migrate-or-verify case.

## Relationship inventory

`RoS` = `lazy="raise_on_sql"`. `d-o` = `cascade="all, delete-orphan"`.
`pd` = `passive_deletes=True`. Reader class: **(a)** eager-loaded & read,
**(b)** read only via `loaded_*` primitive (immune), **(c)** direct lazy read
(risk), **(d)** never read.

| Relationship | Dir | lazy | d-o | pd | Reader | Disposition |
|---|---|---|---|---|---|---|
| DBTrack.mappings | →many | **RoS** | — | ✓ | a | ✅ guarded |
| DBTrack.likes | →many | **RoS** | ✓ | — | a/b | ✅ guarded (add pd) |
| DBTrackMapping.connector_track | →one | **RoS** | — | ✓ | a/b | ✅ guarded |
| DBPlaylist.tracks | →many | **RoS** | ✓ | — | a | ✅ guarded (add pd) |
| DBPlaylist.mappings | →many | **RoS** | — | ✓ | a/b | ✅ guarded |
| DBPlaylistMapping.connector_playlist | →one | **RoS** | — | ✓ | a/b | ✅ guarded |
| DBPlaylistTrack.track | →one | **RoS** | — | ✓ | a/b | ✅ guarded |
| DBTrack.metrics | →many | — | ✓ | — | d | guard + add pd |
| DBTrack.plays | →many | — | ✓ | — | d | guard + add pd |
| DBTrack.preferences | →many | — | ✓ | — | d | guard + add pd |
| DBTrack.tags | →many | — | ✓ | — | d | guard + add pd |
| DBTrack.connector_plays | →many | — | — | ✓ | d | guard |
| DBConnectorTrack.mappings | →many | — | — | ✓ | d (reverse-read) | guard |
| DBTrackMapping.track | →one | — | — | ✓ | d (reciprocal) | guard |
| DBMatchReview.track | →one | — | — | ✓ | d | guard |
| DBMatchReview.connector_track | →one | — | — | ✓ | d | guard |
| DBTrackMetric.track | →one | — | — | ✓ | d | guard |
| DBTrackLike.track | →one | — | — | ✓ | d | guard |
| DBTrackPlay.track | →one | — | — | ✓ | d | guard |
| DBConnectorPlay.resolved_track | →one? | — | — | ✓ | d | guard |
| DBConnectorPlaylist.mappings | →many | — | — | ✓ | d | guard |
| DBPlaylistMapping.playlist | →one | — | — | ✓ | d (reciprocal) | guard |
| DBPlaylistTrack.playlist | →one | — | — | ✓ | d (reciprocal) | guard |
| DBWorkflowVersion.workflow | →one | — | — | ✓ | d | guard |
| DBWorkflowRun.workflow | →one | — | — | ✓ | d | guard |
| DBWorkflowRun.nodes | →many | — | ✓ | ✓ | **c** | ⚠ verify eager-load path, then guard |
| DBWorkflowRunNode.run | →one | — | — | ✓ | d (reciprocal) | guard |
| DBTrackPreference.track | →one | — | — | ✓ | d | guard |
| DBTrackTag.track | →one | — | — | ✓ | d | guard |
| DBPlaylistAssignment.members | →many | — | ✓ | ✓ | d | guard |
| DBPlaylistAssignmentMember.assignment | →one | — | — | ✓ | d (reciprocal) | guard |

(Per-mapper `get_default_relationships()`: `TrackMapper` →
`selectinload(DBTrack.mappings).selectinload(DBTrackMapping.connector_track)`,
`selectinload(DBTrack.likes)`; `PlaylistMapper` → the deep
`DBPlaylist.tracks → DBPlaylistTrack.track → DBTrack.mappings → …connector_track`
chain + `DBPlaylist.mappings → connector_playlist`. Other mappers return `[]`.)

## Recommended flip plan (awaiting sign-off)

1. **Add `passive_deletes=True`** to the 4 delete-orphan rels lacking it
   (`DBTrack.metrics/plays/preferences/tags`) and to the 2 guarded-but-missing-pd
   (`DBTrack.likes`, `DBPlaylist.tracks`). Belt-and-braces (deletes are already
   Core-bulk; this hardens against a future `session.delete`). Low risk.
2. **Verify + guard `DBWorkflowRun.nodes`**: confirm every run-with-nodes mapping
   path eager-loads `nodes`; add a regression test asserting a lazy read raises;
   then add `lazy="raise_on_sql"`. The only (c)-class migration.
3. **Guard the remaining never-read (d) relationships** (the bulk). Free — no
   reader to break; catches future accidental lazy reads. Apply `raise_on_sql`.
4. **Grep gate**: assert no `relationship(` lacks an explicit `lazy=` outside a
   short allowlist (record any deliberate exclusion with its reason).

## Test plan (for the flip, not this audit)

- Full suite incl. slow with the global guard on (the primary net; the scoped
  v0.8.x flip was proved this way).
- Per-repo completeness regression: fetch via each production loader, assert no
  silently-empty mapped collection.
- A representative "forgot the eager-load" query raises
  `sqlalchemy.exc.InvalidRequestError` — proves the guard fires.
- Cascade-delete still succeeds for every delete-orphan rel under the guard
  (the existing cross-repo CRUD/cascade suite).

## Open decisions for review

- **Scope**: guard the whole graph (buckets 1–3 above), or only the read+loaded
  set and leave never-read rels lazy? Recommendation: **guard the whole graph** —
  fail-loud-everywhere is the stated architecture invariant, and the never-read
  rels are free to guard.
- **`DBWorkflowRun.nodes`**: guard it (with the verification + test) this cycle,
  or leave it lazy and allowlist it? Recommendation: **guard it** — it's the one
  rel where a silent wrong-data bug is actually reachable.
