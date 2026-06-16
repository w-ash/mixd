# Design-Debt Review — Findings

**Scope**: the second of two mid-2026 audits. The hygiene pass cleaned lint-visible debt (suppressions, dead symbols, copy-paste). This pass walked the codebase **use case by use case** against documented user goals, hunting over-engineering, accretion drift, misleading names, and disproportionate paths. Review-and-recommend only — no refactors were applied.

**Method**: 30 flow rows walked end-to-end (page/CLI → route → use case → domain → repo/connector) against a fixed fitness rubric, scored against a local calibration bar (the v0.8.2 schedules flow); coupling/naming measured over 320 commits of git history; 8 hotspots deep-dived with **history-before-judgment** archaeology (every draft finding had to survive a refutation attempt before standing). Boundary respected: identity-resolution/matching internals were traversed but not judged — they belong to the parallel track in `identity-resolution-research-handoff.md`; observations are handed over in §8.

**Date**: 2026-06-15. **Calibration bar**: `routes/schedules.py` (5–10-line handlers) → `use_cases/schedules.py` (frozen Command/Result, owns UoW) → `domain/entities/schedule.py` (pure) → `repositories/schedule/repository.py`. ~6 hops, each adding a real decision. "Good" locally = this.

---

## 0. The one-sentence version

**The architecture is sound; the failure paths lie.** The layering direction holds (no outward/sideways coupling found), the Command/Result pattern is not the cost driver, the workflow engine's ceremony is incident-backed, and `base_repo`'s core is justified. The real debt clusters in two places: **(1) a single dropped-result seam that turns five different half-failures into reported successes on the Weekly Curator's core flows** (§3, Tier 1), and **(2) verification ceremony with no incident behind it** on the hottest write path (§5, the handoff's lead #1 — confirmed by the cleanest archaeology of the campaign). Everything else is smaller.

A secondary theme: **the spec doc has decayed faster than the code** (§7). Several "shipped" flows don't exist; several "needs implementation" flows shipped long ago. The doc is now actively misleading and is itself a finding.

---

## 1. Flow-walk matrix (deliverable #1)

Score legend: **fit** = at the schedules bar · **drift** = minor naming/doc/DRY issues, no structural problem · **HOT** = structural finding or verified failure. Cross-cutting sections (CC-*) scored as their own rows. Flow 6.5 (LLM, v0.9.0 sketch) excluded — not built.

| Flow | Score | Hops | Headline |
|---|---|---|---|
| 1.1 Connect Spotify | drift | 10 | Clean OAuth; but server-reachable browser-auth hang + dead v0.3 doc |
| 1.2 Connect Last.fm | **HOT** | 9 | Web-connect username never reaches import path → broken or cross-tenant |
| 2.1 Library | drift | 7 | At bar; sort-key table duplicated app↔repo |
| 2.2 Track Detail | fit→drift | 6 | Cleanest read flow; doc promises Metrics/sparkline that don't exist |
| 2.3 Fix Mapping | **HOT** | 8 | Documented connector-search journey unimplementable; PATCH semantics differ (→identity) |
| 3.1 Playlist List | **fit** | 5 | Reference-quality |
| 3.2 Playlist Detail | drift | 7 | Tracks-error renders as "empty playlist"; double entry-load |
| 3.3 Create Playlist | drift | 6 | Interface imports infrastructure directly |
| 3.4 Add Tracks | **HOT** | 0 | No endpoint, no UI, no CLI — doc says "Exists" |
| 3.5 Reorder | **HOT** | 0 | Same; entry identity severed at domain boundary |
| 3.6 Remove Tracks | **HOT** | 0 | Same |
| 4.1 Import Center | drift | 6 | Page well-factored; doc describes a v0.3 IA that was rebuilt |
| 4.2 Last.fm Import (SSE) | **HOT** | 9 | **Verified silent phase-2 failure + permanent data loss** |
| 4.3 Spotify GDPR Upload | **HOT** | 9 | Shares 4.2's bug; error messages lost at the SSE seam |
| 4.4 Export Likes | drift→HOT | 7 | Partial failure invisible; failed loves dropped forever |
| 4.5 Checkpoint Visibility | drift | 6 | Backend complete; staleness thresholds entirely unbuilt |
| CC-1 Cancellation | drift | 0 | Correctly marked "needs implementation"; narrative oversells it |
| CC-2 Operation Awareness | **HOT** | 0 | All 3 claimed global-visibility mechanisms absent; imports unobservable after unmount |
| CC-3 Error Envelope | drift | 3 | Well-built; HTTPException paths bypass it (429/413 → "unknown error") |
| CC-4 Result Summaries | **HOT** | 0 | Backend computes summaries no web user sees; ~50 lines dead frontend selection |
| 5.1 View Links | **fit** | 5 | At bar |
| 5.2 Link External | drift | 7 | Backend at bar; doc mis-attributes use case; raw-text-input UI vs existing picker |
| 5.3 Sync Direction | **fit** | 5 | At bar |
| 5.4 Manual Push/Pull | **HOT** | 20 | API failure swallowed → link marked SYNCED; ~250–350 lines no-op ceremony |
| 6.1 Workflow List | drift | 6 | Backend at bar; "Delete" action listed, no DELETE route |
| 6.2 Run Workflow | drift→HOT | 12 | Mostly earns its hops; **enrichment outage → COMPLETED run, 0-track playlist** |
| 6.3 Run History | **fit** | 6 | At bar |
| 6.4 Visual Editor | **HOT** | 9 | **Run button is a silent no-op**; Preview ignores unsaved edits |
| 7.1 Dashboard | drift | 7 | Stats chain at bar; doc status inverted; freshness alerts unbuilt |
| 7.2 Unmatched Tracks | drift | 0 | Entirely unbuilt; doc cites a v0.7.0 use case that was rescheduled to v1.0.x |

