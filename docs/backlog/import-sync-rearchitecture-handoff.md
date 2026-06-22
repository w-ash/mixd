# Import/Sync Rearchitecture — Handoff

**For:** the next agent. Job (1) **code-review of Phases 1–4 is DONE** and its
findings are remediated — see **§0a** for what it caught and fixed. Remaining
job: **build the final phases** (5: use cases + REST API + direction model +
operation-awareness backend; 6: frontend rebuild).

**Approved plan (read first):** `~/.claude/plans/the-whole-import-playlist-squishy-melody.md`
— the full design + the locked decisions + the verification section. This doc is
the *delta*: what's actually built, what to scrutinize, and what's left.

---

## 0. Why this work exists (one paragraph)

The Spotify "import playlist" + sync flow was "very broken." Investigation found
three real, verified bugs, all from one root error — **the code sourced "the
external playlist's current state" from the canonical Mixd playlist itself** (a
self-join through the mapping):

1. **Silent track loss on import** — unmatched/local/unavailable tracks were
   dropped, not recorded.
2. **Push sync was a total no-op** — it diffed the canonical against itself → 0
   ops → "already up to date" → never touched Spotify.
3. **The destructive-push safety guard was dead** — same self-diff → always 0
   removals → never fired. (The old unit test mocked the two sides as different,
   hiding the bug.)

The fix is one reconciliation primitive: **fetch the real remote → diff against
a stored per-link base → preview → confirm → atomic apply.**

---

## 0a. Code-review remediation (DONE — applied to the working tree)

A recall-mode review of Phases 1–4 ran 10 finder angles + verification. The
deletion side and the incident-hardened `playlist/core.py` membership matching
held up clean; two scary candidates (an "RLS WITH CHECK bypass" and a
corrupted-row duplication) were **verified as non-issues** from primary sources
(Postgres `rowsecurity.c` falls back to `USING` for INSERT/UPDATE; the CHECK
constraint blocks the corrupt row). What was real and is now **fixed**:

**Correctness**
- **Duplicate-ID gate bypass** — `build_sync_plan` counted removals + the safety
  denominator over `set(...)`, so a duplicate-heavy playlist could hide a
  near-total wipe (`[A, B×11]→[A]` read as 1-of-2, under threshold). Now computed
  over a `collections.Counter` **multiset** (`total_current = len(current_ids)`).
- **Gate vs execution divergence** — the PUSH safety target included *unresolved*
  canonical entries' source ids, which could mask a removal the executor (resolved
  tracks only) performs. The PUSH target is now **resolved-only**
  (`_canonical_connector_ids(..., resolved_only=True)`); PULL keeps the full list
  so a re-pull of an unchanged-but-unresolvable remote stays a no-op.
- Push op-drops report via a dedicated `ReconcileResult.tracks_dropped` /
  `unmatched` instead of overloading `unresolved`; append-mode dedups by canonical
  id **and** connector identity.

