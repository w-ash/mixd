# The Fable Sweep — Working Hub

> Working artifacts for [v0.8.12](../v0.8.12.md). This is the **persistent-memory ledger** for the structural-hardening campaign: the meta-task, the guardrails every spoke must honor, and the master index of suggestions. Populated by Claude Fable 5 during its availability window (through 2026-07-07); the per-suggestion **spokes** are `NN-<slug>.md` files beside this one, each matching [`_TEMPLATE.md`](_TEMPLATE.md).

## The meta-task

Fan out file-by-file across `src/` (Python) and `web/src` (TS/React), understand what each module does and *why it serves a user*, and record every opportunity to make the codebase tighter, more DRY, more maintainable, more performant, and more current with mid-2026 best practices — **without changing user-visible behavior** (except clearly net-positive UX/perf wins, which are flagged). Output = this index + one self-contained work order per suggestion. Execution is farmed to per-spoke agents after the user approves; Fable code-reviews the result.

If a better batching/sequencing than v0.8.12's three-phase plan exists, record it under **Counter-proposals** below before execution starts.

## Why Fable 5, why this window

Scheduled into the Fable 5 window because its lead over Opus 4.8 is largest exactly here — long, broad, whole-repo reasoning:

- **1M-token context** (128k max output) — hold whole parallel module trees (the 5 connector dirs, the 60 use-cases) in one context to see cross-cutting duplication a smaller window fragments.
- **Longest autonomous operation of any Claude**; **reflects on and validates its own work** at high effort — a sustained multi-module audit that stays coherent hours in.
- **Persistent file-based memory helps it 3× more than Opus 4.8** — this git-tracked ledger is the ideal substrate; carrying state across the sweep is its strength.
- **SOTA coding** — highest on Cognition's FrontierCode ("even at medium effort"), SOTA on CursorBench; Stripe ran a **codebase-wide migration in a day** that would otherwise have taken a team 2+ months.
- Model id `claude-fable-5`; adaptive thinking always on (tune depth with `effort`).