**Tally**: 4 fit · 15 drift · 11 HOT. The four `fit` rows (3.1, 5.1, 5.3, 6.3) confirm the schedules bar is reproducible — newer code reaches it consistently. The HOT rows cluster into the two themes above plus the phantom-flow cluster (§4).

---

## 2. Fact corrections (recorded, not fixed)

The handoff and `CLAUDE.md` carry several stale facts; surfacing them so they don't propagate:

| Claim | Reality |
|---|---|
| `docs/user-flows.md` (CLAUDE.md, handoff) | Does not exist. Canonical flows doc is **`docs/web-ui/01-user-flows.md`**. |
| `US-AREA-N` flow IDs (`backlog-format.md`) | No such IDs exist. Flows are numbered sections **1.1–7.2**. |
| "69 use cases" | **56 use-case modules** (12,095 LOC). The count reconciles at the *class* level (`sync_likes.py` holds 3, `schedules.py` 5) — neither number is wrong, but the module/class distinction matters when sizing work. |
| "80% coverage gate" | No `fail_under` in pyproject. **3,117 fast tests**; coverage **omits** `src/interface/cli/*` and `apple_music/*` — any CLI-touching refactor must build its characterization net first. |
| Lead #7: "workflow delete in CLI, API endpoint zero consumers" | Resolved differently than implied: the hygiene pass (`1191128e`) **removed** `DELETE /workflows/{id}`; CLI delete remains as `DeleteWorkflowUseCase`'s **sole** consumer. Drift codified, now CLI-only. |

The `.claude/skills/api-contracts/SKILL.md` skill, despite being "recently trued", omits ~30 live endpoints across 7 mounted routers (`/tags`, `/schedules`, `/settings`, `/reviews`, `/playlist-assignments`, `/operation-runs`, `/webhooks`). Re-truing it is an XS task.

---

## 3. Tier 1 — Silent failures on the Curator's core flows (the headline)

These are not "ugly code" — they are the system **reporting success while losing the user's data or work**, on the primary persona's Sunday ritual. They share one structural root, stated after the findings.

### F1 — Web history imports report SUCCESS while phase-2 resolution silently never runs, with permanent data loss
**Severity: major · Confidence: high (live-reproduced) · Effort: M · Flows 4.2, 4.3**

Verified by executing the real classes: phase-1 start/complete succeeds, phase-2 `start_operation` raises `"Operation web-op-123 is already being tracked"`. Four-link chain:
- `interface/api/services/progress.py:54` — `OperationBoundEmitter` rebinds *every* `start_operation` to the request's operation_id;
- `domain/services/progress_coordinator.py:192` — completed ops are never evicted, so the second start collides;
- `progress.py:261-271` — the subscriber treats the first completion as stream-terminal (sentinel);
- `application/use_cases/import_play_history.py:183` — the use case converts the exception to a returned "failed result" the SSE seam never reads.

**Worse than a missed run**: phase-1 advances sync checkpoints and commits rows *before* phase-2 (`base_play_importer.py:243-264`), so the stranded `connector_plays` (with `resolved_track_id IS NULL`) are permanently skipped by the next incremental import. This is silent data loss. CLI and scheduler are immune — `ProgressOperation` defaults to a fresh `uuid4` per phase (`domain/entities/progress.py:129`). **Web-only.**

**Archaeology**: the emitter's one-operation-per-request assumption was *true* at birth (`da2a5545`, v0.3.1 — single-phase import over HTTP). The v0.6.x two-phase growth invalidated it silently. This is a contract collision, not a slip — which is why a one-line patch isn't the right fix (see §5 design-space, progress lifecycle).

### F2 — Connector playlist sync swallows API failures → link marked SYNCED, "Sync complete" printed
**Severity: major · Confidence: high · Effort: S (fix) / M (with collapse) · Flow 5.4**

