# Project Mixd — Planning

**Current Version**: 0.8.17.1
**Next**: v0.9.0 Workflow assistant — right-panel chat ported from couplefins (pre-work brief landed 2026-07-02 in [v0.9.x.md](v0.9.x.md)) — **or [v0.8.18 Identity Integrity](v0.8.18.md)**, scheduled 2026-07-02 from the research pass: it repairs *active* mapping-confidence corruption (compounds with every playlist import) and gates v0.10.0 artist identity, v1.0.1 Apple Music, and all sharing milestones; which runs first is the user's call. The v0.8.12–v0.8.17 sweep arc is closed; the identity-resolution research pass also landed 2026-07-02 ([design-space memo](identity-resolution-design-space.md) + [governance companion](identity-governance-design-space.md) + PDR-001/002 in docs/decisions/), and its stories are now sequenced into v0.8.18, v0.10.0, v1.0.0, and v1.0.4. Follow-up pool: the [dependency-audit work orders](dependency-audit-findings.md) (W1–W10) and the PLR0913/0917 flip decision (options in [spoke 26](fable-sweep/26-ratchet-closeout.md)).

**v0.8.17.1 shipped (2026-07-02)** — post-ship revision: the v0.8.17 tag was cut from a commit that predated its own version bump (a `git commit` invoked through a pipe masked a pre-commit hook failure, so the tag/release/deploy fired against `main` while `pyproject.toml` still read `0.8.16`). Purely a version-metadata + docs fix — the deployed *code* was already the complete, correct v0.8.17 closeout; this revision makes the running app's self-reported version, the OpenAPI schema, and the generated web client match the tag. Rides along: the identity-resolution research's roadmap sequencing (v0.8.18 Identity Integrity, v1.0.0 Mapping Supersession, v1.0.4 Data Sovereignty).

