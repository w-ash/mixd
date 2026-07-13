# Changelog

All notable changes to Mixd are documented here — newest first, one dated entry per ship.
Each entry leads with the user benefit; technical detail follows; the deep story lives in the
linked backlog version file. Versioning follows mixd's four-segment
`major.minor.feature.revision` scheme (`.claude/rules/version-management.md`), not strict
SemVer. Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.9.3] — 2026-07-12

**Point your own agent at mixd.** The tool surface the in-app assistant drives is now an MCP server, so any MCP-aware client — Claude Desktop, Cursor, Claude Code — can read from and act on your library over a local stdio connection. "Find my starred tracks from last year that aren't on my Friday playlist and add them" runs from your desktop agent, against the same parity-complete registry, with the same never-silent write confirmation.

- **`mixd mcp serve` / `mixd mcp install`** — a low-level stdio MCP server built from the shared `TOOLS` registry (`src/interface/mcp/server.py`); `install` prints the client config snippet (Claude Desktop / Cursor / Claude Code), pinning `MIXD_USER_ID` for non-default users so an agent acts on the right tenant's library. Identity resolves exactly as every CLI command does (`MIXD_USER_ID` → `DEFAULT_USER_ID`) — no new credential surface.
- **26 tools exposed, one shared classifier** — read + synchronous-write tools are exposed; the 3 `agentic` tools (sandbox, subagent, tool search) stay chat-only (an MCP client brings its own loop) and the 5 long-running writes are gated (see below). `src/interface/mcp/exposure.py` is the single classifier both the server and the generated capability matrix consume, so the doc can't drift from what's actually exposed. Annotations (`readOnlyHint`/`destructiveHint`/…) are derived from each tool's `kind`.
- **In-band two-phase confirmation** — every write previews first: a call without `confirm=true` returns `{ status: "needs_confirmation", confirm_token, preview }`; a second call with the token and unchanged arguments commits. It reuses the chat `pending_action_store` + `execute_confirmed_action` (one preview renderer, two surfaces); expired/unknown tokens re-preview, args drift is rejected, and tokens are single-use with a 5-minute TTL.
- **stdout stays pure JSON-RPC** — structlog routes to stderr before the first import-time log for any `mixd mcp` command (early argv guard + a `console_stream` kwarg), so protocol output is never corrupted; `mcp install --print` likewise emits pure JSON for piping.
- **Long-running ops deferred, not forgotten** — imports, workflow runs, and playlist syncs need the MCP Tasks extension (and an `OperationLauncher` decoupled from FastAPI); they show `chat-only (pending Tasks)` in the matrix and flip to exposed when the post-2026-07-28 stable SDK lands. The SDK is pinned to `mcp==2.0.0b1` with a re-verify checkpoint baked into `pyproject.toml`.