`update_connector_playlist.py:652` catches every exception and returns `{success: False, error: str(e)}`; `:620-627` then discards `error`/`partial_success` and zeroes the counts; `UpdateConnectorPlaylistResult.errors` has been dead since the file was born (`4270b17b`, no constructor ever passes it). So `operation_summary['success']` is always `True`. Downstream, `sync_playlist_link.py:119-127` marks the link `SYNCED`, the route emits SSE `COMPLETED`, the CLI prints `Sync complete — added: 0, removed: 0`, and the workflow destination node logs success. **v0.8.4's own theme was "proactive failure surfaces" (`SyncStatus.ERROR` badges)** — which a Spotify API error can never trigger. The same commit added `sync_targets.py:52-64`, whose docstring states the exact lesson this path violates.

### F3 — Workflow enrichment total failure → COMPLETED run + playlist overwritten to 0 tracks
**Severity: major · Confidence: high · Effort: S · Flow 6.2**

`enrich_tracks.py:182-193` catches all exceptions and returns a *success-shaped* `Result(errors=[...], metrics_added={})`; `nodes/factories.py:333-337` logs `"enrichment failed completely — downstream will drop all tracks"` and returns a normal `NodeResult`; the executor's degraded path (`executor.py:401-423`) only fires when the enricher **raises**. Net: a Last.fm outage produces a COMPLETED run whose filter/sort stages drop everything, and the destination node overwrites the Curator's playlist with 0 tracks. **Archaeology**: the swallow (`8697b535`, 2026-03-06, Spotify-ID-churn era) predates the degrade path (`d3a5f782`, 3 days later) — the degrade machinery's headline scenario was shadowed from birth.

### F4 — Partial likes export invisible; failed loves permanently dropped from future incremental exports
**Severity: major · Confidence: high · Effort: S · Flow 4.4**

`sync_likes.py:584` counts errors into `summary_metrics` but `tracked_operation` completes `COMPLETED` unconditionally → SSE `complete` → success toast. The export checkpoint then advances past the failed items, so they never retry. `append_run_issue` exists for exactly this (`operation_run_recorder.py:85`) and is never called.

### F5 — Spotify likes import: failed bulk-find → "all tracks new" → silent batch-wide re-ingestion
**Severity: major · Confidence: high · Effort: S · Flow 4.4 (import side)**

`sync_likes.py:258-264` swallows a `find_tracks_by_connectors` failure and sets `existing_map = {}`, treating the whole batch as new. **Archaeology**: arrived in `89dd31d8` ("reduced error handling verbosity") — a refactor that changed the prior per-track `except → skip` to a batch-wide treat-as-new. Refactor artifact, not scar tissue.

### The shared root: a dropped-result seam
F1–F5 and CC-4 are one design knot. `interface/api/services/sse_operations.py:123` does `await coro` and **discards the returned `OperationResult`** — nothing maps `summary_metrics` into the terminal SSE event or the `OperationRun` audit counts. Because the seam can't see failure-in-the-result, use cases defensively convert exceptions into returned results (F1's `import_play_history.py:183`) — which the seam *also* can't see. Meanwhile the **CLI renders the same `OperationResult` richly** (`cli/ui.py:87`), so every one of these flows is correct on the CLI and lying on the web. CC-4's frontend half is the mirror image: `web/src/lib/toasts.ts:107` has ~50 lines of count-selection machinery keyed on `scrobbles_imported`/`likes_imported`/… that **have zero backend producers**.

**Fix once, fix all**: make `run_sse_operation` capture `result = await coro`, map `OperationResult.summary_metrics` into both the terminal SSE event and `finalize_run`'s counts/issues, and stop the use cases swallowing exceptions into results. This is the single highest-leverage change in the report — it makes six surfaces report the real result. Effort **M**; it is also the dependency for F1/F4's per-flow fixes.

---

## 4. Tier 2 — Phantom flows (spec says shipped, nothing exists)

### F6 — Playlist track editing (3.4 add / 3.5 reorder / 3.6 remove): zero surface
**Severity: moderate · Confidence: high · Effort: see design-space · Flows 3.4–3.6**

No endpoint in `routes/playlists.py`, no UI affordance (`PlaylistDetail.tsx:887,901` renders neither an Add button nor a remove control), no CLI command — while the flows doc marks the endpoints **"Exists"**. The mechanical blocker is a **severed entry-identity chain**: `DBPlaylistTrack` carries a stable membership `id` + lexicographic `sort_key` purpose-built for identity-preserving reorder (incident `e1045cab`, a documented v1.0-blocker), but domain `PlaylistEntry` (`domain/entities/playlist.py:40`) never gained an `id`, and neither does `PlaylistEntrySchema`. Spec payloads are `entry_ids`; there is nothing to address them by above the DB.

**Archaeology reframed the severity** as descoped-by-silence rather than blocking debt: the spec was written 2026-03-01, the page shipped read-only the next day, and five minor series passed with no backlog story. The product call (document-the-descope vs build) was surfaced to the owner and **decided 2026-06-15: build it.** The structural blocker is the entry-id severance, so the work is entry-identity-threading first, then the three flows — filed as a committed feature story ("Playlist track add / remove / reorder via entry identity", L) in `unscheduled.md`, with the full mechanical decomposition and the incident-hardened repository path it must not disturb. Not yet scheduled to a version.