**v0.8.17 shipped (2026-07-02)** — **Sweep closeout: ratchet, review & dependency audit** — closes the v0.8.12–v0.8.17 arc. The **ratchet re-census** (spoke 26) found no suppressed PLR rule at zero, so none flipped — the honest outcome, each re-documented dated in `pyproject.toml` (PLR1702's survivors sit in `apply_playlist_assignments` + the user-protected Apple Music tree; PLR0911's five are guard/classification chains where every return is a distinct log event or HTTP status; PLR0913/0917 remain the user's call, options in the spoke). The **noqa whodunit** traced the 14-vs-13 discrepancy to v0.8.7 (`d2d3a179`) and fixed it by code (`_emitter` rename, suppression deleted, count back to 13); vulture went from 12 findings to clean (8 dead `operation_summary` properties deleted — the 7 use-case copies never had a consumer, diff_engine's died with spoke 12; `retryable` whitelisted as frontend-read; `NO_ISRC`/`added_at_dates` whitelisted as dated parked decisions); ratchet baselines lowered (whitelist 79→63, pyright-ignore 20→18). The **visual gate revival** falsified its own premise: CI's e2e job had been red since 06-01 on image↔package skew (`v1.60.0-noble` vs Playwright 1.61.1 — browsers never launched); with the pin fixed, 16/18 baselines were still valid and only settings-sync ×2 regenerated (legitimate v0.8.4 schedule-card growth). Docker-pinned e2e + vulture + `check_ratchet.sh` joined CLAUDE.md's version-bump bar; two latent bugs fixed in the regen procedure (`CI=true`; pnpm 11.5 drops `--update-snapshots` after `--`). The **dependency audit** ([findings](dependency-audit-findings.md)) found the tree healthy — 0 unused runtime deps, versions current — with 10 work orders (headliners: undeclared direct `starlette` import, unwired `interrogate`/`bandit`, a hand-rolled retry in `base_repo.py`, the import-linter gap — v0.8.12's "already in place" claim was false). Closeout diff reviewed (engineer + QA agents): zero violations. Full matrix green (3386 backend / 727 frontend / 18/18 e2e in the pinned image). The **identity-resolution research pass** shipped alongside: design-space + governance memos, PDR-001/002, and the new `docs/decisions/` PDR system.

**v0.8.16 shipped (2026-07-02)** — **Sweep Wave 4: high-risk decompositions**, the sweep's two riskiest refactors run last on a maximally-trusted suite. **Spoke 11** (Fable main thread) flattened the workflow executor's triple-nested closure stack — but characterization tests landed FIRST: 6 new tests pinning the observer event sequence (the SSE contract — previously unpinned; degrade emits `on_node_failed` but never `on_node_completed`), the timeout firing + node-identity rewrap path, and the first-ever real `run_workflow` executions. The flatten itself: a mutable `_RunState` (exactly the former closure captures; `parameters` by reference, `_shutdown_requested` deliberately left a live module global) with `_run_task_inner`/`_run_node_lifecycle`/`_build_and_execute_workflow` promoted to module level; AST nesting 4→2; move fidelity verified by AST-normalized diff (only intended deltas); all prior engine tests pass byte-unmodified. **Spoke 06** deleted the base-connector `get_playlist` reflection — exploration showed it was production-dead (Spotify overrides directly; no `get_<name>_playlist` methods exist), so migration became deletion — plus `get_connector_config` entirely (zero production callers, user-approved) and the mirrored conditional-`on_page` fork in the sync service; mock assertions retargeted stricter (`on_page=None` explicit). **Spoke 24 skipped** (user-approved): both work-order premises falsified — 72 import sites not ~30, and all 31 relationships are typed `Mapped[DBX]` annotations (not string targets), making the split a circular-import surface for navigation-only gain. Wave review: mechanical AST fidelity check + independent engineer review — zero findings. Full matrix green (3386 backend / frontend check+build; pre-existing noqa-baseline discrepancy unchanged, whodunit scheduled in the closeout). Closeout remains (v0.8.17).

**v0.8.15 shipped (2026-07-02)** — **Sweep Wave 3: frontend structure**, the frontend's whole structural-debt inventory in one deploy: three behavior-preserving spokes, one commit each, strictly sequenced 16 → 17 → 19. **Spoke 16** introduced the shared `QueryStates` wrapper (canonical loading → error → empty → success; discrete `loading` flag passed verbatim because `isLoading`/`isPending` diverge on disabled queries) + four skeleton primitives (`ListRowsSkeleton`/`BlocksSkeleton`/`CardGridSkeleton`/`DetailHeaderSkeleton`, bar dimensions as data for per-page pixel fidelity), migrating 9 screens off hand-rolled ladders, deleting 7 local skeletons, and normalizing Tags/ImportHistory error states onto `QueryErrorState` (new static `description` override keeps their copy verbatim). **Spoke 17** de-forked the cmdk track-search scaffold into `CommandSearchList` (owns search state, query, threshold, and the 3-state ladder) + `TrackResultRow`; `TrackSearchCombobox` deleted, `AddTracksDialog` rebuilt on the shell with consumer-owned checkbox/badge slots, `TagAutocomplete` deliberately skipped (shares only the outer scaffold). One approved visible copy change: "Searching..." → "Searching…". **Spoke 19** decomposed the oversized screens along the audited seams into 13 page-scoped component files — Dashboard 552→241, TrackDetail 554→306 (+`lib/match-methods.ts`), WorkflowRunDetail 511→210, PlaylistDetail 937→186, and Tags' three ConfirmationDialogs collapsed to one mode-keyed instance (copy byte-identical); Library + ConnectorPlaylistPickerDialog skipped (no clean seam). The aggregate wave diff was Fable-reviewed with every moved symbol mechanically diffed against its pre-wave original — zero findings. Full matrix green (3384 backend / 727 frontend + 96-state playlist-detail visual audit). Pre-existing visual-baseline staleness vs the freshly-installed Chrome 149 logged in the hub's Deferred list (failing set identical pre/post wave — not wave-caused). Wave 4 + closeout remain (v0.8.16–v0.8.17).

**v0.8.14 shipped (2026-07-02)** — **Sweep Wave 2: backend structure**, the bulk of the Fable Sweep: fourteen behavior-preserving spokes, one commit each, full matrix green at every step. The five use-case skeletons collapsed into `_shared` helpers (spoke 07); the six stray route handlers moved back behind `execute_use_case` (08); `update_canonical_playlist`'s diff sub-algorithms pushed down to domain (10); the play-importer pipeline + resolver decomposed with typed params and single-source username resolution (02/03); `ConnectorMappingSpec` replaced the mapping 7-tuple (21); the `list_tracks` filter builder + keyset pager extracted (22); mapper machinery split out of `base_repo.py` (23); plus the matching-loop hoist (04), Last.fm boundary validation (05), timed-query envelope (09), backend minor batch (15), service-method splits (14), and CLI structural pass (25). This ship also carries a **dependency refresh** landed ahead of the v0.8.15 frontend wave — react-router 7→8, cryptography 48→49, fastapi 0.139, Starlette 1.3, plus frontend/backend bumps (four <24h-old releases left to pnpm's release-age cooldown) — and schedules a **dependency audit** into the v0.8.17 closeout. Full matrix green (3322 backend / 699 frontend). Waves 3–4 + closeout remain (v0.8.15–v0.8.17).

**v0.8.13 shipped (2026-07-02)** — **Sweep Wave 1: dead code & free wins**, the first execution wave of the Fable Sweep (v0.8.12 audit). Five behavior-preserving commits, each with a `git grep` gate: the **type floor** locked first (8 basedpyright rules — `reportUnknown*` ×5, `reportMissingTypeArgument`, `reportImplicitOverride`, `reportDeprecated` — flipped `warning`→`error` after a fresh 0-warning verify), then four spokes. **Spoke 12** collapsed the dead execution-Strategy abstraction (Protocol + factory + `CanonicalExecutionStrategy` + `execute_with_strategy` + `ExecutionPlan`, ~75% of the file) to a single pure `plan_api_operations()`; the one live push path is unchanged (API-path integration tests green). **Spoke 13** purged `BatchConfig` + 14 pre-Fellegi-Sunter `MatchingConfig` fields across all four surfaces (domain config / factory / settings / `vulture_whitelist.py`); type-constraint tests were re-pointed to live `ConfidenceScore` fields, not deleted. **Spoke 18** deleted the dead `hasActiveFilters` export (Library already uses the tested `countActiveFilters`). **Spoke 20** added `status-success/error/warning/info` severity tokens and replaced raw Tailwind palette in three shared components — which also repaired a latent phantom `bg-status-error/10` (an undefined token Tailwind v4 was silently dropping) in `MergeTrackDialog`/`UnlinkMappingDialog` with zero edits to them. Full matrix green (3350 backend / 699 frontend). Waves 2–4 + closeout remain (v0.8.14–v0.8.17).