→ [details](docs/backlog/v0.9.x.md#v093-mcp-server-mixd-as-a-tool-surface)

## [0.9.2] — 2026-07-12

**Ask for batch answers and deep analysis without watching the assistant grind.** With full parity the assistant could already do everything — but one visible tool call at a time, every intermediate result flowing through its context. v0.9.2 makes it efficient and long-horizon capable: "how many of my 2024 discoveries did I star, by month?" runs as one sandbox script over the read tools instead of hundreds of round trips, and "compare my listening this spring vs last spring and tell me what changed" is delegated to a research subagent that reads widely and hands back a single summary — the working thread stays fast and on-topic.

- **Code-execution sandbox + programmatic tool calling** — read tools are sandbox-callable (`allowed_callers` stamped in `build_tools`, reads only — mutations never), so the model writes Python that aggregates over the library and only the aggregate re-enters the conversation. A batch mutation composed in the sandbox still commits through one confirmable write proposal carrying the full change list. Gated by `CHAT__ENABLE_CODE_EXECUTION` (default on; disabling degrades cleanly to direct calls). A separate sandbox-round backstop (`max_turns × 5`) keeps a runaway code loop from starving the model-turn budget.
- **Research subagent (`delegate_analysis`)** — a fresh read-only loop at low effort with a bounded turn budget (12); its entire output is one dense summary string in the parent conversation, so a deep investigation costs the main context one tool result instead of a nested transcript. Read-only by construction (`build_subagent_tools()` is the read slice only — no writes, no sandbox, no nested delegation).
- **Tool search + deferred loading** — at 34 tools the registry flipped **deferred-first**: 7 tools load upfront (4 hot reads + 3 agentic), the rest are discovered on demand via a BM25 `tool_search` tool, with a system-prompt "search before you claim you can't" nudge. Discovered schemas append rather than swap, preserving the tools/system prompt cache.
- **Page-contextual tool routing** — the web client sends the coarse UI section you're on and its tools promote into the loaded set (rule-based, ≤3/page to hold the ~10-tool accuracy ceiling). Promoted tools ride the *uncached tail* so the cached core prefix is page-invariant — navigating between pages never re-pays for the tool schemas. Strictly additive: an unrouted page degrades to the static core + search. The restructure also fixed a latent bug where the cache breakpoint had been landing on a `{type,name}` server-tool block that rejects `cache_control`.
- **Context management + effort control** — the Quick/Standard/Thorough selector (low/high/xhigh, persisted locally, default Standard) rides every request; the subagent always runs at low effort regardless of the parent's selection.

→ [details](docs/backlog/v0.9.x.md#v092-agentic-depth)

## [0.9.1] — 2026-07-11

**Ask the assistant to do anything you can do in the app.** v0.9.0 could build workflows and little else; v0.9.1 makes the parity contract real — every read a user can perform in the web UI or CLI is a chat tool, every mutation is a two-phase-confirmed tool, long-running imports/syncs/runs stream their progress into the conversation, and whatever the assistant does lands in the same run log a human uses. "Which starred tracks haven't I played in six months? Tag them `context:revival` and add them to my Revival playlist" is now one conversation, end to end.

- **31 tools, parity closed** — the shared registry grew from 6 to 31 tools (14 read / 17 write). Every one of mixd's 82 application use cases is now either covered by a chat tool or explicitly excluded (blacklisted / mechanically-excluded / internal), enforced by CI: `NOT_YET_COVERED` is empty and the tripwire test asserts it stays that way. A generated `docs/web-ui/capability-matrix.md` (freshness-tested) is the audit.
- **Consolidated by intent, not use case** — tools are shaped around what a user wants ("manage tags", "query library"), 74 use cases collapsed to 31 tools via `operation`/`scope`/`view` discriminators, so the model reliably picks the right one.
- **Every mutation is a proposal** — write tools return a before/after preview; the confirmation card shows exactly what changes, with a distinct destructive-action banner (delete / merge / unlink) before anything commits.
- **Long-running operations in chat** — imports, playlist syncs, bulk assignment applies, and workflow runs launch from chat with the same live progress card the Sync page uses (reusing the existing operations SSE), and land in the run log attributed to the assistant (new `operation_runs.initiated_by`, migration `037`, Assistant badge in the import history).
- **Prompt-injection defense** — user-library free text (track titles, playlist/tag names) reaches the model wrapped in `<user_data>` tags and the frontend raw; a leaked title can't smuggle instructions.

→ [details](docs/backlog/v0.9.x.md#v091-full-capability-parity-in-app)

## [0.9.0.1] — 2026-07-11

**The AI assistant is now yours alone — bring your own Anthropic key, and until you do, it simply isn't there.** v0.9.0 mounted the chat surface for everyone behind one shared deployment key; that broke mixd's multi-user principle (one person's key and spend serving all) and showed a chat affordance that only errored when used. Now the assistant is per-user and opt-in: connect a key in Settings → Assistant (or `mixd assistant connect`), and the whole surface — Cmd+K, edge tab, side panel, mobile `/chat` and the "Ask" tab — appears only for you. No key, no assistant; no shared key, no broken button.

- **Per-user, encrypted, write-only credential** — the key is stored encrypted in the existing per-user token store (RLS-scoped, same discipline as connector tokens) and is never echoed back in any response.
- **Single resolution rule** — `resolve_chat_credential()` is the one place precedence lives (your key → optional single-tenant server fallback → none); `get_llm_client` and the `/assistant/status` capability signal both derive from it. A stored key that fails to decrypt fails closed rather than silently falling back to the shared server key.
- **Validated at connect, not first use** — a pasted key is checked with a minimal live completion, so a key with no billing is rejected up front instead of 500-ing on the first real message.
- **Frontend gated on a `useChatAvailable()` signal** — every assistant mount point consumes it; a desktop `/chat` deep-link waits for the gate to resolve before deciding, and cached Anthropic clients are closed on API shutdown.

→ [details](docs/backlog/v0.9.x.md#post-deploy-revisions)

## [0.9.0] — 2026-07-11

**Describe a playlist in plain English and get a real, editable workflow.** A persistent right-panel assistant (Cmd+K, edge tab when collapsed, full-screen `/chat` on mobile) turns "build me a chill weekend playlist" or "upbeat tracks I haven't played in 6 months" into a valid `WorkflowDef` — rendered in the same read-only graph the app uses everywhere else, refined in conversation, and saved to your workflow list on approval. This is the front door for anyone who'd never build a workflow from the node catalog by hand.

- **Natural-language workflow generation** — the `generate_workflow_def` tool's input schema is derived mechanically from the node catalog (`config_fields.py`), so the model proposes DAGs that reference real nodes with correct parameters; validation failures return as structured tool errors the model self-corrects from in the same turn. No few-shot templates.
- **In-graph preview + two-phase save** — a `WorkflowPreviewCard` embeds the existing `WorkflowGraph`; the assistant proposes `save_workflow` in the same turn, and nothing persists until you click Save (create) or Save changes (update an open workflow). Refinements replace the preview in place; superseded drafts collapse.
- **Enriched domain primer** — the system prompt carries the full node taxonomy, connector/preference/tag model, and per-user library statistics, split so the stable primer caches while volatile stats and current-workflow context trail it uncached.
- **Parity-classified tool registry** — every capability is a `ToolSpec` (read / write / agentic) that thinly adapts an application use case; a CI test asserts every one of the 81 use cases is classified, so "anything a human can do, the agent can do" can't silently rot. This registry is the foundation v0.9.1 fills, v0.9.2 deepens, and v0.9.3 exposes over MCP.
- **Feedback loop** — thumbs up/down (with an optional note) on any generated draft persist to a new `chat_feedback` table, so prompt and tool-description iteration is grounded in real accept/reject signal.
- **Under the hood** — 7-event SSE streaming, in-memory rate limiting (20/60s), two-phase confirmation store (5-min TTL, owner-checked), and untrusted-content tag-stripping, all ported from couplefins' production-tested v1.8.x agentic chat.

→ [details](docs/backlog/v0.9.x.md#v090-workflow-assistant-agentic-foundation)

## [0.8.18.3] — 2026-07-10

**CI stops leaking Neon database branches — the orphan pile-up that maxed the project's monthly limit in a week is fixed and purged.** Every direct push to main was creating a `ci/pr-<sha>` branch (the `github.event.number || github.sha` fallback) that no cleanup path ever deleted — `neon-cleanup.yml` only fires on PR close. 40 orphans had accumulated since April.

- **One-time purge** — all 40 orphaned `ci/pr-*` branches deleted; only `production` remains.
- **Explicit delete for push runs** — `ci.yml` now ends push-triggered runs with `delete-branch-action@v3` (`if: always()`, gated on the create step having succeeded), so the branch is removed even when tests fail.
- **TTL backstop** — every CI branch is created with `expires_at` (1 day for push, 7 days for PR) via `create-branch-action@v6`, so Neon auto-deletes anything orphaned by cancelled runs, runner crashes, or missed close events. PR-close deletion via `neon-cleanup.yml` is unchanged.

→ [details](docs/backlog/v0.8.18.md#post-deploy-revisions)

## [0.8.18.2] — 2026-07-09

**A dependency-freshness sweep — mostly internal currency, with one real client fix: filtering by multiple tags now serializes correctly.** Triaging the post-deploy Dependabot banner (42 alerts) showed ~41 were already remediated in the freshly-pushed lockfiles — the stale remote just hadn't re-scanned — and surfaced a handful of genuinely-behind direct deps worth taking while current.

- **Multi-tag filter fix** — regenerating the API client under orval 8.20 fixed array query-param serialization: `tag` filters now explode to `?tag=a&tag=b` (matching FastAPI's `list[str]`) instead of comma-joining, which the old client got wrong.
- **Backend** — uvicorn 0.49→0.51 (prod ASGI server), ruff 0.15.21, croniter, coverage, and transitive bumps via `uv lock --upgrade` (pydantic-core held to pydantic's pin). Full backend suite + basedpyright clean.
- **Frontend** — @xyflow/react, radix-ui, react-router, lucide-react, and the dev toolchain (biome, vite, vitest, knip, msw, shadcn) to latest-in-range.
- **TypeScript 6 → 7** — the native compiler generation, adopted with zero type errors across all 272 files.
- **Security floor** — `hono` floored to `>=4.12.25`. The residual `better-auth` Dependabot alerts stay open: forcing the patched line breaks the build because `@neondatabase/auth@0.4.2-beta` pins 1.4.18 and imports an export removed in 1.6.x. Those advisories are server-side (Neon-hosted), unreachable in our client usage; blocked on a Neon SDK update.

→ [details](docs/backlog/v0.8.18.md#post-deploy-revisions)

## [0.8.18.1] — 2026-07-04

**A follow-up review of Identity Integrity closed four latent defects before they could bite in prod.** No new user-facing surface — this is correctness hardening on the v0.8.18 matching layer: the Last.fm identity fold (migration 035) no longer aborts when a survivor's current identifier collides with another group's target; Last.fm's untrusted MBIDs can no longer merge two distinct recordings into one track; match reviews raised by non-`default` users route to their real owner instead of vanishing onto the shared default; and the identifier a track *displays* now always matches the mapping the system *promotes* to primary.

- **Migration 035 collision-safe rename** — a two-phase park-then-assign pass parks every to-be-renamed survivor at a temporary identifier before assigning finals, so a rename cycle can't trip the `connector_tracks` unique constraint and roll back the whole migration. The fold key is recomputed in Python (`strip().lower()`) to match the runtime mint, since SQL `btrim` leaves the Unicode whitespace (tab / newline / NBSP) the mint strips.
- **Last.fm MBID quarantine** — `track.getInfo`'s track-level MBID (untrusted per LB-431) no longer lands in the `musicbrainz` identity slot that feeds `save_track`'s `uq_tracks_user_mbid` merge key; it survives only in the enrichment log. The raw-spelling alias mapping is minted without auto-promotion so it can't demote the corrected import as the track's provenance.
- **Per-user review routing** — `create_review`'s upsert now keys on `user_id` too, matching the `uq_match_reviews_user_track_connector` constraint; non-`default` tenants' reviews were silently taking the RLS server-default and never surfacing to their owner.
- **One promotion policy** — display (`TrackMapper`) and promotion (`ensure_primary_for_connector`) share a single `(confidence desc, id asc)` total order; an integration test now fails if the two ever diverge on an equal-confidence tie.
- **DRY / internals** — a `compute_duration_diff_ms` domain helper replaces the ISRC duration-diff guard duplicated across 5 call sites; the drift panel reuses `list_pending_reviews`'s total instead of issuing a second identical `count_pending()` query.

→ [details](docs/backlog/v0.8.18.md#post-deploy-revisions)

## [0.8.18] — 2026-07-03

**Your library's identity data stops silently corrupting itself.** Identity Integrity hardens the matching layer in place: confidence scores still mean "how likely is this the right track" months after import, ISRC-reuse remasters route to review instead of overwriting your metadata, Last.fm plays/loves stop fragmenting across duplicate rows, and `mixd stats --matching` now surfaces resolution-health drift signals. **Confidence integrity (C1–C5):** re-imports record a `last_seen_at` freshness stamp (migration 034) instead of promoting 70-confidence guesses to a permanent 100; the re-resolution fast path returns stored provenance, not a synthetic 90; Last.fm's stale MBIDs are demoted to `artist_title` scoring; MISSING title/artist comparison levels + an ISRC-grade evaluation gate stop empty-metadata auto-accepts. **ISRC merge guards (epic 3):** `save_track`, Spotify inward dedup, and cross-discovery all reuse the engine's own `assess_isrc_match_reliability` suspect check — suspect collisions queue a review and mint a distinct ISRC-stripped canonical rather than clobbering; the Last.fm inward flow was rewritten reuse-before-create (cross-discovery now returns a `DiscoveryOutcome`), so one canonical holds both connectors' mappings and the dangling/orphan-canonical defect is gone. **Last.fm identity unification (epic 4):** all four identifier mint sites key on `make_lastfm_identifier` of Last.fm-corrected names (new `track.getCorrection` fallback; a raw-spelling alias mapping for fast re-import); migration 035 folds existing URL/MBID/case variants into the normalized scheme, RLS-bracketed and reattaching mappings, reviews, and playlist pointers. **Healing correctness (C6):** the denormalized `spotify_id` column syncs only for primary mappings, and one highest-confidence promotion policy replaces two contradictory ones. **Drift metrics (epic 6):** a confidence-band distribution + Drift Signals panel (fallback share, review inflow/depth/age, isrc_suspect pending, evidence divergence, stale denorm IDs) in `stats --matching` and `GET /stats/matching`, data-integrity check #7, and run-attached resolution counters in `operation_runs.counts`. A 10-test characterization net pinned every fix, so each landed as an assertion flip rather than a silent behavior change. Full suite green (3466 backend incl. both migration tests / 727 frontend).
→ [full stories](docs/backlog/v0.8.18.md)

## [0.8.17.2] — 2026-07-02

CI reliability fix — a green pipeline means green again. Fixed a Python-CI failure surfaced by the v0.8.17.1 push: `test_cadence_requires_at` asserts a literal substring of a Typer CLI error message; GitHub Actions' non-interactive runner truncates that rendered output via ambient terminal-width detection, reproducing deterministically on CI (2/2) but never locally (5+ attempts, including matched worker count and stripped credentials) — a rendering/assertion fragility, not a functional defect (the CLI validation itself is correct, exit code 2). Fix: `tests/unit/interface/cli/conftest.py` pins `COLUMNS=200`/`LINES=50` for the directory, removing the dependency on ambient detection.

## [0.8.17.1] — 2026-07-02

The running app now reports the version it actually is. The v0.8.17 tag was cut from a commit that predated its own version bump (a `git commit` invoked through a pipe masked a pre-commit hook failure, so the tag/release/deploy fired against `main` while `pyproject.toml` still read `0.8.16`). Purely a version-metadata + docs fix — the deployed *code* was already the complete, correct v0.8.17 closeout; this revision makes the running app's self-reported version, the OpenAPI schema, and the generated web client match the tag. Rides along: the identity-resolution research's roadmap sequencing (v0.8.18 Identity Integrity, v1.0.0 Mapping Supersession, v1.0.4 Data Sovereignty).

## [0.8.17] — 2026-07-02

**Sweep closeout: ratchet, review & dependency audit** — the quality gates the project depends on (visual regression, dead-code, ratchet baselines) are all live and honest again; closes the v0.8.12–v0.8.17 arc. The **ratchet re-census** (spoke 26) found no suppressed PLR rule at zero, so none flipped — the honest outcome, each re-documented dated in `pyproject.toml` (PLR1702's survivors sit in `apply_playlist_assignments` + the user-protected Apple Music tree; PLR0911's five are guard/classification chains where every return is a distinct log event or HTTP status; PLR0913/0917 remain the user's call, options in the spoke). The **noqa whodunit** traced the 14-vs-13 discrepancy to v0.8.7 (`d2d3a179`) and fixed it by code (`_emitter` rename, suppression deleted, count back to 13); vulture went from 12 findings to clean (8 dead `operation_summary` properties deleted — the 7 use-case copies never had a consumer, diff_engine's died with spoke 12; `retryable` whitelisted as frontend-read; `NO_ISRC`/`added_at_dates` whitelisted as dated parked decisions); ratchet baselines lowered (whitelist 79→63, pyright-ignore 20→18). The **visual gate revival** falsified its own premise: CI's e2e job had been red since 06-01 on image↔package skew (`v1.60.0-noble` vs Playwright 1.61.1 — browsers never launched); with the pin fixed, 16/18 baselines were still valid and only settings-sync ×2 regenerated (legitimate v0.8.4 schedule-card growth). Docker-pinned e2e + vulture + `check_ratchet.sh` joined CLAUDE.md's version-bump bar; two latent bugs fixed in the regen procedure (`CI=true`; pnpm 11.5 drops `--update-snapshots` after `--`). The **dependency audit** ([findings](docs/backlog/dependency-audit-findings.md)) found the tree healthy — 0 unused runtime deps, versions current — with 10 work orders (headliners: undeclared direct `starlette` import, unwired `interrogate`/`bandit`, a hand-rolled retry in `base_repo.py`, the import-linter gap — v0.8.12's "already in place" claim was false). Closeout diff reviewed (engineer + QA agents): zero violations. Full matrix green (3386 backend / 727 frontend / 18/18 e2e in the pinned image). The **identity-resolution research pass** shipped alongside: design-space + governance memos, PDR-001/002, and the new `docs/decisions/` PDR system.
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0817-sweep-closeout--ratchet--review)

## [0.8.16] — 2026-07-02

No user-facing changes — the sweep's two riskiest internal refactors landed safely, with the workflow engine's behavior pinned by new characterization tests first. **Sweep Wave 4: high-risk decompositions.** **Spoke 11** (Fable main thread) flattened the workflow executor's triple-nested closure stack — but characterization tests landed FIRST: 6 new tests pinning the observer event sequence (the SSE contract — previously unpinned; degrade emits `on_node_failed` but never `on_node_completed`), the timeout firing + node-identity rewrap path, and the first-ever real `run_workflow` executions. The flatten itself: a mutable `_RunState` (exactly the former closure captures; `parameters` by reference, `_shutdown_requested` deliberately left a live module global) with `_run_task_inner`/`_run_node_lifecycle`/`_build_and_execute_workflow` promoted to module level; AST nesting 4→2; move fidelity verified by AST-normalized diff (only intended deltas); all prior engine tests pass byte-unmodified. **Spoke 06** deleted the base-connector `get_playlist` reflection — exploration showed it was production-dead (Spotify overrides directly; no `get_<name>_playlist` methods exist), so migration became deletion — plus `get_connector_config` entirely (zero production callers, user-approved) and the mirrored conditional-`on_page` fork in the sync service; mock assertions retargeted stricter (`on_page=None` explicit). **Spoke 24 skipped** (user-approved): both work-order premises falsified — 72 import sites not ~30, and all 31 relationships are typed `Mapped[DBX]` annotations (not string targets), making the split a circular-import surface for navigation-only gain. Wave review: mechanical AST fidelity check + independent engineer review — zero findings. Full matrix green (3386 backend / frontend check+build; pre-existing noqa-baseline discrepancy unchanged, whodunit scheduled in the closeout).
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0816-sweep-wave-4--high-risk-decompositions)

## [0.8.15] — 2026-07-02

Every screen's loading, error, and empty states now look and behave consistently. **Sweep Wave 3: frontend structure** — the frontend's whole structural-debt inventory in one deploy: three behavior-preserving spokes, one commit each, strictly sequenced 16 → 17 → 19. **Spoke 16** introduced the shared `QueryStates` wrapper (canonical loading → error → empty → success; discrete `loading` flag passed verbatim because `isLoading`/`isPending` diverge on disabled queries) + four skeleton primitives (`ListRowsSkeleton`/`BlocksSkeleton`/`CardGridSkeleton`/`DetailHeaderSkeleton`, bar dimensions as data for per-page pixel fidelity), migrating 9 screens off hand-rolled ladders, deleting 7 local skeletons, and normalizing Tags/ImportHistory error states onto `QueryErrorState` (new static `description` override keeps their copy verbatim). **Spoke 17** de-forked the cmdk track-search scaffold into `CommandSearchList` (owns search state, query, threshold, and the 3-state ladder) + `TrackResultRow`; `TrackSearchCombobox` deleted, `AddTracksDialog` rebuilt on the shell with consumer-owned checkbox/badge slots, `TagAutocomplete` deliberately skipped (shares only the outer scaffold). One approved visible copy change: "Searching..." → "Searching…". **Spoke 19** decomposed the oversized screens along the audited seams into 13 page-scoped component files — Dashboard 552→241, TrackDetail 554→306 (+`lib/match-methods.ts`), WorkflowRunDetail 511→210, PlaylistDetail 937→186, and Tags' three ConfirmationDialogs collapsed to one mode-keyed instance (copy byte-identical); Library + ConnectorPlaylistPickerDialog skipped (no clean seam). The aggregate wave diff was Fable-reviewed with every moved symbol mechanically diffed against its pre-wave original — zero findings. Full matrix green (3384 backend / 727 frontend + 96-state playlist-detail visual audit). Pre-existing visual-baseline staleness vs the freshly-installed Chrome 149 logged in the hub's Deferred list (failing set identical pre/post wave — not wave-caused).
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0815-sweep-wave-3--frontend-structure)

## [0.8.14] — 2026-07-02

No user-facing changes — the backend's structural-debt inventory cleared in one deploy. **Sweep Wave 2: backend structure**, the bulk of the Fable Sweep: fourteen behavior-preserving spokes, one commit each, full matrix green at every step. The five use-case skeletons collapsed into `_shared` helpers (spoke 07); the six stray route handlers moved back behind `execute_use_case` (08); `update_canonical_playlist`'s diff sub-algorithms pushed down to domain (10); the play-importer pipeline + resolver decomposed with typed params and single-source username resolution (02/03); `ConnectorMappingSpec` replaced the mapping 7-tuple (21); the `list_tracks` filter builder + keyset pager extracted (22); mapper machinery split out of `base_repo.py` (23); plus the matching-loop hoist (04), Last.fm boundary validation (05), timed-query envelope (09), backend minor batch (15), service-method splits (14), and CLI structural pass (25). This ship also carries a **dependency refresh** landed ahead of the v0.8.15 frontend wave — react-router 7→8, cryptography 48→49, fastapi 0.139, Starlette 1.3, plus frontend/backend bumps (four <24h-old releases left to pnpm's release-age cooldown) — and schedules a **dependency audit** into the v0.8.17 closeout. Full matrix green (3322 backend / 699 frontend).
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0814-sweep-wave-2--backend-structure)

## [0.8.13] — 2026-07-02

No user-facing changes, one latent UI bug fixed for free — dead code purged and the type floor raised. **Sweep Wave 1: dead code & free wins**, the first execution wave of the Fable Sweep (v0.8.12 audit). Five behavior-preserving commits, each with a `git grep` gate: the **type floor** locked first (8 basedpyright rules — `reportUnknown*` ×5, `reportMissingTypeArgument`, `reportImplicitOverride`, `reportDeprecated` — flipped `warning`→`error` after a fresh 0-warning verify), then four spokes. **Spoke 12** collapsed the dead execution-Strategy abstraction (Protocol + factory + `CanonicalExecutionStrategy` + `execute_with_strategy` + `ExecutionPlan`, ~75% of the file) to a single pure `plan_api_operations()`; the one live push path is unchanged (API-path integration tests green). **Spoke 13** purged `BatchConfig` + 14 pre-Fellegi-Sunter `MatchingConfig` fields across all four surfaces (domain config / factory / settings / `vulture_whitelist.py`); type-constraint tests were re-pointed to live `ConfidenceScore` fields, not deleted. **Spoke 18** deleted the dead `hasActiveFilters` export (Library already uses the tested `countActiveFilters`). **Spoke 20** added `status-success/error/warning/info` severity tokens and replaced raw Tailwind palette in three shared components — which also repaired a latent phantom `bg-status-error/10` (an undefined token Tailwind v4 was silently dropping) in `MergeTrackDialog`/`UnlinkMappingDialog` with zero edits to them. Full matrix green (3350 backend / 699 frontend).
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0813-sweep-wave-1--dead-code--free-wins)

## [0.8.12] — 2026-07-01

**The Fable Sweep — audit & work orders** (docs-only, no deploy artifact): a full structural audit of the codebase producing a working hub + 26 self-contained refactor work orders, executed as v0.8.13–v0.8.17.
→ [details](docs/backlog/v0.8.12.md#v0812-the-fable-sweep--structural-hardening)

## [0.8.11] — 2026-06-30

**Manual playlist track editing**, the closing slice of the v0.8.x cycle: add, remove, and reorder a canonical playlist's tracks directly from its detail page, instead of round-tripping through connector sync or a workflow. Three frozen-Command use cases (`AddPlaylistTracks` / `RemovePlaylistEntries` / `ReorderPlaylistEntries`) + REST endpoints + CLI parity (`mixd playlist add-tracks` / `remove-tracks` / `reorder`) sit on the entry-identity threading already shipped in v0.8.7 (`PlaylistEntry.id`, `eq=False`), so the only load-bearing schema change was finally *exposing* `PlaylistEntrySchema.id`. The web surface is a **dnd-kit** sortable `PlaylistTrackEditor` (keyboard reorder + screen-reader announcements) + an `AddTracksDialog` multi-select search modal; **remove is optimistic with a deferred-commit "Undo" snackbar** — the `DELETE` waits out the snackbar window, so an undone removal never reaches the server and keeps the entry's `added_at`/position (re-truing flow 3.6 away from its old confirm-dialog spec). Manual add deliberately allows duplicates without disturbing the workflow-append dedupe path.
→ [details](docs/backlog/v0.8.11.md#v0811-manual-playlist-track-editing)

## [0.8.10] — 2026-06-29

Editor polish + trustworthy play-history config in one ship (it **absorbed the play-history-config work originally scoped as a separate feature** — the v0.8.9-review follow-ons). Four stories: (1) a typed **entry-intent state machine** (`load`/`seed`/`blank`, in `editor-entry.ts` + `useEditorEntry`) replaces the implicit `{ imported: true }` reset chokepoint so the navigation-surviving editor store is predictable on every entry — a fresh "New Workflow" never shows a stale draft (and a latent web-suite "1 error" was traced + fixed along the way: a partial recovery snapshot crashing `useWorkflowSSE`); (2) **browse-to-link** retires paste-an-ID on Playlist Detail by reusing the existing import picker in a new single-select `mode` — no new backend (the browse method + endpoint shipped in v0.8.7–v0.8.8); (3) **config-aware validation** — `filter.by_metric`/`sorter.by_metric` check what the upstream `play_history` enricher is *configured* to emit, not its capability, so the editor's green check stops certifying empty-result workflows; plus a `period_days`-is-inert warning (which revealed + cleaned an inert `period_days` in all 9 play-history templates); (4) a **breaking day-window rename** — `min/max_days_back` → `not_played_in_days`/`played_within_days` — across code + seed JSON + a first-of-its-kind JSONB key-rewrite migration (033) over the three `WorkflowDef` columns. **Sub-flows/node-grouping was cut** as the wrong solution to a real problem (40+ node navigation) — recorded in [unscheduled.md](docs/backlog/unscheduled.md) for a lighter approach.
→ [details](docs/backlog/v0.8.9-0.8.10.md#v0810-editor-polish--trustworthy-play-history-config)

## [0.8.9] — 2026-06-28

**Workflow templates & import/export** — curated template gallery content plus workflow import/export, on the clone-on-use plumbing that shipped early as 0.7.8.20.
→ [details](docs/backlog/v0.8.9-0.8.10.md#v089-workflow-templates--importexport)

## [0.8.7 – 0.8.8] — 2026-06-25

**Import/sync reconciliation**, the correctness epic that fixed a *very broken* Spotify import + sync. One `PlaylistReconciliationEngine` now fetches the real remote fresh and diffs at the connector-identifier level — killing the self-join that silently dropped tracks, made push a no-op, and left the destructive guard dead. Unmatched tracks survive as first-class **unresolved** rows, destructive syncs are gated behind a real `confirm_token` 409 round-trip, and every run leaves a durable `OperationRun` audit row. **v0.8.7** shipped the engine + REST + CLI (`import-spotify` / `sync` / `sync-preview` / `repair`, per-item-atomic batches, position-aware duplicate removal on push). **v0.8.8** brought it to the web: a headless `OperationsProvider` that surfaces overnight failures + an "N running" sidebar badge, **Retry failed only** (server-reconstructed from the audit row via a pure `OperationRun.is_retryable`), the destructive-sync confirm dialog, additive imports with honest per-playlist progress + unresolved bulk-repair, and one `DirectionChooser` direction vocabulary. A web-robustness pass rode along in the same ship — SSE stream-end REST reconcile, toast-ledger dedup, render-pure recovery gate, and auth-gated/non-background polling extracted to a shared `useAdaptivePollingList`.
→ [details](docs/backlog/v0.8.7-0.8.8.md#v087-importsync-reliability-backend--cli)

## [0.8.6] — 2026-06-18

Sync results are now reported honestly — a partial push errors instead of silently claiming success, and tracks with no match on the destination are surfaced instead of vanishing. **Cycle hardening & cleanup** from the 2026-06 design-debt review: split the 2,062-line `repositories/interfaces.py` into 15 per-aggregate protocol modules; extended the `lazy="raise_on_sql"` eager-load guard across the whole ORM graph (eager-coverage audit + `passive_deletes` belt-and-braces, every relationship now carries an explicit `lazy=`); collapsed the `update_connector_playlist` verification ceremony and `base_repo` speculative periphery. Connector push now reports honestly via `PlaylistOpsOutcome.fully_applied` — a partial push routes to ERROR instead of a silent SYNCED, and canonical tracks with **no match on the destination** surface as an "unmatched" count in the CLI sync output and a **gold tooltip chip** on the web Playlist Detail (persisted via `last_sync_tracks_unmatched`, migration `029`). A code-review pass also caught a suppressed-error ADD false-success and an orphaned `@db_operation("delete")` decorator. Two rule carve-outs reconciled (irreducible SQLAlchemy-reflection suppressions; the interface→infra OAuth-access exception).
→ [details](docs/backlog/v0.8.5-0.8.6.md#v086-cycle-hardening--cleanup)

## [0.8.5] — 2026-06-16

**Operation & surface reliability** (design-debt review): SSE-seam lifecycle inversion + a data-loss fix for stranded connector plays (with a dry-run backfill script), server-safe OAuth tokens, a Last.fm cross-tenant fix, and 409 pre-flight guards.
→ [details](docs/backlog/v0.8.5-0.8.6.md#v085-operation--surface-reliability)

## [0.8.4] — 2026-06-09

Background **sync scheduling** + proactive **failure surfaces**, closing the "sync overnight → rebuild in the morning" loop. Daily/weekly schedules for the three sync targets (`lastfm:plays`, `spotify:likes`, `lastfm:likes`) live on their existing Settings›Sync cards via a shared `ScheduleCard` + `useScheduleController` (the bespoke `WorkflowScheduleCard` was refactored onto the same pair — zero new backend; the v0.8.2/8.3 engine already covered it). Two discovery-without-checking surfaces ride the already-fetched `GET /schedules`: a dashboard aggregate `ScheduleFailuresBanner` and an amber "Failing" marker on each workflow row, both self-clearing on the scheduler's success reset (one shared `AlertBanner` primitive behind both the per-schedule and aggregate banners). Also folded in: a human-facing per-workflow `run_number` (migration `027`, shown instead of the UUID), the `loaded_list`/`loaded_one` no-I/O mapper read primitives + a scoped 7-relationship `lazy="raise_on_sql"` guard (down-payment on the v0.8.6 eager-load-hardening epic), and a toolchain/dependency bump pass (SQLAlchemy 2.0.50, uv 0.11, node 24, pnpm 11.5.2, flyctl 1.6, Playwright 1.60).
→ [details](docs/backlog/v0.8.0-0.8.4.md#v084-background-sync-scheduling)

## [0.8.3] — 2026-06-07

Workflow scheduling **web UI** + an unplanned **workflow-page redesign with live-run reconnection**. A timezone-aware `SchedulePicker` (daily/weekly toggle, no cron) sets automation; the workflow list shows a "Next run" column sourced from a single caller-scoped `/schedules` fetch (no N+1). The detail page replaced the loose pipeline strip + `LastRunCard` with one state-aware `WorkflowStatusPanel` (active/idle/never-run) + a dedicated `RunHistoryTable`, and now reconnects to an in-flight run after reload via an app-global active-runs source + DB snapshot adoption. A paired scheduler fix persists an `operation_id` on scheduled runs so they're reconnectable, and the review pass unified schedule advancement into one fresh-read `_release` transaction. **Partial:** only the per-schedule failure badge shipped; the proactive dashboard banner + workflow-list failure indicator were carried to v0.8.4.
→ [details](docs/backlog/v0.8.0-0.8.4.md#v083-workflow-scheduling---web-ui--failure-alerts)

## [0.8.2] — 2026-06-07

Workflow scheduling **engine & CLI** — playlists can now rebuild themselves on a schedule. DB-stored daily/weekly schedules (no freeform cron; `croniter` kept only as the internal DST-correct next-occurrence engine over `zoneinfo`) fire from an in-process poll loop in the FastAPI lifespan, built on a shared `run_periodic_background_loop` (the sweeper was retrofitted onto it). Resilience: optimistic per-tick claim, a txn-level advisory poll-lock for multi-instance leader election, a stuck-start reaper-as-skip (no failure-streak bump), per-user OAuth-token isolation, and a single `schedules` table with an exclusive-arc CHECK for workflow-vs-sync targets. Drivable end-to-end via `mixd workflow schedule` / `mixd sync schedule`.
→ [details](docs/backlog/v0.8.0-0.8.4.md#v082-workflow-scheduling---engine--cli)

## [0.8.1] — 2026-05-31

No user-facing changes — the app boots leaner and runs workflows on its own engine. Workflow engine swap: Prefect 3 removed in favor of a homespun stdlib-asyncio DAG executor. Parallel execution levels are computed via Kahn's topological sort (a pure domain function) and each level runs in an `asyncio.TaskGroup`; run-state, cancellation (SIGTERM), and fault tolerance are owned in-process. Prefect and its full transitive tail are gone — the app no longer imports an embedded orchestration server at boot. The `workflows/` package was reorganized into `definition/`, `engine/`, and `nodes/`. Review pass also fixed primary-input track-count diagnostics and made lifecycle-observer emission best-effort.
→ [details](docs/backlog/v0.8.0-0.8.4.md#v081-workflow-engine-swap-prefect-to-stdlib-asyncio)

## [0.8.0] — 2026-05-30

Workflow runs fail loudly and accurately instead of silently or wrongly. **Run reliability & validation hardening** opened the v0.8.x scheduling cycle: a first-writer-wins terminal-write guard, a distinct `crashed` status (worker died) vs `failed` (logic broke), an OS-thread heartbeat watchdog that survives a blocked event loop, three closed silent-wrong-result validation gaps, and a SIGTERM-shielded connector cleanup.
→ [details](docs/backlog/v0.8.0-0.8.4.md#v080-run-reliability--validation-hardening)

## [0.7.8.20] — 2026-05-30

Workflow "kinds" consolidated — the read-only built-in **template** kind was eliminated in favor of a file-backed template **gallery** + clone-on-use, leaving a single editable `Workflow` entity (migration `023` drops `is_template`/`source_template`, the read-only guards, and shared `user_id IS NULL` rows). Clone-on-use mints a fresh unique slug, and Duplicate runs through a single-transaction `DuplicateWorkflowUseCase`. This delivered most of v0.8.9's template *plumbing* early; v0.8.9 then scoped down to curating the template content + import/export.

## Earlier releases (v0.2.7 – v0.7.8)

One line per ship; full stories live in the [archived version files](docs/backlog/completed/).

- **v0.7.8** (2026-05-09) — Mobile responsiveness + visual regression baseline (Playwright `toHaveScreenshot`) · [details](docs/backlog/completed/v0.7.8.md)
- **v0.7.7** (2026-04-26) — Operation Run Log: persisted import history + post-run toast · [details](docs/backlog/completed/v0.7.7.md)
- **v0.7.6** (2026-04-22) — Tag maintenance & single-playlist Spotify polish · [details](docs/backlog/completed/v0.7.6.md)
- **v0.7.5** — Workflow integration & quick filters · [details](docs/backlog/completed/v0.7.4-5.md)
- **v0.7.4** — Tag & preference bootstrap: bulk-map playlists to tags/preferences · [details](docs/backlog/completed/v0.7.4-5.md)
- **v0.7.3** — Playlist browser: browse & import Spotify playlists · [details](docs/backlog/completed/v0.7.2-3.md)
- **v0.7.2** — Tagging system: categorize tracks by mood, energy, context · [details](docs/backlog/completed/v0.7.2-3.md)
- **v0.7.1** — Preference sync from likes with original dates · [details](docs/backlog/completed/v0.7.0-1.md)
- **v0.7.0** — Preference system: rate tracks as hmm/nah/yah/star · [details](docs/backlog/completed/v0.7.0-1.md)
- **v0.6.0 – v0.6.12** — Multi-user: schema, per-user OAuth, data isolation, first-class CLI, SSE resilience, Neon platform integration, zero-Any codebase · [details](docs/backlog/completed/v0.6.x.md)
- **v0.5.0 – v0.5.10** — Infrastructure: CI/CD, PostgreSQL migration, containerized Fly.io deploys, OAuth + WCAG AA + theming, parallel execution, security hardening, the narada→mixd rename · [details](docs/backlog/completed/v0.5.x.md)
- **v0.4.0 – v0.4.11** — Workflows era: persistence, execution, visual editor, connector linking, provenance & merge, data-integrity audit, CLI unification · [details](docs/backlog/completed/v0.4.x.md)
- **v0.3.0 – v0.3.3** — Web UI foundation: playlists, imports with real-time progress, track library, dashboard · [details](docs/backlog/completed/v0.3.x.md)
- **v0.2.7 and earlier** — CLI era: like sync, play history import, Clean Architecture rebuild, playlist diff engine, workflow transforms · [details](docs/backlog/completed/v0.2.x.md)

---

## Historical (pre-v0.5, git-cliff era)

The sections below are the original auto-generated changelog, preserved as-is. It stopped being
maintained around v0.4.11; the "Unreleased" heading it accumulated under is retitled here. Some
documents it references (`ROADMAP.md`, `ARCHITECTURE.md`, `REFACTORING_PROGRESS.md`) have since
been retired into `docs/`.

### v0.4.x-era tail (formerly "Unreleased")

#### Bug Fixes

- post-migration cleanup from /simplify review
- checkpoint always-write-on-exit, Unset sentinel for cursor, commit-before-SSE
- async cleanup task leak and optimize test suite speed
- remove environment variable pollution and test fixture anti-patterns
- resolve LastFM e2e test failures and improve code quality
- resolve enricher key types and track repository test failures
- resolve 7 of 11 test failures through infrastructure fixes
- resolve type checking issues and format code

#### Documentation

- update ARCHITECTURE.md and ROADMAP.md for current codebase
- Phase 2 complete - SKIPPED conversion utilities (differences are semantic)
- comprehensive modernization progress with context and Phase 11 test review
- update REFACTORING_PROGRESS.md for Phase 2 completion
- complete Clean Architecture migration documentation

#### Features

- upgrade Vite 7→8 (Rolldown), adopt codeSplitting + tsconfigPaths
- workflow CLI CRUD + agent modernization (v0.4.11)
- CLI workflow runs create DB records + frontend design token consistency
- v0.4.10 — cross-source play history deduplication
- v0.4.9 — data integrity, identity resolution, test suite & type audit
- v0.4.8 — usability & self-explanatory interface pass
- connector track mapping management — relink, unlink, set primary
- v0.4.6 — track provenance & merge
- v0.4.4 — connector playlist linking + documentation restructure
- workflow fault tolerance, track invariants, and execution diagnostics
- add dynamic metric columns to workflow output tracks
- v0.4.3 — visual workflow editor, preview, versioning & diff
- v0.4.2 — run output persistence, pipeline strip, run-focused UI
- v0.4.1 — workflow execution, run history, live DAG status
- v0.4.0 — workflow CRUD, enricher nodes, workflow web UI, Spotify library/contains
- stale Spotify ID resolution with redirect detection and search fallback
- pre-flight connector validation before workflow execution
- dry-run mode + per-node execution records
- execution guard preventing concurrent workflow runs
- task timeouts + on_failure hook for workflow nodes
- NodeExecutionObserver protocol + refactor progress out of execute_node
- sub-operation progress tracking, workflow extraction, and simplify cleanup
- v0.3.3 — dashboard stats, version flow fix, roadmap resequencing
- track library pages, settings redesign, and nested API config
- v0.3.2 — DRY refactoring, checkpoint batching, and docs restructuring
- v0.3.1 — imports & real-time progress in the web UI
- v0.3.0 — web UI foundation, FastAPI backend, and playlist CRUD
- add multi-artist fallback and improve Last.FM logging levels
- improve metric display and update dependencies
- preserve playlist track metadata with PlaylistEntry pattern
- consolidate progress system and enhance Spotify operations
- implement unified progress tracking with Progress.console coordination
- resolve LastFM concurrency bottleneck and DRY code consolidation
- implement modular play import system with connector-specific factories
- implement comprehensive batch processing and error handling infrastructure
- complete Last.fm import system with enhanced track resolution and pagination
- implement unified Spotify import service with enhanced track identity resolution
- complete unambiguous identity pipeline refactor with clean architecture
- add data retrieval use cases and enhance play history management
- implement ultra-DRY enhanced playlist naming with template support
- complete infrastructure and workflow system overhaul
- implement comprehensive play history filtering and database optimization
- complete v0.2.4 playlist workflow expansion with comprehensive refactor
- complete Clean Architecture migration with UnitOfWork pattern and perfect code quality
- fix runtime workflow execution failures and implement comprehensive test improvements
- resolve SQLite database locking issues with session management improvements
- implement comprehensive metadata management and CLI restructuring

#### Maintenance

- v0.4.10 cleanup — resolver consolidation, branding, rules refinement
- migrate from Poetry to uv
- bump dev-setup-guide submodule to 61fe1b1
- move completed/ into backlog/, audit dev-setup-guide for clarity
- v0.4.5 — code & test suite hardening
- add claude rules and running log
- reorganize docs and add claude agents
- upgrade to Python 3.14.2 and update all dependencies
- remove 2 unused imports

#### Performance

- optimize bulk_upsert and update with identity map pattern

#### Refactoring

- codebase tighten pass — purge dead code (~7k lines: one-shot scripts, SQLite-era migration archive, test-only methods, dead endpoints/fields/enums), shrink suppression debt, consolidate checkpoint access, extract webhook verification + workflow background execution, fix stale docs/skills
- split Settings into Integrations + Sync sub-pages with collapsible sidebar nav
- DRY cleanup across repositories and frontend
- simplify v0.4.6 per code review
- extract dev-setup-guide into git submodule
- remove dead code, harden static analysis, and enforce strict track invariants
- dark editorial UI polish, extract SectionHeader, fix decodeHtmlEntities
- atomic sync+upsert in playlist_source connector branch
- restructure modules, remove dead code, and align test paths
- typed connector protocols, per-UoW caching, and codebase cleanup
- relocate modules to correct layers and redesign metrics system
- type architecture cleanup and Pydantic boundary validation
- migrate to native async httpx, fix logging, harden source nodes
- DRY consolidation and web interface readiness
- migrate from backoff to tenacity for retry policies
- configure tooling for Python 3.14 and fix string literal bugs
- modernize to Python 3.14 patterns in src/
- add slots=True to result value objects for memory efficiency
- convert remaining @dataclass to @define for 100% attrs consistency
- replace validate() methods with attrs field validators (Phase 1)
- extract BaseMatchingProvider to eliminate workflow duplication
- extract HTTPErrorClassifier base class to eliminate duplication
- eliminate duplication and modernize use case layer
- modularize domain transforms and eliminate duplication
- complete connector architecture migration and code reorganization
- restructure connector architecture with modular design
- enhance operation tracking and progress monitoring across import services
- modernize track matching system with pluggable provider pattern
- restructure project to clean architecture with src/ layout

#### Testing

- add comprehensive test coverage for new matching system

#### Cleanup

- remove redundant session regression test and complete Clean Architecture migration