### F7 — Connector-side search (2.3) never built
**Severity: moderate · Confidence: high · Effort: documentation / identity-track · Flow 2.3**

The documented "find the correct Spotify track" journey (`docs:237-259`) has no implementation path — `connectors.py` exposes only list/token-delete/playlists/import. What shipped is `RelinkConnectorTrackUseCase`, which repoints a mapping at a track **already in mixd's library**. Identity-adjacent → §8 handoff. The doc-drift half (PATCH `/tracks/{id}/mappings/{mapping_id}` is marked "Needs implementation" but shipped `2026-03-10` with a *different* contract) is fixable now.

### F8 — Persistent Operation Awareness (CC-2): three claimed mechanisms, zero exist
**Severity: moderate · Confidence: high · Effort: M · CC-2**

The doc states "Active operations are visible globally" via a sidebar badge, a persistent toast, and a tab-title indicator. None exist (`Sidebar.tsx` has no badge; titles are static). Worse, `operationId` lives in per-card `useState` (`Sync.tsx:216`) — unmounting the page loses it permanently, and there is no re-attach path for imports (the snapshot endpoint is workflow-only). A running import becomes **unobservable** while still consuming one of the global 3 concurrency slots.

---

## 5. Tier 3 — Ceremony without a scar (the handoff's central hypothesis)

### F9 — `update_connector_playlist.py`: persistence-verification ceremony, no incident behind it
**Severity: major (composite) · Confidence: high · Effort: M · Handoff lead #1, flow 5.4**

The cleanest archaeology of the campaign. Commit **`e6a6124c` (2025-09-18, "consolidate progress system and enhance Spotify operations")** literally *replaced* the comments `"Executes playlist operations via connector, trusting its implementation"` and `"Trust connector's sophisticated implementation"` with:
- pre/post `get_playlist_details` GETs (**2 wasted Spotify API calls per sync**, `:150`, `:200`), the post-execution result explicitly discarded (`:213` "Don't fail the operation for this");
- log-only post-upsert read-back (`:350-359`: record-not-found → `logger.error` → continue);
- a four-method persist chain for one save (`_update_connector_playlist_optimistic` → `_persist_optimistic_update` → `_persist_connector_playlist_with_verification` → `_upsert_and_verify_connector_playlist`), three of them carrying the docstring "Holds the … body so the caller's protective try clause stays narrow";
- a `state_consistency_check` metadata blob nobody reads;
- dead `_get_existing_connector_playlist` "ID continuity" logic — `upsert_model` resolves by natural key and ignores the entity id.