**v0.8.11 shipped (2026-06-30)** — **Manual playlist track editing**, the closing slice of the v0.8.x cycle: add, remove, and reorder a canonical playlist's tracks directly from its detail page, instead of round-tripping through connector sync or a workflow. Three frozen-Command use cases (`AddPlaylistTracks` / `RemovePlaylistEntries` / `ReorderPlaylistEntries`) + REST endpoints + CLI parity (`mixd playlist add-tracks` / `remove-tracks` / `reorder`) sit on the entry-identity threading already shipped in v0.8.7 (`PlaylistEntry.id`, `eq=False`), so the only load-bearing schema change was finally *exposing* `PlaylistEntrySchema.id`. The web surface is a **dnd-kit** sortable `PlaylistTrackEditor` (keyboard reorder + screen-reader announcements) + an `AddTracksDialog` multi-select search modal; **remove is optimistic with a deferred-commit "Undo" snackbar** — the `DELETE` waits out the snackbar window, so an undone removal never reaches the server and keeps the entry's `added_at`/position (re-truing flow 3.6 away from its old confirm-dialog spec). Manual add deliberately allows duplicates without disturbing the workflow-append dedupe path.

**v0.8.10 shipped (2026-06-29)** — editor polish + trustworthy play-history config in one ship (it **absorbed the play-history-config work originally scoped as a separate feature** — the v0.8.9-review follow-ons). Four stories: (1) a typed **entry-intent state machine** (`load`/`seed`/`blank`, in `editor-entry.ts` + `useEditorEntry`) replaces the implicit `{ imported: true }` reset chokepoint so the navigation-surviving editor store is predictable on every entry — a fresh "New Workflow" never shows a stale draft (and a latent web-suite "1 error" was traced + fixed along the way: a partial recovery snapshot crashing `useWorkflowSSE`); (2) **browse-to-link** retires paste-an-ID on Playlist Detail by reusing the existing import picker in a new single-select `mode` — no new backend (the browse method + endpoint shipped in v0.8.7–v0.8.8); (3) **config-aware validation** — `filter.by_metric`/`sorter.by_metric` check what the upstream `play_history` enricher is *configured* to emit, not its capability, so the editor's green check stops certifying empty-result workflows; plus a `period_days`-is-inert warning (which revealed + cleaned an inert `period_days` in all 9 play-history templates); (4) a **breaking day-window rename** — `min/max_days_back` → `not_played_in_days`/`played_within_days` — across code + seed JSON + a first-of-its-kind JSONB key-rewrite migration (033) over the three `WorkflowDef` columns. **Sub-flows/node-grouping was cut** as the wrong solution to a real problem (40+ node navigation) — recorded in [unscheduled.md](unscheduled.md) for a lighter approach.

**v0.8.7–v0.8.8 shipped (2026-06-25)** — **Import/sync reconciliation**, the correctness epic that fixed a *very broken* Spotify import + sync. One `PlaylistReconciliationEngine` now fetches the real remote fresh and diffs at the connector-identifier level — killing the self-join that silently dropped tracks, made push a no-op, and left the destructive guard dead. Unmatched tracks survive as first-class **unresolved** rows, destructive syncs are gated behind a real `confirm_token` 409 round-trip, and every run leaves a durable `OperationRun` audit row. **v0.8.7** shipped the engine + REST + CLI (`import-spotify` / `sync` / `sync-preview` / `repair`, per-item-atomic batches, position-aware duplicate removal on push). **v0.8.8** brought it to the web: a headless `OperationsProvider` that surfaces overnight failures + an "N running" sidebar badge, **Retry failed only** (server-reconstructed from the audit row via a pure `OperationRun.is_retryable`), the destructive-sync confirm dialog, additive imports with honest per-playlist progress + unresolved bulk-repair, and one `DirectionChooser` direction vocabulary. A web-robustness pass rode along in the same ship — SSE stream-end REST reconcile, toast-ledger dedup, render-pure recovery gate, and auth-gated/non-background polling extracted to a shared `useAdaptivePollingList`.

**v0.8.6 shipped (2026-06-18)** — **Cycle hardening & cleanup** from the 2026-06 design-debt review. Split the 2,062-line `repositories/interfaces.py` into 15 per-aggregate protocol modules; extended the `lazy="raise_on_sql"` eager-load guard across the whole ORM graph (eager-coverage audit + `passive_deletes` belt-and-braces, every relationship now carries an explicit `lazy=`); collapsed the `update_connector_playlist` verification ceremony and `base_repo` speculative periphery. Connector push now reports honestly via `PlaylistOpsOutcome.fully_applied` — a partial push routes to ERROR instead of a silent SYNCED, and canonical tracks with **no match on the destination** surface as an "unmatched" count in the CLI sync output and a **gold tooltip chip** on the web Playlist Detail (persisted via `last_sync_tracks_unmatched`, migration `029`). A code-review pass also caught a suppressed-error ADD false-success and an orphaned `@db_operation("delete")` decorator. Two rule carve-outs reconciled (irreducible SQLAlchemy-reflection suppressions; the interface→infra OAuth-access exception).