**Clean-break removals (no dead code)**
- The whole `playlist_sync_bases` table was found **write-only** today (the diff
  runs against fresh remote + canonical; the base isn't read yet). Kept the table
  + `base_snapshot_id` as the Phase-5 snapshot-fast-skip hook, but removed the
  speculative **`base_items`** payload (entity + DB column + migration `030` +
  repo) and the dead `SyncPlan.{target_item_count, base_snapshot_id,
  remote_snapshot_id}` fields + the `unresolved_count` param.
- **DRY:** one `ConnectorTrackRef.to_metadata` / `from_metadata` pair now owns the
  unresolved-snapshot dict shape (was hand-built in `core.py` + parsed in
  `mapper.py`).
- **Decoupling:** `MetricConfigProvider` moved out of `workflows.protocols` into a
  leaf module `use_cases/_shared/metric_config.py`; all importers repointed, which
  also removed five `TYPE_CHECKING` guards that only existed to dodge the old cycle.

**Gates:** `uv run pytest` → 3160 passed · `basedpyright src/` 0 errors · `ruff`
clean · migration `030` re-verified up/down/up (no `base_items` column).

---

## 1. Status

| Phase | What | State |
|---|---|---|
| 1 | Entry identity (`PlaylistEntry.id`) | ✅ done, green |
| 2 | Data model + migration `030` (sync base + unresolved entries) | ✅ done, migration verified up/down/up |
| 3 | Domain reconciliation + entities + repo persistence of unresolved | ✅ done, green |
| 4 | Reconciliation engine, processing rewrite, push unification, hardening | ✅ done, green |
| 5 | Import use case → engine, REST API, direction model, op-awareness backend, repair-unresolved use case | ✅ **done — all of Phase 5 = v0.8.7** (one version, internal slices): Slice 1 (import→engine + audit + sync-route migration); Slice 2 (confirm-token, repair use case + endpoint, operation-awareness via `/operation-runs?status=running` + `operation_id` (migration 031), PULL-default + `direction_label` vocab); Slice 3 (CLI parity — unified `--source {spotify,mixd}` vocab, import always-pull, sync 409 two-step, `repair` command). Notes: ops-awareness reused the existing `/operation-runs` surface (DRY, not new `/operations/*`); CLI unified on the existing `--source` vocab (not the plan's `--overwrite`, DRY); Orval regen + frontend are **v0.8.8** (Phase 6). |
| 6 | Frontend rebuild (wizard, sync panel, direction indicator, op awareness) | ⬜ **not started** |

**Current state: fully green, code-review remediation applied (§0a).** `uv run pytest`
→ 3160 passed. `uv run basedpyright src/` → clean. `uv run ruff check .` → clean.
Migration `030` verified on real Postgres.

> ✅ **RESOLVED in Phase 5 Slice 1 (v0.8.7).** Import now routes re-imports
> through `engine.apply(PULL)` (first imports keep the CREATE path: fetch +
> `upsert_canonical_playlist` + link, since the engine needs an existing link +
> canonical). The `has_fresh_cache` short-circuit is deleted — bug #2 is no longer
> live for import. Import + sync both return an `OperationResult` so the SSE audit
> row records real status/counts, and per-playlist failures land via
> `append_run_issue`. The bespoke sync route was migrated onto `launch_sse_operation`
> (audit row + run_id + 429 cap) with a synchronous 409 pre-flight for unconfirmed
> destructive syncs. Unit-green (2440 passed); integration tests (sync route 409 +
> audit row, engine round-trip, preservation-bugs-v2) need Docker to run.

---

## 2. What landed (file inventory)

### New files
- `src/domain/entities/playlist_sync_base.py` — `PlaylistSyncBase` entity.
- `src/domain/playlist/reconciliation.py` — `SyncPlan` + `build_sync_plan`
  (**connector-identifier-based**, not UUID — see §4).
- `src/application/services/connector_push.py` — **the single connector-push
  module**: `external_as_playlist`, `execute_connector_operations` (fail-loud),
  `overwrite_external_playlist`, `push_tracklist_to_connector`, `PushResult`.
- `src/application/services/playlist_reconciliation_engine.py` — the engine
  (`preview` + `apply` for pull/push).
- `src/infrastructure/persistence/repositories/playlist/sync_base.py` — sync-base repo.
- `alembic/versions/030_sync_base_unresolved.py` — migration.
- Tests: `tests/unit/domain/test_reconciliation.py`,
  `test_playlist_unresolved.py`; `tests/unit/application/services/
  test_playlist_reconciliation_engine.py`, `test_connector_push.py`,
  `test_connector_playlist_processing_service.py`;
  `tests/integration/repositories/test_playlist_sync_base_repository.py`,
  `test_unresolved_playlist_entries.py`;
  `tests/integration/services/test_playlist_reconciliation_engine_integration.py`.

### Changed files
- `src/domain/entities/playlist.py` — `PlaylistEntry.id` (`eq=False`),
  `PlaylistEntry.track` now `Track | None`, `ConnectorTrackRef`, `is_resolved`,
  `display_title`; `Playlist.tracks`/`to_tracklist` filter to resolved;
  `resolved_entries`/`unresolved_entries`/`unresolved_count`.
- `src/domain/entities/__init__.py` — export `ConnectorTrackRef`, `PlaylistSyncBase`.
- `src/domain/exceptions.py` — (no net change; the declared-but-unused
  `SyncDivergenceError` was removed in remediation — see §6).
- `src/infrastructure/persistence/database/db_models.py` — `DBPlaylistSyncBase`;
  `playlist_tracks` nullable `track_id` + `connector_track_id` + `unresolved_metadata`
  + CHECK `ck_playlist_tracks_resolved_or_source` + partial index.
- `src/infrastructure/persistence/repositories/playlist/{core,mapper}.py` —
  persist + read unresolved entries; entry-id-first membership matching.
- `src/application/services/connector_playlist_processing_service.py` — emits an
  entry for **every** source position (unresolved instead of dropped).
- `src/application/use_cases/sync_playlist_link.py` — thin status-lifecycle
  wrapper over the engine.
- `src/application/use_cases/preview_playlist_sync.py` — delegates to `engine.preview`.
- `src/application/use_cases/update_connector_playlist.py` — **900 → ~95 lines**:
  thin shell over `push_tracklist_to_connector` (kept for the workflow destination's
  DI + Command/Result contract).
- `src/domain/repositories/{playlist,uow}.py` + `src/infrastructure/persistence/
  unit_of_work.py` — `PlaylistSyncBaseRepositoryProtocol` + UoW accessor.
- `tests/fixtures/{mocks,__init__}.py` — `make_mock_sync_base_repo`.

### Deleted
- The ceremony inside `update_connector_playlist.py` (the F9 ~250–350 lines:
  verification GETs, four-method persist chain, dead Command knobs).
- **Dead code orphaned by the slim (zero production callers):**
  `_shared/metadata_builder.py` (incl. `PlaylistMetadataBuilder`, the over-
  engineered builder design-debt §F9/#4 flagged) + its test;
  `_shared/playlist_validator.py` (`classify_connector_api_error`,
  `classify_db_error_for_logging`, `is_auth_error_message`, `is_rate_limit_error`
  — only the deleted ceremony used them) + its test; `AppendOperationResult` and
  `ApiMetadata` from `_shared/playlist_results.py`; the corresponding
  `_shared/__init__.py` exports.
- Obsolete tests that encoded the bugs (mocked external≠canonical): the old
  `test_sync_playlist_link.py`, `test_preview_playlist_sync.py`,
  `test_update_connector_playlist.py` — replaced with engine/connector_push tests.

---

## 3. Architecture as built

```
import / pull / push / preview
        │
        ▼
PlaylistReconciliationEngine            (application/services)
  preview(link, dir) → SyncPlan         read-only, connector-id diff, no ingest
  apply(link, dir, confirmed)           fetch → plan → safety gate → apply → record base
        │                         │
        │ pull                    │ push
        ▼                         ▼
upsert_canonical_playlist    connector_push.overwrite_external_playlist
  (ingest + save canonical,    (diff external vs canonical → ops →
   unresolved preserved)        execute_connector_operations [fail-loud])
        │                         │
        └─────── record base (playlist_sync_bases) ───────┘

workflow destination ─► UpdateConnectorPlaylistUseCase (thin) ─► connector_push.push_tracklist_to_connector
```

**One push implementation** (`connector_push`), shared by the engine (link sync)
and the workflow destination. The workflow destination's identical self-diff bug
is fixed for free by this routing.

---

## 4. Invariants the reviewer/next agent MUST preserve

1. **Plan/safety/no-op are computed at the connector-identifier level**, not
   canonical UUID. This is THE fix for the pull-import no-op: a fresh pull's
   tracks have no canonical UUID yet, so a UUID diff counts zero adds and skips.
   `is_noop` is an **ordered** id-list comparison so reorders aren't false no-ops.
   (Execution still uses the UUID diff for push, and upsert for pull.) Counts +
   the safety denominator are **multiset** (`Counter`) so duplicate tracks can't
   dilute the destructive-removal ratio; the **PUSH target is resolved-only**
   (only resolved tracks can be pushed), the PULL current keeps unresolved ids.
2. **"Always complete" is structural:** every source position becomes a
   `playlist_tracks` row; the CHECK is `track_id IS NOT NULL OR unresolved_metadata
   IS NOT NULL` (NOT the connector FK — local/unavailable tracks have no
   connector_tracks row). Never reintroduce a "skip unmatched position" path.
3. **Fail loud:** a partial/failed connector push raises `ConnectorSyncError`
   (via `execute_connector_operations`); the link goes `ERROR`, never silent SYNCED.
4. **Safety against fresh remote:** the destructive guard must always diff against
   freshly-fetched remote state. Never resurrect `get_playlist_by_connector` as
   "the external state" (that returns the canonical — the original bug). The
   gate-vs-execution divergence (gate counting on connector ids, execution on
   resolved tracks) is closed by the resolved-only PUSH target — keep it that way.
5. **`PlaylistEntry.id` is `eq=False`** — value equality stays track-based so the
   diff fast-path + append dedupe are unaffected. Don't make it part of `eq`.
6. **The incident-pinned test must stay green:**
   `tests/integration/test_playlist_update_preservation_bugs_v2.py` (record-identity
   preservation on reorder/dup/remove).

---

## 5. Code-review checklist (DONE — kept for reference)

The review ran and §0a applied the fixes. Scrutiny items #2 and #3 below were the
ones that surfaced real bugs (now fixed); the rest held up.

**Gates (all pass):**
```bash
uv run pytest                       # 3160 passed (~80s, needs Docker)
uv run basedpyright src/            # 0 errors
uv run ruff check .
# migration up/down/up on real PG:
docker run -d --name mig -e POSTGRES_USER=mixd -e POSTGRES_PASSWORD=mixd -e POSTGRES_DB=mixd -p 5544:5432 postgres:17-alpine
DATABASE_URL=postgresql+psycopg://mixd:mixd@localhost:5544/mixd uv run alembic upgrade head
DATABASE_URL=... uv run alembic downgrade 029_last_sync_unmatched && DATABASE_URL=... uv run alembic upgrade head
docker rm -f mig
```

**Scrutinize (highest-risk, in order):**
1. **`playlist/core.py` membership matching** (`_update_playlist_tracks`,
   `_consume_record_for_entry`, `_build_track_values`) — the incident-hardened
   path, now extended for mixed resolved/unresolved rows + entry-id matching.
   Verify the v2 preservation test still covers it and reason through duplicates.
2. **`connector_push.overwrite_external_playlist`** — ✅ **found a real divergence**:
   the gate counted connector ids (incl. unresolved) while execution acts on
   resolved tracks → a removal could slip. Fixed via the resolved-only PUSH target
   (§0a, §4 invariant #4). Unresolved-on-remote tracks are still intentionally left
   untouched (safe partial overwrite) — confirm that product behavior with the owner.
3. **`_canonical_connector_ids`** (engine) — includes unresolved entries' source
   ids so re-pull of an unchanged-but-unresolvable remote is a no-op. ✅ **Duplicate
   handling fixed**: counts/safety are now multiset (`Counter`); push passes
   `resolved_only=True`.
4. **`alembic check` drift** — at head there are ~28 pre-existing ORM↔migration
   diffs (convention double-prefixes, TIMESTAMP/DateTime, trigram indexes). NONE
   are from this work (verified: no new-object names appear). Don't try to "fix"
   them here; they're a separate cleanup.
5. **`base_items`** — ✅ **removed.** The review found the whole `playlist_sync_bases`
   table is write-only today (the diff runs against fresh remote + canonical, not
   the base). Kept the table + `base_snapshot_id` as the Phase-5 fast-skip hook;
   dropped the speculative `base_items` payload (entity + column + migration + repo).
   `_record_base` now stores the snapshot only.

---

## 6. Known open items / decisions already made

- **`SyncDivergenceError`** — RESOLVED (deleted). It was never raised and the
  divergence-from-stale-base concern isn't live (the base snapshot isn't read for
  planning yet), so the use case's generic `except → ERROR` already covers the
  API-succeeded-then-DB-failed case without a false SYNCED. Re-introduce a typed
  divergence error when Phase 5 wires base-reading and has a concrete consumer.
- **Repair-unresolved-entries use case** is NOT built yet (deferred to Phase 5 —
  it needs an API/CLI surface). The schema supports it (`ix_playlist_tracks_unresolved`
  partial index; `connector_track_id` FK for re-resolution lookup). Coordinate
  with the identity-resolution track + the existing match-review surface.
- **Confirm-token** for the 409 confirm round-trip: not yet designed (Phase 5).
  Current `confirmed: bool` works; a per-plan token prevents confirm-stale-plan races.
- **Direction model unification (Phase 5, §5b of the plan)** — the four divergent
  default directions and three vocabularies are NOT yet unified; the import-action
  vs standing-direction split (import always pulls) is NOT yet implemented in the
  import use case. This is core Phase 5 work.
- **Unresolved count in pull preview** is currently 0 (unknown pre-apply); the
  apply result reports the real count. Improve only if product needs it.

---

## 6b. Additional hardening / test gaps (do during review, before Phase 5)

Current coverage is strong on the engine plan/safety/idempotency (unit) and the
unresolved + sync-base persistence (integration). Known gaps, roughly by value:

1. ✅ **DONE** — Engine PUSH integration round-trip added (`TestPushRoundTrip` in
   `tests/integration/services/...`): real repos + mocked connector
   `execute_playlist_operations`, asserting the base records the post-push
   `outcome.snapshot_id`.
2. ✅ **DONE** — `_append_new_tracks` (incl. connector-identity dedup) and
   `_update_metadata` now have unit tests in `test_connector_push.py`.
3. ✅ **DONE** — `push_tracklist_to_connector` now has a direct unit test for the
   fresh-fetch → diff path (the workflow-destination no-op fix).
4. **Re-resolution of unresolved entries** — schema + `ix_playlist_tracks_unresolved`
   support it, but nothing resolves an unresolved row when a mapping later appears.
   This is both a test gap AND the missing **repair use case** (Phase 5).
5. **Concurrency** — two concurrent syncs on one link (the SYNCING guard / the
   `uq` on the link). Not tested.
6. **Migration downgrade with data present** — `030` downgrade re-tightens
   `track_id` NOT NULL and will fail if unresolved rows exist (documented as a
   clean-break behavior; not tested).
7. **CLI rendering** for `sync`/`import`/`sync-preview` — coverage-omitted; build
   `CliRunner` characterization tests BEFORE any Phase 5 CLI change.
8. ✅ **DONE** — `external_as_playlist` has direct unit tests (resolved/unresolved
   split, empty remote), plus a duplicate-id safety regression in
   `test_reconciliation.py`.
9. **Perf (not correctness): snapshot fast-skip.** The engine always fetches +
   diffs; `is_noop` then skips work. A pre-fetch `base.base_snapshot_id ==`
   cheap-snapshot check could skip the fetch entirely. Only if API cost matters.

## 6c. DRY / dead-code / conciseness opportunities (reviewer judgment calls)

Removed already: see §2 "Deleted" + **§0a** (dead `SyncPlan` fields, `base_items`,
the `MetricConfigProvider` decouple, and the connector-ref serialize/parse
consolidation — item 3 below). The following remain — candidates flagged but NOT
changed (judgment calls / adjacent):

1. **`create_connector_playlist.py`** — its docstrings say "mirroring
   update_connector_playlist," and it still has an `operation_summary` Result
   property in the old style. Now that the push path is slim + shared, review it
   for the same treatment (route its push/create through `connector_push`?).
2. **`upsert_canonical_playlist`** (`playlist_upsert.py`) — a wrapper over
   Create/Update canonical use cases. The plan flagged it as possibly thin-able.
   Decide whether it earns its layer or the engine should call Create/Update
   directly.
3. ✅ **DONE (§0a)** — the snapshot serialize/parse pair is now
   `ConnectorTrackRef.to_metadata` / `from_metadata` (was hand-built in `core.py`
   + parsed in `mapper.py`). The two remaining `ConnectorTrackRef` *constructors*
   (`_item_title` from extras, `processing_service._connector_ref` from a
   ConnectorTrack) build from different sources — left as-is.
4. **`processing_service` bulk-then-individual-retry ingest** — resilience that may
   now be over-engineered given unresolved entries absorb failures. Review whether
   the per-track retry still earns its complexity.
5. **Two snapshot concepts** — `connector_playlists.snapshot_id` (global cache,
   used by the OLD import short-circuit) vs `playlist_sync_bases.base_snapshot_id`
   (per-link). After Phase 5 rewires import to the engine, audit whether the
   global `snapshot_id` is still needed anywhere.
6. **`_shared/playlist_results.py`** now holds just `OperationCounts` +
   `build_playlist_changes`; could fold into `operation_counters.py`. Minor.
7. **Sync gate double-fetch (DEFERRED to v0.8.8).** A web sync fetches the remote
   twice — once in the route's synchronous `_ensure_sync_confirmed` preview (so the
   destructive-sync 409 is reachable before backgrounding) and again in the
   background `engine.apply`. Consolidating to one fetch needs the pre-fetched
   remote threaded from the synchronous gate into the background apply (across two
   UoW sessions: preview use case → engine → route → sync use case). The gating is
   already correct and the residual gate→apply TOCTOU window is negligible, so this
   is an efficiency/altitude cleanup — do it **with the v0.8.8 sync panel**, which
   rebuilds this flow. Until then the engine's own `requires_confirmation` gate is
   the live one for CLI/direct callers; the web route hardcodes `confirmed=True`
   after its synchronous pre-gate passes.

Don't bundle these into Phase 5 feature work blindly — each is a small, separate,
test-backed cleanup. Apply the project's clean-break rule (no compat shims).

---

## 7. Phase 5 — concrete next steps

Build on the engine; do NOT add a second sync/push path.

1. **Import use case → engine.** Rewire `import_connector_playlist_as_canonical.py`
   to call `engine.apply(direction=PULL, ...)` per playlist (keep its sub-op
   progress machinery). This gives it snapshot idempotency + base recording + the
   silent-loss fix automatically. **Return an `OperationResult`** (not the bespoke
   result) so `sse_operations._audit_outcome` records real status + counts +
   per-item failures (`append_run_issue`). This is the durability fix.
2. **Direction model (plan §5b).** Separate the import *action* (always pull) from
   the link's *standing direction*; unify the four defaults; make the per-sync
   override explicitly one-time. One vocabulary everywhere ("which side gets
   overwritten").
3. **REST API.** Preview endpoints (GET, read-only) returning resolved/unresolved
   + diff + safety + confirm token; apply endpoints via `launch_sse_operation`
   (→ `{operation_id, run_id}` + SSE). Migrate `POST .../links/{id}/sync` off its
   bespoke background path onto `launch_sse_operation` (gets the audit row + 429
   cap + reachable 409). Add `POST .../links/{id}/repair`.
4. **Repair-unresolved use case** + its endpoint.
5. **General operation-awareness backend (NET-NEW):** `GET /operations/active` +
   a general snapshot for import/sync (today `/operations/{id}/snapshot` is
   workflow-shaped). The live SSE stream `GET /operations/{id}/progress` is
   already general.
6. **CLI parity:** `import-spotify`/`sync`/`sync-preview`/new `repair` map to the
   same use cases.

**Test-first for any CLI work** (coverage-omitted): characterization tests via
`CliRunner` before changing rendering.

---

## 8. Phase 6 — frontend (after the API exists)

Per plan §6. Generalize the **proven workflow re-attach pattern**
(`web/src/contexts/WorkflowExecutionContext.tsx` + `useWorkflowSSE` +
`useActiveRuns`) to operations (React Context, no Redux/Zustand):
- `OperationsProvider` + sidebar badge + re-attach (poll `/operations/active` →
  adopt → snapshot-seed → live SSE).
- **Preview-first import wizard** (reuse `ConnectorPlaylistPickerDialog` +
  `OperationProgress`); **remove the "Force re-fetch" toggle** (leaky internal —
  the engine always fetches fresh).
- **Per-playlist sync panel** with the **real 409 confirm round-trip** (today
  `PlaylistDetail.tsx` hard-codes `confirmed:true`) + "Repair unresolved (N)".
- **One shared `SyncDirectionIndicator`/`DirectionChooser`** (one vocabulary,
  leads with what-gets-overwritten) used in wizard + link dialog + sync panel.
- Regenerate Orval after the API lands: `pnpm --prefix web sync-api`.
- Tests: Vitest + RTL + MSW; Playwright E2E for import, sync-409, and
  operation-awareness unmount/reload re-attach.

---

## 9. Verification reference

```bash
uv run pytest                                  # full fast suite (Docker req'd)
uv run pytest tests/unit/application/services/test_playlist_reconciliation_engine.py
uv run pytest tests/integration/services/      # engine against real DB
uv run basedpyright src/
uv run ruff check . --fix && uv run ruff format .
# frontend (Phase 6): pnpm --prefix web test && pnpm --prefix web check && build
```
```