**Refutation failed on every front**: no incident (the commit's own scratchpad is entirely about progress-bar consolidation), no backlog story, no test depends on any of it (the 1,047-line use case has **zero direct tests**). This is belt-and-braces that an over-eager refactor added, not scar tissue. ~250–350 lines are removable no-op ceremony.

Riding alongside: **dead Command knobs** (`preserve_timestamps`, `batch_size` — with a live validator — `max_api_calls`, `metadata`), write-only for ~9 months since their last reads were deleted in `67992b68`; and **`PlaylistMetadataBuilder`** (handoff lead #4) — 92 lines, one production entry point, a zero-caller `.build()` carrying the file's lone pyright-ignore. The builder is the over-engineering the lead suspected; replace it with a plain function returning a dict literal.

**Justified within this file (do not touch)**: the raise-on-DB-failure-after-API-success path (`:780-797`, a real divergence state), `check_sync_safety` + `ConfirmationRequiredError` (v0.5.9 destructive-push protection, working web UX), and the three-transaction status shape (status visible mid-run; ERROR write survives the rolled-back sync).

**Design-space** (recommended path = fail-loud fix, then ceremony collapse):
1. **(behavior, ship first)** Make the connector API failure *raise* instead of returning `success:False`; let `SyncPlaylistLinkUseCase` route it to the `ERROR` branch it already implements (`test_sync_playlist_link.py:151` already proves raise→`SyncStatus.ERROR`). Delete the now-dead `partial_success`/`error` keys and the always-`True` success ternary. *Net: every surface reports the real result.*
2. **(behavior, separable)** Fix pull-sync counts to use `calculate_playlist_diff` like push/preview (`sync_playlist_link.py:238-241` currently reports 0/0 for a 5-for-5 replacement).
3. **(refactor)** Collapse the four-method persist chain to one; delete the two `get_playlist_details` GETs and the log-only read-back; slim `enhanced_metadata`; replace `PlaylistMetadataBuilder` with a function; prune the dead Command knobs.
4. **(interface, separable)** Migrate `POST …/links/{link_id}/sync` to the standard `launch_sse_operation` lifecycle (it currently uses a bespoke background path → no `OperationRun` audit row, no 429 cap, and renders `ConfirmationRequiredError`'s 409 handler at `middleware.py:58` unreachable).

**Verification net**: `test_sync_playlist_link.py` (8 tests, pins raise→ERROR and the safety-guard confirm path) + `test_destination.py`. **Build first** (the use case is untested and CLI is coverage-omitted): a `test_update_connector_playlist.py` characterizing current success/failure rendering, and CLI sync rendering tests.

### F10 — Progress quartet: naming + per-tick ceremony
**Severity: moderate · Confidence: high · Effort: M (folds into F1's fix) · Handoff lead #3**

The naming audit's sharpest finding. `ProgressCoordinator` **coordinates nothing** — it is a per-operation ledger (lifecycle + monotonicity + ETA). `AsyncProgressManager` is a **pub/sub broker**. `RichProgressProvider` **provides nothing** — it implements the `ProgressSubscriber` protocol and is registered via `manager.subscribe`; it is a render-sink named Provider. The three authority nouns (Coordinator/Manager/Provider) are interchangeable and hide the real division. The newest member, `SSEProgressSubscriber`, is the only one whose name matches what it does — it is the rename template. (`.claude/rules/api-layer-patterns.md:32` and the api-contracts skill both reference a nonexistent `SSEProgressProvider` — the confusion has already leaked into the rules.)

Mechanics: a tick crosses 9 hops, two of them (`OperationBoundEmitter.emit_progress`, the manager forward) adding zero decisions; the coordinator takes its lock twice per tick (`:95` validate, `:135` record) with an `await` gap between — a TOCTOU window for one monotonicity decision. And `progress_manager.py:141` **re-raises a validation `ValueError` into the emitting use case** — progress *reporting* can kill the operation it observes, while subscriber failures are carefully isolated.

This is best fixed *with* F1 (the lifecycle-ownership consolidation): one broker owns start/complete + eviction in the application layer, the coordinator's pure parts (monotonicity, ETA) become free functions, and the rename + docs-truing ride along. **Justified, do not strip**: the `gather(return_exceptions=True)` + `task.uncancel()` broadcast ceremony, `ThrottledSubOperationEmitter`'s tail-flush, and the snapshot-endpoint recovery path all carry documented incident reasoning.

---

## 6. Tier 4/5 — Structural coupling & the generic base

### F11 — Two god-files convert legal coupling into a per-feature edit tax
**Severity: moderate · Confidence: high · Effort: L (interfaces) / L (db_models) · Measurement-derived**

`src/domain/repositories/interfaces.py` — **2,062 lines, 24 protocols, 73 commits**, whole-history co-change with `db_models.py` (37/73), `tests/fixtures/mocks.py` (29/73), `unit_of_work.py` (28/73). Every persistence-adjacent feature edits it (v0.8.2 alone added +145 lines). The dependency *direction* is exactly the declared architecture (protocols in domain, impls in infra) — each pair is "expected" — but concentrating all protocols in one file means shotgun surgery is **structural, not incidental**. `src/infrastructure/persistence/database/db_models.py` (1,404 lines, 28 ORM models, 66 commits) is the same shape.

The `interfaces.py` split is the rare high-value/low-risk move: protocols are typing-only (zero `@runtime_checkable`), so `basedpyright src/` is near-complete verification; it also fixes a **house-rule violation** — `domain/repositories/__init__.py` re-exports 13 of 34 names, giving two import paths for the same protocol (`from src.domain.repositories import X` vs `…interfaces import X`), against the one-import-path rule. One-time cost ~123 import edits across ~97 files, but 83 are the identical sed-able `UnitOfWorkProtocol` line. **Recommended order**: split `interfaces.py` by aggregate now (before the v0.10.x artists epic re-inflates it), into `domain/repositories/{track,playlist,play,connector,workflow,checkpoint,schedule,…}.py` with **no re-exporting `__init__`** (clean break, one path per thing, grep-gated per aggregate); defer `db_models.py` to the start of v0.10.x, gated behind a mapper-registry-completeness test and an empty-`alembic` autogenerate check (real runtime risk: PEP 649 cross-module relationship resolution). `mocks.py` co-change is **inherent** to the mock-factory pattern (refuted as a god-file).

### F12 — Workflow spine always-touched: mostly inherent
**Severity: minor · Confidence: high · Effort: M (optional) · Measurement-derived**

`use_cases/workflow_runs.py` + `routes/workflows.py` appear in all 5 recent feature diffs. Refuted as primarily structural: the five features *were* the v0.8.x workflow epic (run reliability, engine swap, scheduling ×3), so any decomposition would still be touched. The one structural sliver: `routes/workflows.py` (572 lines) hosts 4 REST sub-resources (CRUD+templates+nodes / runs+previews / versions / schedules) vs the one-resource-per-module norm (`schedules.py` is 109 lines). A FastAPI-router split is mechanical but **modest value** — hunk analysis shows the features never collided *within* the file. Fold into the next workflow-touching PR; not worth a standalone effort.

### base_repo.py — split verdict (handoff lead #5): core justified, periphery speculative
**Severity: moderate · Confidence: high · Effort: M · Handoff lead #5**

The draft's over-engineering suspicion splits down the middle. **Justified (refuted)**: `BaseRepository` has 17 real subclasses; `SimpleMapperFactory` was *extracted from verified duplication* (`687fd1b5` deletes a 45-line hand-written `TrackLikeMapper`) and has 10 consumers including the calibration-good schedules repo; the identity-map relationship loading is incident-grade perf machinery (80×–800× query reduction) with dedicated tests; the savepoint/dedup ceremony guards real PostgreSQL semantics (`CardinalityViolation`). This is not a one-consumer abstraction.

**Speculative periphery (stands)**: ~130 lines of caller-dead surface (`delete()`, `order_by()` helper, the `select(*columns)` overload — all 0 callers, carrying 6 of the file's 15 pyright-ignores); dual-typed `conditions` the codebase grew out of (`find_by` is 19/19 `ColumnElement`-form, the `Mapping` branch has zero src callers since file birth); the DUP-03 conditions-loop (4 near-identical sites). Recommended: delete dead surface, narrow `conditions` to the majority type, extract the residual loop — removing ~130 lines and 10–11 of 15 ignores with near-zero caller churn. The **remaining ~4 ignores are irreducible SQLAlchemy string-reflection** → rule-change proposal §9.1. Two latent correctness notes handed forward (not part of the cleanup): single-row `upsert` is SELECT-then-INSERT (race-unsafe vs the multi-user/thundering-herd principle), and `execute_select_one`'s string-matched "concurrent operations" sleep-retry is scar tissue from the dead shared-session era.

### Workflow engine altitude (handoff lead #10) — the suspicion inverts
**Severity: n/a (justified) + 2 seam findings · Handoff lead #10**

Under archaeology the over-engineering suspicion **inverts**. The architecture is *pulled by real definitions* (level-parallelism fires in `common_ground.json`, `discovery_mix_composition.json`, and a width-10 dev def); the ceremony is incident-/contract-backed (SIGTERM module-global fixes a documented sibling-run-clobber bug; shielded connector cleanup traces to the v0.8.0 audit on leaked httpx pools; the TaskGroup totality contract prevents sibling cancellation); and `executor.py`'s "born-large" 855 lines are a **1:1 port of the 758-line `prefect.py` under a golden-snapshot test** (post-birth delta +30/−8 — not accreting). The genuine debt is two *seams*, not the engine: F3 (enrichment failure semantics) and **validator duplication** — `validate_workflow_def` (raise-first, guards save) and `validate_workflow_def_detailed` (accumulating, behind the editor) have **diverged in both directions** (duplicate-task-id check only in the blocking one; cycle check only in the detailed one), so a cyclic workflow can be *saved* and a duplicate-id workflow passes the editor's validate. Fix by extracting one `_collect_validation_items` the two thin entry points share. Effort **S**.

---

## 7. Doc-drift cluster — the flows doc has decayed badly

`docs/web-ui/01-user-flows.md` is the stated primary specification, and its Status columns are now systematically unreliable — a finding in its own right because it actively misleads readers (and agents):

- **"Exists" labels endpoints that don't exist**: 3.4/3.5/3.6 track mutations, 2.2's `POST /playlists/{id}/tracks`, 7.2's `POST /tracks/rematch`.
- **"Exists" mis-attributes use cases**: 5.2 says `CreateConnectorPlaylistUseCase`, actual is `CreatePlaylistLinkUseCase`; 5.4 says `UpdateConnectorPlaylistUseCase`, actual is `SyncPlaylistLinkUseCase`.
- **"Needs implementation" labels long-shipped endpoints**: 1.1/1.2 OAuth (shipped v0.5.x), 5.1 links query (v0.4.4), 7.1 dashboard stats (shipped as `GetDashboardStatsUseCase`).
- **Whole IA sections describe rebuilt designs**: 4.1's single Import Center with date pickers / cancel buttons / checkpoint tables → rebuilt as `/settings/sync` + `/settings/imports`; 5.1–5.3's three-state "Mixd Master/Connector Master/Manual" model predates a two-value `SyncDirection`.
- **Promised features with no code**: 2.2 Metrics section + sparkline; 4.5 staleness thresholds (green/yellow/red); CC-1 cancellation narrative (correctly marked "needs implementation" in its status row but oversold in prose).

The Status token appears to have once meant "the use case exists, endpoint needs wiring" but now reads as "shipped". **Recommendation**: a doc-truing pass is an **S** task with outsized value — it's the document every feature "starts from". Plus the two always-loaded references to fix: `CLAUDE.md`'s `docs/user-flows.md` link and `backlog-format.md`'s `US-AREA-N` convention.

---

## 8. Identity-resolution handoffs (boundary respected — observations only, no verdicts)

Traversed while walking flows; handed to the identity-resolution track:

- **Lead #6 (dual `MatchingConfig` thresholds)** — the three-zone (`auto_accept`/`review`) and "legacy per-method" systems coexist (`settings.py:303-400`). Confirmed both fields are read; whether the legacy floor is still load-bearing is an identity-track call.
- **`existing_map = {}` re-ingestion path** (F5) funnels a failed-find batch into `ingest_external_tracks_bulk` — whether `ON CONFLICT DO UPDATE`'s conflict key prevents duplicate canonical tracks / mapping churn on a *transient* find failure is an identity-semantics question.
- **`*_stale_id` match-method values** (`TrackDetail.tsx:142-149`) encode provenance ("originally matched by X, ID since changed") by *suffixing the match_method string* rather than as a separate field — an identity-model shape question.
- **Orphan-track lifecycle** — `UnlinkConnectorTrackUseCase` surfaces `orphan_track_id`; post-unlink policy is identity-resolution's.
- **`MatchAndIdentifyTracksUseCase` has no interface exposure** despite the doc documenting `POST /tracks/rematch` against it; only callers are workflow enrichment and config wiring.
- **Cross-connector resolution wiring** — `lastfm/factory.py:33-47` instantiates `SpotifyConnector` + `SpotifyCrossDiscoveryProvider` unconditionally, so a pure Last.fm web import can transitively trigger Spotify token acquisition (a second route into F-auth's browser-auth hang until that fix lands).
- **Repository-layer identity precedence** — `track/core.py:385-401`'s upsert key cascade (isrc → mbid → spotify_id) encodes matching precedence inside the repo; any `BaseRepository.upsert` change touches it.
- **Match-review flow** (`routes/reviews.py`) is the shipped "fix mappings" surface; it has **zero web consumers** today (no route in `App.tsx`) — a live zero-consumer recurrence the identity track should decide on (CLI `mixd reviews` is the only consumer).
- Naming note for that track: `evaluation_service.py`, `match_and_identify_tracks.py`, and `track_identity_service_impl.py` all carry stale "this replaces X / new architecture" docstrings (the pre-v0.7 vintage).

---

## 9. Rule-change proposals (separated — you decide)

These challenge a declared rule with evidence, per the handoff protocol. None applied.

### 9.1 — Carve-out for irreducible SQLAlchemy string-reflection suppressions
`.claude/rules/python-conventions.md` says "No suppressions — resolve lint/type via fixing code". After the `base_repo` cleanup, ~4 pyright-ignores remain that **cannot** be fixed without worse code: SQLAlchemy's dynamic column/attribute reflection has no static type, and the "typed" alternative is `cast("QueryableAttribute[object]", getattr(...))` — an uglier suppression in disguise. **Proposal**: add an explicit, narrow carve-out naming string-keyed SQLAlchemy reflection in the repository layer, and lower `scripts/check_ratchet.sh`'s `BASE_PYRIGHT_IGNORE` to the new floor in the same commit so the carve-out is bounded, not open-ended.

### 9.2 — Legalize (or close) the interface→infrastructure connector-access bypass
`CLAUDE.md` says interface data access goes only via `execute_use_case()`. The connect/auth subsystem openly violates this: `routes/auth.py:46-109` owns DB-backed CSRF state with raw SQLAlchemy; `connectors.py` reads `connector_status` directly from infrastructure. **Archaeology shows this was deliberate** — the v0.6.5 "Shared OAuth Utilities Refactor" (`059726ed`) chose infra-level sharing so CLI and web reuse `exchange_code`, and `routes/settings.py:3-5` documents the bypass informally. **Proposal**: either (a) legalize it explicitly in the invariant text (connector token storage + OAuth CSRF as a named exception), or (b) extract the `ConnectAuth` use-case pair and move CSRF behind an `OAuthStateRepository` protocol. The auth dive recommends (a) — keep the chosen architecture, reconcile the rule text — but flags that the rule currently *reads* as violated, which is itself a problem.

### 9.3 — NOT proposed: Command/Result repetition (lead #9 resolved)
The handoff floated whether the Command/Result-per-use-case floor cost is being overpaid. **Evidence says no.** Sampling found ~85% substantive orchestration / 15% pass-through, and the two heaviest modules disprove the floor-cost worry directly: `sync_likes.py`'s three Commands total ~26 lines and reuse the generic domain `OperationResult`/`SyncCheckpointStatus` (zero custom Results). The pattern is not the cost driver in any hotspot examined. Keep the rule as-is.

---

## 10. "Looks over-engineered but is justified" (the handoff requires this non-empty; it is rich)

History-before-judgment **refuted** a substantial fraction of the leads. Do not "simplify" these:

- **Checkpoint always-write-on-exit** (`sync_likes.py:392`) — scar from `06f44c01`; **commit-per-batch + checkpoint-per-batch** — real v0.6.8 incident (a first user's ~1,200-track import fully rolled back when the Fly.io machine auto-stopped mid-run); **fetch-failure checkpoint-save-then-re-raise** + **force-mode/early-stop heuristic** — v0.6.11 stories (rate-limit failures and first-all-duplicate-batch made imports unrecoverable).
- **Workflow engine**: SIGTERM module-global (`executor.py:139` — fixes a documented sibling-run-clobber); shielded connector cleanup (v0.8.0 audit: `CancelledError` leaks httpx pools); the TaskGroup totality contract; born-large size (golden-snapshot port).
- **Auth**: DB-backed CSRF `DELETE…RETURNING` (v0.6.3 "OAuth State Durability"); the Last.fm env-credential path (documented CLI/local-dev story, the `DEFAULT_USER_ID` sibling); per-provider status probes deliberately *not* sharing a template; `try_silent_refresh` as the server-safe primitive.
- **base_repo**: `SimpleMapperFactory` (extracted from real duplication), identity-map relationship loading (incident-grade perf), `begin_nested` savepoints + `_deduplicate_batch` (PostgreSQL semantics).
- **Playlists**: the entry identity-preservation machinery (`e1045cab` v1.0-blocker incident); `_append_entries` dedupe (the Curator's weekly append-mode re-run must not re-add last week's tracks); the three-transaction sync-status shape.
- **Workflow runs**: the CRASHED/CANCELLED/FAILED except-ladder (distinguishes server-reload kills from SIGTERM drain from pipeline bugs); the `uq_workflow_runs_active` concurrency-guard comment (multi-instance correctness).

If this section looked empty, the review wasn't done. It isn't — roughly half the handoff's "looks heavy" leads are incident-backed.

---

## 11. Recommended sequencing & verification philosophy

A suggested order by leverage-per-risk (not a schedule):

1. **The dropped-result seam fix** (§3 root) — *one change makes six surfaces report the real result*. Unblocks F1/F4. **M**. Net: `test_progress.py`, `test_imports.py`; build the SSE-seam characterization tests first.
2. **F3 enrichment fail-loud** (**S**) and **F2 connector-sync fail-loud** (**S**) — independent, both protect the Curator's playlist; both reuse existing degrade/ERROR machinery.
3. **Doc-truing pass** on `01-user-flows.md` + the two always-loaded references (**S**) — cheap, high-value, prevents the drift from misleading the next planner.
4. **`interfaces.py` aggregate split** (**L**) — before the v0.10.x artists epic; typing-only, pyright-verified; fixes the dual-import-path violation.
5. **F9 ceremony collapse** (**M**) and **F10 progress rename** (folds into #1) — pure-refactor behind new characterization nets.
6. **base_repo periphery trim** + **validator unification** (**M** + **S**).
7. **F6 playlist track editing** — decided 2026-06-15: build it. Filed as a committed feature story (L) in `unscheduled.md`; entry-identity threading first, then add/remove/reorder. Not yet scheduled to a version.

**Verification discipline** (carried in every dive): the fast suite (3,117 tests) is the standing net. **CLI is coverage-omitted** — every CLI-touching recommendation above names the characterization tests to build *first* (via `CliRunner`, per `cli-patterns.md`). The two untested hotspots — `update_connector_playlist.py` (zero direct tests) and the auth callbacks (zero coverage) — must get characterization tests before any change, not after.

---

## Appendix — disposition of every handoff §4 lead

| Lead | Disposition |
|---|---|
| #1 `update_connector_playlist` ceremony | **Finding F9** — ceremony without a scar, confirmed by archaeology (`e6a6124c` replaced "trust the connector" comments). |
| #2 `sync_likes` shape | **Refuted as shape debt** — size is incident-hardened orchestration; the real finding is the error path (F4/F5). No split recommended. |
| #3 progress subsystem | **Finding F1 (bug) + F10 (naming/ceremony)** — upgraded from "how many layers?" to a verified silent-failure cluster. |
| #4 `MetadataBuilder` | **Finding (folded into F9)** — 1 caller, zero-caller `.build()`, carries a pyright-ignore; replace with a function. |
| #5 `base_repo` generics | **Mixed** — core justified, ~130 lines of periphery speculative; 4 irreducible ignores → rule §9.1. |
| #6 dual `MatchingConfig` thresholds | **Handed to identity track** (§8). |
| #7 CLI↔Web parity drift | **Resolved** — workflow delete is now CLI-only (API removed by the hygiene pass); match-review is a live zero-consumer web gap (§8). |
| #8 DUP-06 batch rendering | **Confirmed minor** — `playlist_commands.py:793` ≈ `:877`; plus a broader CLI-output-assembly gap (a partial home exists in `cli_helpers.py`, adoption incomplete). **XS.** |
| #9 69 use cases / pass-through ratio | **Justified** (§9.3) — pattern is not the cost driver. |
| #10 workflow engine altitude | **Suspicion inverts** — engine justified; debt is two seams (F3 + validator duplication). |