**v0.8.4 shipped (2026-06-09)** — Background **sync scheduling** + proactive **failure surfaces**, closing the "sync overnight → rebuild in the morning" loop. Daily/weekly schedules for the three sync targets (`lastfm:plays`, `spotify:likes`, `lastfm:likes`) live on their existing Settings›Sync cards via a shared `ScheduleCard` + `useScheduleController` (the bespoke `WorkflowScheduleCard` was refactored onto the same pair — zero new backend; the v0.8.2/8.3 engine already covered it). Two discovery-without-checking surfaces ride the already-fetched `GET /schedules`: a dashboard aggregate `ScheduleFailuresBanner` and an amber "Failing" marker on each workflow row, both self-clearing on the scheduler's success reset (one shared `AlertBanner` primitive behind both the per-schedule and aggregate banners). Also folded in: a human-facing per-workflow `run_number` (migration `027`, shown instead of the UUID), the `loaded_list`/`loaded_one` no-I/O mapper read primitives + a scoped 7-relationship `lazy="raise_on_sql"` guard (down-payment on the v0.8.6 eager-load-hardening epic), and a toolchain/dependency bump pass (SQLAlchemy 2.0.50, uv 0.11, node 24, pnpm 11.5.2, flyctl 1.6, Playwright 1.60).

**v0.8.3 shipped (2026-06-07)** — Workflow scheduling **web UI** + an unplanned **workflow-page redesign with live-run reconnection**. A timezone-aware `SchedulePicker` (daily/weekly toggle, no cron) sets automation; the workflow list shows a "Next run" column sourced from a single caller-scoped `/schedules` fetch (no N+1). The detail page replaced the loose pipeline strip + `LastRunCard` with one state-aware `WorkflowStatusPanel` (active/idle/never-run) + a dedicated `RunHistoryTable`, and now reconnects to an in-flight run after reload via an app-global active-runs source + DB snapshot adoption. A paired scheduler fix persists an `operation_id` on scheduled runs so they're reconnectable, and the review pass unified schedule advancement into one fresh-read `_release` transaction. **Partial:** only the per-schedule failure badge shipped; the proactive dashboard banner + workflow-list failure indicator are carried to v0.8.4.

**v0.8.2 shipped (2026-06-07)** — Workflow scheduling **engine & CLI**. DB-stored daily/weekly schedules (no freeform cron; `croniter` kept only as the internal DST-correct next-occurrence engine over `zoneinfo`) fire from an in-process poll loop in the FastAPI lifespan, built on a shared `run_periodic_background_loop` (the sweeper was retrofitted onto it). Resilience: optimistic per-tick claim, a txn-level advisory poll-lock for multi-instance leader election, a stuck-start reaper-as-skip (no failure-streak bump), per-user OAuth-token isolation, and a single `schedules` table with an exclusive-arc CHECK for workflow-vs-sync targets. Drivable end-to-end via `mixd workflow schedule` / `mixd sync schedule`.

**v0.8.1 shipped (2026-05-31)** — Workflow engine swap: Prefect 3 removed in favor of a homespun stdlib-asyncio DAG executor. Parallel execution levels are computed via Kahn's topological sort (a pure domain function) and each level runs in an `asyncio.TaskGroup`; run-state, cancellation (SIGTERM), and fault tolerance are owned in-process. Prefect and its full transitive tail are gone — the app no longer imports an embedded orchestration server at boot. The `workflows/` package was reorganized into `definition/`, `engine/`, and `nodes/`. Review pass also fixed primary-input track-count diagnostics and made lifecycle-observer emission best-effort.

**v0.8.0 shipped (2026-05-30)** — Run reliability & validation hardening opened the v0.8.x scheduling cycle: a first-writer-wins terminal-write guard, a distinct `crashed` status (worker died) vs `failed` (logic broke), an OS-thread heartbeat watchdog that survives a blocked event loop, three closed silent-wrong-result validation gaps, and a SIGTERM-shielded connector cleanup.

**Earlier refactor (shipped as 0.7.8.20)**: Workflow "kinds" consolidated — the read-only built-in **template** kind was eliminated in favor of a file-backed template **gallery** + clone-on-use, leaving a single editable `Workflow` entity (migration `023` drops `is_template`/`source_template`, the read-only guards, and shared `user_id IS NULL` rows). Clone-on-use mints a fresh unique slug, and Duplicate runs through a single-transaction `DuplicateWorkflowUseCase`. This delivered most of v0.8.9's template *plumbing* early; v0.8.9 now scopes down to curating the template content + import/export.

→ [Completed milestones](completed/) | [Unscheduled ideas](unscheduled.md)

---

## Planned Versions

Each milestone delivers a **vertical slice** — backend API + frontend page together — so every increment is testable end-to-end.