Sources: [Anthropic — Claude Fable 5 & Mythos 5](https://www.anthropic.com/news/claude-fable-5-mythos-5) · [Claude Platform docs — Introducing Claude Fable 5](https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5).

## Scope

- **In:** `src/` (domain / application / infrastructure / interface) + hand-written `web/src` (pages, components, hooks, lib). Behavior-preserving structural cleanup — dead code, duplication, oversized-module decomposition, DRY, June-2026 idioms — plus flagged net-positive UX/perf wins.
- **Out (leave alone):** biome-excluded `web/src/api/generated/**` (orval output) and `web/src/components/ui/**` (shadcn); migrations already applied; anything that changes user-visible behavior without a flagged spoke.

## Method — audit batches (2026-07-01)

Sequenced by ROI density; each batch ends with its spokes written + index rows added, so the ledger survives context loss. Main thread reads comparative/high-ROI targets side-by-side; Explore agents sweep breadth areas.

- **B0** Grounding: layers-and-patterns, personas, user-flows map — done
- **B1** Connector parallel trees vs `_shared` (~11.6k lines, main thread)
- **B2** Persistence: `db_models` + 4 giant repos + `base_repo` (main thread)
- **B3** Use-case CRUD quartets + 18 API route modules (hybrid)
- **B4** Interface CLI: `playlist_commands` / `workflow_commands` / `cli_helpers` (main thread)
- **B5** Application workflows/services + domain + `config/settings.py` (agent sweeps + targeted reads)
- **B6** Frontend: big-five pages/dialogs + settings screens (hybrid)
- **B7** Cross-cutting closers: vulture 13 candidates · PLR ratchet map (143 violations: PLR0913×57, PLR0912×21, PLR0917×20, PLR0914×18, PLR0915×18, PLR0911×7, PLR1702×2) · basedpyright warning census

## Guardrails (every spoke honors these)

- **Behavior-preserving** unless the spoke is explicitly a UX/perf win (with a before/after note + a `docs/web-ui/01-user-flows.md` re-true).
- **Clean breaks** — no shims/aliases/re-export layers; one import path per thing; every call site updated; a `git grep` gate proves the old symbol is gone.
- **Layer invariants** — inward-only deps; domain entities stay `@define(frozen=True, slots=True)` and pure; no sideways/outward reach. The `interface/`→infra OAuth-shared-helpers exception stands.
- **Green at every step** — `uv run pytest` + `pnpm --prefix web test`; never weaken a test to pass a refactor. Add a characterization test before decomposing untested surface.
- **Don't drag in unrelated semantic debt** — log it under **Deferred / out-of-scope** below; don't fix it inline.
- **Ratchet, don't dodge** — re-enable a suppressed lint/type rule in the same spoke that clears it; no new `# noqa`.

## Master index

Populated during investigation. One row per spoke. ROI/Risk are the executor's dispatch cues; Status tracks the spoke through approve → execute → reviewed.

| ID | Title | Area | Effort | ROI | Risk | Suggested executor | Status |
|----|-------|------|--------|-----|------|--------------------|--------|
| [01](01-apple-music-dead-classifier.md) | ~~Delete dead AppleMusicErrorClassifier~~ | infrastructure | — | — | — | — | **Rejected** (user: deliberate groundwork for Apple integration) |
| [02](02-play-importer-pipeline.md) | Play-importer pipeline: typed params, single username resolution | infrastructure | L | high | med | Fable | Not Started |
| [03](03-play-resolver-decomposition.md) | Play-resolver decomposition & Last.fm chain flattening | infrastructure | M | med | med | Opus | Not Started |
| [04](04-matching-provider-loop-hoist.md) | Matching providers: hoist per-track loop | infrastructure | S | med | low | Opus | **Done (v0.8.14)** |
| [05](05-lastfm-conversions-boundary.md) | Last.fm conversions: validate at the boundary | infrastructure | S | med | low | Opus | **Done (v0.8.14)** |
| [06](06-base-connector-reflection.md) | BaseAPIConnector: replace get_playlist reflection | infrastructure | M | med | med | Opus | Not Started |
| [07](07-use-case-skeleton-collapse.md) | Collapse the five copy-pasted use-case skeletons | application | L | high | med | Fable | Not Started |
| [08](08-route-handler-conformance.md) | Route handlers: move strays behind execute_use_case | interface | M | med | med | Opus | Not Started |
| [09](09-timed-query-envelope.md) | Shared timed-query envelope for read use cases | application | S | med | low | Haiku | Not Started |
| [10](10-update-canonical-playlist-split.md) | update_canonical_playlist: push diff sub-algorithms down | application | M | med | med | Opus | Not Started |
| [11](11-executor-closure-flatten.md) | Workflow executor: flatten triple-nested closure stack | application | L | high | med-high | Fable | Not Started |
| [12](12-execution-strategies-dead-abstraction.md) | Delete dead execution-Strategy abstraction | domain | M | high | low-med | Opus | **Done (v0.8.13)** |
| [13](13-dead-config-purge.md) | Purge dead config: BatchConfig + MatchingConfig fields | domain+config | S-M | high | low | Opus | **Done (v0.8.13)** |
| [14](14-service-method-splits.md) | Split two oversized service methods along phase seams | application | M | med | med | Opus | Not Started |
| [15](15-backend-minor-batch.md) | Backend minor batch: dual_mode, play-history predicate, protocol surface | domain+application | S | low-med | low | Haiku | **Done (v0.8.14)** |
| [16](16-query-states-wrapper.md) | QueryStates wrapper + skeleton primitives (four-state ladder) | web | L | high | med | Fable | Not Started |
| [17](17-command-search-list.md) | CommandSearchList: de-fork track-search dialogs | web | M | med | med | Opus | Not Started |
| [18](18-dead-frontend-export.md) | Resolve dead export hasActiveFilters | web | XS | low | low | Haiku | **Done (v0.8.13)** |
| [19](19-page-decomposition.md) | Page decomposition along named seams (4 screens) | web | M-L | med | low-med | Opus | Not Started |
| [20](20-design-token-conformance.md) | Design-token conformance: SyncConfirmationDialog palette | web | XS-S | low-med | low | Haiku | **Done (v0.8.13)** |
| [21](21-track-connector-mapping-spec.md) | track/connector.py: mapping-spec object + batch-method decomposition | infrastructure | M | high | med | Opus | Not Started |
| [22](22-list-tracks-decomposition.md) | track/core.py: extract list_tracks filter builder | infrastructure | S-M | med | low-med | Opus | Not Started |
| [23](23-base-repo-mapper-split.md) | base_repo.py: split mapper machinery from repository base | infrastructure | S | med | low | Opus | Not Started |
| [24](24-db-models-aggregate-split.md) | db_models.py: split by aggregate (optional, honest ROI med-low) | infrastructure | M | med-low | low-med | Opus | Not Started |
| [25](25-cli-structural-pass.md) | CLI structural pass: ui renderer, helper param-objects, family split | interface | M | med | low-med | Opus | Not Started |
| [26](26-ratchet-closeout.md) | Ratchet closeout: flip suppressed rules as the sweep clears them | config/tooling | M | high | low | Opus | basedpyright step **done (v0.8.13)**; remainder scheduled v0.8.17 |

## Counter-proposals

_If Fable finds a better plan than v0.8.12's three-phase seed, it goes here for approval before execution starts._

> **Scheduled 2026-07-01:** the user approved the wave sequencing below and built it into the backlog as **[v0.8.13–v0.8.17](../v0.8.13-0.8.17.md)** (Wave 1 → v0.8.13, Wave 2 → v0.8.14, Wave 3 → v0.8.15, Wave 4 → v0.8.16, Close → v0.8.17; spoke 25 moved into Wave 2, spoke 24 optional in Wave 4). That file is the schedule; this hub stays the per-spoke spec + status ledger.

> **Window re-scope (2026-07-02, approved):** two doc-only investigation briefs join the window alongside the waves — the v0.9.x Pre-Work brief and the identity-resolution research pass (extended to artist identity + an omni-integration provider breadth scan). If Fable attention runs short they outrank Fable-executor spokes 02/07/16 (work orders written, Opus fallback documented); spoke 11 keeps its Fable slot longest. The v0.8.17 Fable review runs per-wave inside the window rather than as one post-sweep aggregate. **The ordered queue lives in the schedule file's [window execution order](../v0.8.13-0.8.17.md) — the source of truth for what to tackle next.**

**The three-story structure held up.** Two sequencing refinements for Story 2, as originally proposed:

1. **Flip the basedpyright promotion FIRST, not last.** The census found `basedpyright src/` at 0 errors/0 warnings — the promotion is free today. Executing spoke 26's step 1 as the sweep's first commit locks the type floor before any refactor, so every spoke is checked at the higher bar. (Story 3 as written defers all ratcheting to the end.)
2. **Suggested execution order** (cheap-confident first, riskiest last, conflicts sequenced):
   - *Wave 1 — dead code & free wins (Haiku/Opus, ~independent):* 13 (dead config) → 12 (dead Strategy) → 18 (dead export) → 20 (tokens) → basedpyright flip.
   - *Wave 2 — backend structure:* 04, 05, 15, 09, 23 (small, low-risk) → 07 → 08 → 10 (shared `_shared/` files: run 07 before 08/10) → 14 → 21, 22 → 02 → 03 (02 before 03, same subsystem).
   - *Wave 3 — frontend:* 16 → 17 → 19 (16 creates the primitives the others consume).
   - *Wave 4 — highest risk:* 11 (executor flatten, characterization tests first) → 06 (base-connector reflection) → optional 24, 25-item-3 (wide splits, user's call).
   - *Close:* 26 (ratchet remainder + full matrix + Fable review + version bump).

## Vulture census disposition (all 13 candidates accounted for)

| Candidate | Disposition |
|---|---|
| `command_validators.api_batch_size_validator` | Dead → deleted in spoke 07 |
| `settings truncation_limit` (BatchConfig) | Dead → deleted in spoke 13 |
| `execution_strategies.execute_with_strategy` | Dead → deleted in spoke 12 |
| `entities/playlist.is_resolved` (PlaylistEntry) | Semantic live, property bypassed → wired in spoke 15 |
| `entities/playlist.resolved_entries`, `.to_tracklist` | Test-kept-alive → resolved in spoke 15 |
| `repositories connector.get_by_connector_id` (×2: protocol+impl) | No callers → spoke 15 |
| `repositories playlist.create_links_batch` (×2: protocol+impl) | No callers → spoke 15 |
| `schemas/operation_runs.retryable` (×2) | **False positive** — API field read by the frontend; stays whitelisted |
| `schemas/playlists.is_resolved` | **False positive** — read by PlaylistDetail/PlaylistTrackEditor; stays whitelisted |

## Deferred / out-of-scope

_Unrelated debt discovered mid-sweep, parked for separate tracking (not fixed inline)._

- **Last.fm checkpoint tenancy naming** — `SyncCheckpoint(user_id=<lastfm username>, ...)` (`lastfm/play_importer.py:576`): the checkpoint's `user_id` field holds the *Last.fm account name*, not the mixd user id. Works today (single account per user) but is a multi-tenancy semantics question, not a refactor.
- **`architecture_version: "connector_plays_deferred_resolution"`** written into every persisted play's `context` JSON (`spotify/play_resolver.py:221`, `lastfm/play_resolver.py:115`) — archaeology baked into user data; removing it changes persisted rows, so it needs its own decision.
- **`.claude/rules/application-patterns.md` sanctions envelope repetition** — spoke 07 proposes amending one line; needs user sign-off on the wording.
- **Frontend minor items** (from sweep, below work-order threshold): mutation `invalidate → toast → close` helper (~6 dialogs); `ScheduleFailureBanner` vs `ScheduleFailuresBanner` rename; `NodeConfigPanel` `FieldInput` extraction; `EditorToolbar` `useWorkflowSave` extraction; `Tags.tsx` bespoke `tagMutationCallbacks` (justified by 422-in-onSuccess envelope).
- **Repo-wide raw-Tailwind-palette sweep** beyond spoke 20's three files — bigger scope decision.
- **`MatchFailureReason.NO_ISRC` has no producers** after spoke 04 removed the only emitters (the unreachable re-validation branches). Removing the enum member is a domain change (persisted/reported failure vocabulary) — decide separately, e.g. at the v0.8.17 vulture prune.
- **`added_at_dates` metadata has no production writer** (found in spoke 15): `metric_transforms.py` reads it (`sort_by_date`) and `track.py` types it, but the only writer was the test-kept-alive `Playlist.to_tracklist()` (now deleted); the workflow playlist source builds `track_sources` metadata instead. Either the playlist source should emit `added_at_dates` (feature gap: date-sorting playlist-sourced tracks by added-at) or the reader + typed keys are dead — needs its own decision.

## Healthy — audited and dispositioned leave-alone

So the closeout can prove every seed was examined, not just the ones that became spokes:

- **Connector `error_classifier` family** — `_shared` template + hook; spotify/lastfm/musicbrainz subclasses are exemplary (seed's "×5 duplication" already solved). **Apple Music tree (incl. its classifier) is user-protected: deliberate groundwork for the upcoming Apple integration — hands off in this sweep** (spoke 01 rejected).
- **Connector `matching_provider` family** — base template healthy; Last.fm's non-inheritance is documented LSP reasoning (loop-shell hoist only → spoke 04).
- **Connector `conversions` family** — per-service mapping, not duplication (Last.fm boundary fix only → spoke 05).
- **`inward_track_resolver` family** — three-step pipeline base + thin subclasses; genuinely well-factored.
- **Connector clients/operations/auth** (spotify 647/630/516/427, lastfm 583/321, musicbrainz 170) — systematic `_api_call` public/`_impl` pairs; no cross-tree duplication worth merging.
- **`listenbrainz/lookup.py`** — clean 90-line utility, purposeful.
- **`workflows/nodes/config_fields.py` (816)** — declarative UI-field registry, not logic; already extracts shared option tuples + builders.
- **Node catalog/transform-registry auto-registration** — reference example of the good dispatch pattern.
- **`application/services/scheduler.py` (635)** — large file, well-decomposed (~19 fns avg ~35 lines).
- **`connector_playlist_processing` vs `_sync` services** — similar names, distinct concerns; not duplicates.
- **`domain/matching/`** (algorithms, probabilistic, evaluation, text_normalization, play_dedup) — clean separation, no duplicate scoring.
- **`domain/playlist/diff_engine.py` LIS functions** — inherent algorithmic complexity, well-named.
- **`domain/transforms/` structure** — consistent pure-function factories; only the `dual_mode` nit (spoke 15).
- **`config/settings.py` groups** — all live except `batch` (spoke 13); organization sound.
- **`persistence/repositories/playlist/core.py` (1,155)** — big but already well-decomposed internally: the save path is a documented pipeline of private helpers (`_save_new_tracks` → `_manage_playlist_tracks` → `_manage_connector_mappings`) with load-bearing docstrings (natural-identity probe, entry-identity matching). Further splitting is churn, not cleanup.
- **`base_repo.py` upsert machinery** (two-phase upsert, savepoint bulk-upsert, overloads) — dense but framework-quality; spoke 23 moves mappers out, machinery itself untouched.
- **`track/core.py` merge family + `save_track`** — dense-but-cohesive with NamedTuple count reporting; only `list_tracks` earns work (spoke 22).
- **Frontend SSE hook stack** (`useSSEConnection` → `useOperationSSE` → `useOperationProgress`/`useWorkflowSSE`) — textbook layering; do not touch.
- **Adaptive polling + `useScheduleController`** — anti-drift patterns to emulate.
- **`lib/toasts.ts` + `query-client.ts`** — centralized, typed, DRY.
- **`useOperationProgress.ts` (370) + `editor-store.ts` (354)** — large but cohesive single-responsibility units.
- **`PlaylistTrackEditor.tsx` (582, v0.8.11)** — fresh, cohesive: cache helpers + desktop-row/mobile-card variants (inherent duality) + dnd/optimistic-undo core. Leave alone.
- **`pages/settings/Sync.tsx` (572)** — the four import/export sections already share `OperationCard` + `makeOperationCallbacks`; the parallel-screen skeleton problem is solved here. Leave alone (its ladder/skeleton → spoke 16).