| Version | Goal | Status | Details |
|---------|------|--------|---------|
| **v0.2.7** | Advanced workflow features + DRY consolidation | ✅ Completed | [details](completed/v0.2.x.md#v027-advanced-workflow-features) |
| **v0.3.0** | Web UI foundation + playlists + settings | ✅ Completed | [details](completed/v0.3.x.md#v030-web-ui-foundation--playlists-vertical-slice-1) |
| **v0.3.1** | Imports + real-time progress | ✅ Completed | [details](completed/v0.3.x.md#v031-imports--real-time-progress-vertical-slice-2) |
| **v0.3.2** | Track library + search | ✅ Completed | [details](completed/v0.3.x.md#v032-library--search-vertical-slice-3) |
| **v0.3.3** | Dashboard + stats | ✅ Completed | [details](completed/v0.3.x.md#v033-dashboard--stats-vertical-slice-4) |
| **v0.4.0** | Workflows (persistence, visualization, CRUD) | ✅ Completed | [details](v0.4.x.md#v040-workflow-persistence--visualization-vertical-slice-5a) |
| **v0.4.1** | Workflow execution + run history | ✅ Completed | [details](v0.4.x.md#v041-workflow-execution--run-history-vertical-slice-5b) |
| **v0.4.2** | Run-first workflow UX | ✅ Completed | [details](v0.4.x.md#v042-run-first-workflow-ux-vertical-slice-5c) |
| **v0.4.3** | Visual workflow editor + versioning | ✅ Completed | [details](v0.4.x.md#v043-visual-workflow-editor--preview-vertical-slice-5d) |
| **v0.4.4** | Connector playlist linking | ✅ Completed | [details](v0.4.x.md#v044-connector-playlist-linking-vertical-slice-6) |
| **v0.4.5** | Code & test suite hardening | ✅ Completed | [details](v0.4.x.md#v045-code--test-suite-hardening) |
| **v0.4.6** | Track provenance & duplicate merge | ✅ Completed | [details](v0.4.x.md#v046-track-provenance--merge-vertical-slice-7a) |
| **v0.4.7** | Track relink & unlink | ✅ Completed | [details](v0.4.x.md#v047-track-relink--unlink-vertical-slice-7b) |
| **v0.4.8** | Usability & self-explanatory interface pass | ✅ Completed | [details](v0.4.x.md#v048-usability--self-explanatory-interface-pass) |
| **v0.4.9** | Data integrity & quality audit | ✅ Completed | [details](v0.4.x.md#v049-data-integrity--quality-audit) |
| **v0.4.10** | Cross-service play history deduplication | ✅ Completed | [details](v0.4.x.md#v0410-cross-service-play-history-deduplication) |
| **v0.4.11** | CLI unification & polish | ✅ Completed | [details](v0.4.x.md#v0411-cli-unification--polish) |
| **v0.5.0** | CI/CD + environment hardening | ✅ Completed | [details](completed/v0.5.x.md#v050-cicd--environment-hardening) |
| **v0.5.1** | PostgreSQL migration + optimization | ✅ Completed | [details](completed/v0.5.x.md#v051-postgresql-migration) |
| **v0.5.2** | PostgreSQL-native feature adoption | ✅ Completed | [details](completed/v0.5.x.md#v052-postgresql-native-feature-adoption) |
| **v0.5.3** | Containerization & deployment | ✅ Completed | [details](completed/v0.5.x.md#v053-containerization--deployment) |
| **v0.5.4** | OAuth + integrations UX + WCAG AA + light/dark mode + settings persistence | ✅ Completed | [details](completed/v0.5.x.md#v054-oauth--credentials) |
| **v0.5.5** | Parallel execution & performance | ✅ Completed | [details](completed/v0.5.x.md#v055-parallel-execution--performance) |
| **v0.5.6** | Auth gate, automated deploys & startup DX | ✅ Completed | [details](completed/v0.5.x.md#v056-auth-gate-automated-deploys--startup-dx) |
| **v0.5.7** | Security hardening | ✅ Completed | [details](completed/v0.5.x.md#v057-security-hardening) |
| **v0.5.8** | Playlist sync safety guards | ✅ Completed | [details](completed/v0.5.x.md#v058-playlist-sync-safety-guards) |
| **v0.5.9** | Project rename: narada → mixd | ✅ Completed | [details](completed/v0.5.x.md#v059-project-rename--narada--mixd) |
| **v0.5.10** | Polish, documentation & observability | ✅ Completed | [details](completed/v0.5.x.md#v0510-polish-documentation--observability) |
| **v0.6.0** | Multi-user: schema + user identity + UUIDv7 PKs | ✅ Completed | [details](completed/v0.6.x.md#v060-schema--user-identity-foundation) |
| **v0.6.0.post1** | Neon Auth integration fix (SPA auth flow) | ✅ Completed | [details](completed/v0.6.x.md#v060post1-neon-auth-integration-fix) |
| **v0.6.1** | Account management (profile, sign out, delete) | ✅ Completed | [details](completed/v0.6.x.md#v061-account-management) |
| **v0.6.2** | Multi-user: repository + use case scoping | ✅ Completed | [details](completed/v0.6.x.md#v062-repository--use-case-scoping) |
| **v0.6.3** | Multi-user: per-user OAuth | ✅ Completed | [details](completed/v0.6.x.md#v063-per-user-oauth) |
| **v0.6.4** | Multi-user: testing + data purge | ✅ Completed | [details](completed/v0.6.x.md#v064-testing--data-purge) |
| **v0.6.5** | First-class CLI (identity + feature parity) | ✅ Completed | [details](completed/v0.6.x.md#v065-first-class-cli) |
| **v0.6.6** | Data isolation housekeeping | ✅ Completed | [details](completed/v0.6.x.md#v066-data-isolation-housekeeping) |
| **v0.6.7** | OAuth UX fix & connector card settings | ✅ Completed | [details](completed/v0.6.x.md#v067-oauth-ux-fix--connector-card-settings) |
| **v0.6.8** | Resilient imports, UUID cleanup & review persistence | ✅ Completed | [details](completed/v0.6.x.md#v068-resilient-imports--real-time-progress) |
| **v0.6.9** | SSE auth & connection resilience | ✅ Completed | [details](completed/v0.6.x.md#v069-sse-auth--connection-resilience) |
| **v0.6.10** | Neon platform integration & multi-user hardening | ✅ Completed | [details](completed/v0.6.x.md#v0610-neon-platform-integration--multi-user-hardening) |
| **v0.6.11** | Likes sync resilience & Sync page redesign | ✅ Completed | [details](completed/v0.6.x.md#v0611-likes-sync-resilience--sync-page-redesign) |
| **v0.6.12** | Explicit Any cleanup — zero Any codebase | ✅ Completed | [details](completed/v0.6.x.md#v0612-explicit-any-cleanup--zero-any-codebase) |
| **v0.7.0** | Preference system — rate tracks as hmm/nah/yah/star | ✅ Completed | [details](v0.7.0-1.md#v070-preference-system) |
| **v0.7.1** | Preference sync from likes — imported likes become preferences with original dates | ✅ Completed | [details](v0.7.0-1.md#v071-preference-sync-from-likes) |
| **v0.7.2** | Tagging system — categorize tracks by mood, energy, context | ✅ Completed | [details](completed/v0.7.2-3.md#v072-tagging-system) |
| **v0.7.3** | Playlist browser — browse & import Spotify playlists | ✅ Completed | [details](completed/v0.7.2-3.md#v073-playlist-browser) |
| **v0.7.4** | Tag & preference bootstrap — bulk-map playlists to tags/preferences | ✅ Completed | [details](completed/v0.7.4-5.md#v074-tag--preference-bootstrap) |
| **v0.7.5** | Workflow integration & quick filters | ✅ Completed | [details](completed/v0.7.4-5.md#v075-workflow-integration--quick-filters) |
| **v0.7.6** | Tag maintenance & single-playlist Spotify polish — tag mgmt page, force-refresh, route integration tests | 🚀 Shipped | [details](v0.7.6.md#v076-tag-maintenance--single-playlist-polish) |
| **v0.7.7** | Operation Run Log — persisted import history + post-run toast | 🚀 Shipped | [details](v0.7.7.md#v077-operation-run-log) |
| **v0.7.8** | Mobile responsiveness + visual regression baseline (Playwright `toHaveScreenshot`) | 🚀 Shipped | [details](v0.7.8.md#v078-mobile-responsiveness) |
| **v0.8.0** | Run reliability & validation hardening | 🚀 Shipped | [details](v0.8.0-0.8.4.md#v080-run-reliability--validation-hardening) |
| **v0.8.1** | Workflow engine swap (Prefect → stdlib asyncio) | 🚀 Shipped | [details](v0.8.0-0.8.4.md#v081-workflow-engine-swap-prefect-to-stdlib-asyncio) |
| **v0.8.2** | Workflow scheduling — engine & CLI | 🚀 Shipped | [details](v0.8.0-0.8.4.md#v082-workflow-scheduling---engine--cli) |
| **v0.8.3** | Workflow scheduling — web UI & failure alerts | 🚀 Shipped | [details](v0.8.0-0.8.4.md#v083-workflow-scheduling---web-ui--failure-alerts) |
| **v0.8.4** | Background sync scheduling | 🚀 Shipped | [details](v0.8.0-0.8.4.md#v084-background-sync-scheduling) |
| **v0.8.5** | Operation & surface reliability (design-debt review) | 🚀 Shipped | [details](v0.8.5-0.8.6.md#v085-operation--surface-reliability) |
| **v0.8.6** | Cycle hardening & cleanup (design-debt review) | 🚀 Shipped | [details](v0.8.5-0.8.6.md#v086-cycle-hardening--cleanup) |
| **v0.8.7** | Import/sync reconciliation — reliability (backend + CLI) | 🚀 Shipped | [details](v0.8.7-0.8.8.md#v087-importsync-reliability-backend--cli) |
| **v0.8.8** | Import/sync reconciliation — web UI | 🚀 Shipped | [details](v0.8.7-0.8.8.md#v088-importsync-web-ui) |
| **v0.8.9** | Workflow templates & import/export | 🚀 Shipped | [details](v0.8.9-0.8.10.md#v089-workflow-templates--importexport) |
| **v0.8.10** | Editor polish (predictable canvas + playlist browse) + trustworthy play-history config (absorbed the v0.8.9 review follow-ons; sub-flows cut) | 🚀 Shipped | [details](v0.8.9-0.8.10.md#v0810-editor-polish--trustworthy-play-history-config) |
| **v0.8.11** | Manual playlist track editing (design-debt review) | 🚀 Shipped | [details](v0.8.11.md#v0811-manual-playlist-track-editing) |
| **v0.8.12** | The Fable Sweep — audit & work orders (execution → v0.8.13–v0.8.17; docs-only, no deploy artifact) | 🚀 Shipped | [details](v0.8.12.md#v0812-the-fable-sweep--structural-hardening) |
| **v0.8.13** | Sweep Wave 1 — dead code & free wins (+ basedpyright ratchet flip) | 🚀 Shipped | [details](v0.8.13-0.8.17.md#v0813-sweep-wave-1--dead-code--free-wins) |
| **v0.8.14** | Sweep Wave 2 — backend structure (use-case collapse, persistence, play pipeline) | 🚀 Shipped | [details](v0.8.13-0.8.17.md#v0814-sweep-wave-2--backend-structure) |
| **v0.8.15** | Sweep Wave 3 — frontend structure (QueryStates, search de-fork, page splits) | 🚀 Shipped | [details](v0.8.13-0.8.17.md#v0815-sweep-wave-3--frontend-structure) |
| **v0.8.16** | Sweep Wave 4 — high-risk decompositions (executor flatten, connector contract) | 🚀 Shipped | [details](v0.8.13-0.8.17.md#v0816-sweep-wave-4--high-risk-decompositions) |
| **v0.8.17** | Sweep closeout — ratchet, Fable review & dependency audit | 🚀 Shipped | [details](v0.8.13-0.8.17.md#v0817-sweep-closeout--ratchet--review) |
| **v0.8.18** | Identity integrity — confidence repair, ISRC guards, drift metrics (2026-07 research D1; gates v0.10.0 artist identity, v1.0.1 Apple Music, v1.1.x–v1.2.x sharing) | 🔜 Not Started | [details](v0.8.18.md#v0818-identity-integrity) |
| **v0.9.0** | Workflow assistant — right-panel chat (couplefins port) + shared tool registry | 🔜 Not Started | [details](v0.9.x.md#v090-workflow-assistant-right-panel-chat) |
| **v0.9.1** | MCP server — mixd as a tool surface (stdio; consumes the v0.9.0 registry) | 🔜 Not Started | [details](v0.9.x.md#v091-mcp-server-mixd-as-a-tool-surface) |
| **v0.9.3** | Follow-ups & hardening — placeholder, scoped after v0.9.0/v0.9.1 ship | 🔜 Not Started | [details](v0.9.x.md#v093-follow-ups--hardening-placeholder) |
| **v0.10.0** | First-class artists | 🔜 Not Started | [details](v0.10.x.md#v0100-first-class-artists) |
| **v0.10.1** | First-class albums | 🔜 Not Started | [details](v0.10.x.md#v0101-first-class-albums) |
| **v0.10.2** | Physical media & Discogs | 🔜 Not Started | [details](v0.10.x.md#v0102-physical-media--discogs) |
| **v0.10.3** | Manual scrobbling | 🔜 Not Started | [details](v0.10.x.md#v0103-manual-scrobbling) |
| **v1.0.0** | Data quality tools | 🔜 Not Started | [details](v1.0.x.md#v100-data-quality) |
| **v1.0.1** | Apple Music connector | 🔜 Not Started | [details](v1.0.x.md#v101-apple-music-connector) |
| **v1.0.2** | Rekordbox connector + audio quality enrichment | 🔜 Not Started | [details](v1.0.x.md#v102-rekordbox-connector) |
| **v1.0.3** | Cross-user track identity (foundation for sharing & social; trust machinery parked per 2026-07 revision) | 🔜 Not Started | [details](v1.0.x.md#v103-cross-user-track-identity) |
| **v1.0.4** | Data sovereignty — continuous archive & exit rights (last pre-social milestone; direction-neutral under PDR-001) | 🔜 Not Started | [details](v1.0.x.md#v104-data-sovereignty--archive--exit-rights) |
| **v1.1.0** | Privacy controls & public profiles | 🔜 Not Started | [details](v1.1.x.md#v110-privacy-controls--public-profiles) |
| **v1.1.1** | Social graph & follows | 🔜 Not Started | [details](v1.1.x.md#v111-social-graph--follows) |
| **v1.1.2** | Activity feed & social context | 🔜 Not Started | [details](v1.1.x.md#v112-activity-feed--social-context) |
| **v1.1.3** | Sharing & growth | 🔜 Not Started | [details](v1.1.x.md#v113-sharing--growth) |
| **v1.2.0** | Public track share links + service picker | 🔜 Not Started | [details](v1.2.x.md#v120-public-track-share-links) |
| **v1.2.1** | Saved service preference (cookie + account) | 🔜 Not Started | [details](v1.2.x.md#v121-saved-service-preference) |

---

## Persona Alignment

All three personas use most of what we build. The question isn't "who is this for?" — it's "how does each persona experience this, and are we meeting their needs?" Design every feature assuming all three will interact with it.

See [docs/personas.md](../personas.md) for full persona definitions.

| Version | What | Curator perspective | Tinkerer perspective | Casual perspective |
|---------|------|--------------------|--------------------|-------------------|
| v0.4.x | Workflows | Weekly ritual — build, run, review | Learns system by exploring editor | Not yet reachable (needs LLM, v0.9) |
| v0.5.0–v0.5.3 | Infrastructure | Wants it deployed so they can access from any device | Self-hosts, expects clean setup | Needs hosted instance to exist |
| v0.5.4 | OAuth + auth UX + WCAG + theming + settings | Connects services, picks light/dark mode, settings persist across devices | May prefer CLI auth, but web flow should be clean; appreciates system theme respect | "Connect Spotify" button IS the first impression; light mode widens appeal |
| v0.5.5 | Performance | Faster workflow execution, snappier pages | Appreciates efficient infrastructure | Expects modern web app responsiveness |
| v0.6.x | Multi-user data isolation + first-class CLI | Per-user data isolation, CLI for power curation | Security hardening, CLI as primary interface | Account creation on hosted instance |
| v0.7.x | Preferences + tags + playlist bootstrap | Replaces 4 Spotify playlists with hmm/nah/yah/star, bulk-imports hundreds of themed playlists as tags | Explores tag system, builds taxonomies, workflow nodes for preferences/tags | Likes/dislikes via simple toggle, quick filters for "what should I listen to?" |
| v0.7.8 | Mobile responsiveness | Phone for status checks ("did the overnight sync work?"), laptop for deep curation and workflow editing | Self-host UX feels polished on any viewport; workflow editor stays desktop-only by design | Phone is the primary device — full feature parity except editor; bottom nav, sheet dialogs, no horizontal scroll |
| v0.8.x | Scheduling + templates | Automates the weekly ritual | Templates as onboarding entry point | Scheduling means playlists stay fresh without effort |
| v0.9.x | LLM-assisted creation + MCP surface | Power use — complex intent in natural language; agents can run scheduled maintenance | Interesting tech to explore; mixd plugs into existing local-LLM toolchains via MCP | THE adoption enabler — changes who can use mixd; indirect benefit when third-party agents grow workflows over mixd |
| v0.10.x | Artists, albums, physical | Deeper library modeling, Discogs integration | Rich data model to explore | Browsing by artist/album is intuitive |
| v1.0.x | Data quality + connectors + cross-user identity | Fixes mappings, finds gaps, adds Rekordbox; v1.0.3 makes shared playlists trustworthy | Apple Music broadens self-host appeal; v1.0.3's audit ledger is full data-sovereignty | More services = less lock-in; v1.0.3 makes friend-shared links resolve correctly without trust |
| v1.1.x | Social layer | Share curated playlists, discover curators | Public API surface, federation potential | Shareable links, follows — the growth mechanism |
| v1.2.x | Track share links | Shares individual track finds with friends; service preference syncs across devices | Public track URL surface, opt-in cookie/DB preference layering | Friend's track link is the entry point — picker page is first contact with mixd |

---

## Infrastructure Readiness Matrix

Visual guide to infrastructure capabilities across version milestones:

| Capability | v0.2.7 (CLI) | v0.3.0 (Web Local) | v0.5.x (Deployed) | v0.6.x (Multi-User) |
|------------|--------------|-------------------|-------------------|---------------------|
| **Testing** | ✅ pytest suite, <1min | ✅ + Vitest components | ✅ + E2E (Playwright) | ✅ + isolation tests |
| **CI/CD** | ⚠️ Manual | ⚠️ Manual | ✅ GitHub Actions | ✅ Same |
| **Deployment** | ✅ uv install | ✅ Local (SQLite) | ✅ Docker + Fly.io | ✅ Same |
| **Observability** | ✅ structlog flat JSON | ✅ Same | ✅ + Fly.io stdout JSON | ✅ Same |
| **Authentication** | ❌ Not needed | ❌ Env var tokens | ✅ Neon Auth + OAuth | ✅ + per-user OAuth |
| **Database** | ✅ SQLite | ✅ PostgreSQL (Docker) | ✅ PostgreSQL | ✅ + user_id scoping |
| **Caching** | ❌ Not needed | ✅ Tanstack Query | ✅ + lru_cache | ✅ Same |
| **Security** | ✅ Env vars, secrets | ✅ + CORS (localhost) | ✅ + HTTPS + JWT | ✅ + token encryption |

**Legend**: ✅ Ready | ⚠️ Needs work | ❌ Not needed

**Note**: Right-sized for a local music community (dozens to low hundreds of users). No Redis, CDN, MFA, load testing, or enterprise observability. Focus on quality code over production infrastructure. Revisit scaling if usage exceeds 100+ active users.

---

## Technology Decision Records

Key architecture & tech choices (see CLAUDE.md for migration details):

- **Python 3.14+ & attrs**: Modern type syntax (`str | None`, `class Foo[T]`), immutable domain entities with slots
- **PostgreSQL (v0.5.1)**: Migrated from SQLite for remote hosting and parallel Prefect execution. `psycopg3` driver (SQLAlchemy 2.1 default), managed hosting via Neon (free tier). SQLite removed entirely — PostgreSQL-only. Repository pattern meant zero application-layer code changes. Post-migration optimization: all JSON→JSONB, pg_trgm trigram search, BRIN indexes, DB-side aggregations, tuple IN queries.
- **Vite 8 / Vitest**: Rolldown-powered unified bundler, 10-30x faster builds, native ESM + TypeScript
- **Tailwind CSS v4**: Rust engine (10x performance), @theme design tokens
- **Pydantic v2**: 5-50x faster validation, `from_attributes=True`
- **Clean Architecture + DDD**: Composable workflows, isolated APIs, testable logic (see docs/architecture/README.md)

---

## Reference

### Effort Estimates

Never estimate time, always estimate based on relative effort.

| Size    | Complexity Factors           | Criteria                                                                       |
| ------- | ---------------------------- | ------------------------------------------------------------------------------ |
| **XS**  | Well known, isolated         | Minimal unknowns, fits existing components, no dependencies                    |
| **S**   | A little integration         | Simple feature, 1-2 areas touched, low risk, clear requirements                |
| **M**   | Cross-module feature         | 3-4 areas involved, small unknowns, minor dependencies                         |
| **L**   | Architectural impact         | >=3 subsystems, integrations, external APIs, moderate unknowns                  |
| **XL**  | High unknowns & coordination | Cross-team, backend + frontend + infra, regulatory/security concerns           |
| **XXL** | High risk & exploration      | New platform, performance/security domains, prototype-first, many dependencies |

### Status Options
- Not Started
- In Progress
- Blocked
- Completed
