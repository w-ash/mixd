# Project Mixd — Planning

**Current Version**: 0.10.0
**Next**: v0.10.1 Continuous play polling — Spotify recently-played on the existing scheduler, demand-driven + adaptive ([details](v0.10.x.md#v0101-continuous-play-polling)). Follow-up pool unchanged: the [dependency-audit work orders](dependency-audit-findings.md) (W1–W10), the PLR0913/0917 flip decision ([spoke 26](fable-sweep/26-ratchet-closeout.md)), and the still-gated candidates (MCP spec/SDK drift check after the stable-v2 bump; model/effort cost re-eval before Sonnet 5 intro pricing ends 2026-08-31; demand-gated conversation persistence, memory tool, subagent fan-out, chat-voices toggle).

## Shipped — current cycle (v0.9.x–v0.10.x)

Canonical release log: [CHANGELOG.md](../../CHANGELOG.md) (all ships, full entries). This narrative keeps one line per ship for the current + previous minor cycle only; older lines are pruned at cycle close.

- **v0.10.0** (2026-07-17) — Convergent play history: canonical plays are a deterministic projection of an immutable observation ledger — re-imports and import order can't change your history, each play keeps the richest field values any source offered, and `mixd plays rebuild [--dry-run]` re-derives the whole history on demand. [changelog](../../CHANGELOG.md#0100--2026-07-17)
- **v0.9.5** (2026-07-16) — Remote MCP server: the same read + confirmable-write tools reachable over authenticated HTTPS at `https://mixd.me/mcp` — point Claude Code, Cursor, or Claude Desktop at your hosted account from any machine and authorize it once in the browser. An in-app OAuth 2.1 authorization server (CIMD + DCR, session-gated consent, rotating refresh tokens), a resource-server-authed stateless `/mcp` transport, and a Postgres-backed pending-action store so two-phase confirmation survives a multi-machine deploy. Local stdio MCP unchanged; the whole surface is off unless a signing key is configured. [changelog](../../CHANGELOG.md#095--2026-07-16)
- **v0.9.4** (2026-07-16) — Follow-ups & hardening: a full-series review of v0.9.0–v0.9.3 with fixes applied — the chat panel can no longer hang after a dropped connection, Stop click, or message cap; write confirmations report real success/failure; chat-launched long operations show progress again; two prompt-injection gaps (subagent summary, operation-failure text) are wrapped; live key validation is rate-limited; several 500-paths are closed; the MCP server survives a read-only spawn dir. Plus the ready-now follow-ups: page-contextual tool routing reclaims hot-slot budget off the workflows page (validated hint map, two-tier cache), and §6.5 of the user flows is rewritten to the shipped assistant. [changelog](../../CHANGELOG.md#094--2026-07-16)
- **v0.9.3** (2026-07-12) — MCP server: point any MCP client (Claude Desktop, Cursor, Claude Code) at your mixd library and drive it with the same read + confirmable-write tools the in-app assistant uses — a local stdio server over the parity registry, installed with `mixd mcp install`, with two-phase mutation confirmation carried in-band. Long-running ops (imports, runs, syncs) stay chat-only pending the post-2026-07-28 Tasks extension.
- **v0.9.2** (2026-07-12) — Agentic depth: the assistant now composes batch library operations in a server-side Python sandbox (only the answer re-enters the conversation), delegates deep investigations to a read-only research subagent that returns one dense summary, and reaches the 34-tool registry through BM25 tool search — with a curated hot set that follows the UI section you're on so the cached prompt prefix stays page-invariant.
- **v0.9.1** (2026-07-11) — Full capability parity: the in-app assistant's shared registry grew to 31 tools covering every read and mutation a user can perform, with two-phase confirmation on writes, long-running imports/syncs/runs streaming progress into chat, and agent-initiated operations attributed in the run log. The parity contract is CI-enforced (`NOT_YET_COVERED` empty) with a generated capability matrix.
- **v0.9.0.1** (2026-07-11) — Per-user, opt-in AI assistant: each user brings their own Anthropic key (validated live, stored encrypted, write-only), and the whole chat surface stays hidden until they connect one — no shared key, no broken affordance.
- **v0.9.0** (2026-07-11) — Workflow Assistant: a persistent right-panel chat turns a plain-English request ("build me a chill weekend playlist") into a real, editable WorkflowDef previewed in the graph renderer and saved on approval — on a parity-classified tool registry the later v0.9.x milestones and the MCP server all consume.

## Shipped — previous cycle (v0.8.x)

- **v0.8.18.3** (2026-07-10) — Neon CI branch-leak fix: push-to-main runs delete their database branch and every CI branch carries a TTL, ending the orphan pile-up that maxed the Neon monthly limit (40 stale branches purged).
- **v0.8.18.2** (2026-07-09) — Dependency freshness sweep: backend + frontend deps to latest (uvicorn, the UI stack, TypeScript 7), an orval regen that fixes `tag` query-param serialization; `better-auth`'s alerts remain blocked on the beta Neon SDK.
- **v0.8.18.1** (2026-07-04) — Identity-review hardening: migration 035 renames stop aborting on collisions, Last.fm's untrusted MBIDs can no longer merge distinct recordings, per-user reviews route to the right owner, and display/promotion agree on equal-confidence ties.
- **v0.8.18** (2026-07-03) — Identity Integrity: the matching layer stops corrupting its own confidence data — freshness-not-confidence re-imports, ISRC-reuse routes to review instead of clobbering, one Last.fm identity per track (migration 035), and drift metrics in `stats --matching`.
- **v0.8.17.2** (2026-07-02) — CI flake fixed: CLI tests pin terminal size, so a green pipeline means green.
- **v0.8.17.1** (2026-07-02) — version metadata corrected after a mistagged ship; deployed code unchanged.
- **v0.8.17** (2026-07-02) — sweep closeout: ratchet re-census, noqa whodunit, visual-gate revival, dependency audit (W1–W10), identity-resolution research + the PDR system.
- **v0.8.16** (2026-07-02) — Sweep Wave 4: executor flatten (characterization tests first) + dead connector-reflection deletion.
- **v0.8.15** (2026-07-02) — Sweep Wave 3: consistent loading/error/empty states (`QueryStates`), track-search de-fork, 13 page decompositions.
- **v0.8.14** (2026-07-02) — Sweep Wave 2: fourteen behavior-preserving backend-structure spokes + a dependency refresh.
- **v0.8.13** (2026-07-02) — Sweep Wave 1: dead code purged, type floor flipped to error, severity tokens (+ a latent dialog-styling fix).
- **v0.8.12** (2026-07-01) — the Fable Sweep audit: working hub + 26 work orders (docs-only, no deploy artifact).
- **v0.8.11** (2026-06-30) — manual playlist track editing: add/remove/reorder from the detail page, with undo.
- **v0.8.10** (2026-06-29) — predictable editor entry, browse-to-link, config-aware validation, day-window rename (migration 033).
- **v0.8.9** (2026-06-28) — workflow templates & import/export.
- **v0.8.7–v0.8.8** (2026-06-25) — import/sync reconciliation: honest diffs, unresolved rows, confirm-token destructive gate, retry-failed-only web surface.
- **v0.8.6** (2026-06-18) — cycle hardening: honest partial-push reporting, unmatched-track surfacing, ORM eager-load guard.
- **v0.8.5** (2026-06-16) — operation & surface reliability: SSE-seam inversion, stranded-plays data-loss fix, server-safe tokens.
- **v0.8.4** (2026-06-09) — background sync scheduling + proactive failure banners.
- **v0.8.3** (2026-06-07) — scheduling web UI + workflow-page redesign with live-run reconnection.
- **v0.8.2** (2026-06-07) — scheduling engine & CLI: daily/weekly, DST-correct, multi-instance leader election.
- **v0.8.1** (2026-05-31) — workflow engine swap: Prefect → stdlib-asyncio DAG executor.
- **v0.8.0** (2026-05-30) — run reliability & validation hardening: crashed-vs-failed statuses, heartbeat watchdog.
- **v0.7.8.20** (2026-05-30) — workflow template kind eliminated → file-backed gallery + clone-on-use.

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
| **v0.4.0** | Workflows (persistence, visualization, CRUD) | ✅ Completed | [details](completed/v0.4.x.md#v040-workflow-persistence--visualization-vertical-slice-5a) |
| **v0.4.1** | Workflow execution + run history | ✅ Completed | [details](completed/v0.4.x.md#v041-workflow-execution--run-history-vertical-slice-5b) |
| **v0.4.2** | Run-first workflow UX | ✅ Completed | [details](completed/v0.4.x.md#v042-run-first-workflow-ux-vertical-slice-5c) |
| **v0.4.3** | Visual workflow editor + versioning | ✅ Completed | [details](completed/v0.4.x.md#v043-visual-workflow-editor--preview-vertical-slice-5d) |
| **v0.4.4** | Connector playlist linking | ✅ Completed | [details](completed/v0.4.x.md#v044-connector-playlist-linking-vertical-slice-6) |
| **v0.4.5** | Code & test suite hardening | ✅ Completed | [details](completed/v0.4.x.md#v045-code--test-suite-hardening) |
| **v0.4.6** | Track provenance & duplicate merge | ✅ Completed | [details](completed/v0.4.x.md#v046-track-provenance--merge-vertical-slice-7a) |
| **v0.4.7** | Track relink & unlink | ✅ Completed | [details](completed/v0.4.x.md#v047-track-relink--unlink-vertical-slice-7b) |
| **v0.4.8** | Usability & self-explanatory interface pass | ✅ Completed | [details](completed/v0.4.x.md#v048-usability--self-explanatory-interface-pass) |
| **v0.4.9** | Data integrity & quality audit | ✅ Completed | [details](completed/v0.4.x.md#v049-data-integrity--quality-audit) |
| **v0.4.10** | Cross-service play history deduplication | ✅ Completed | [details](completed/v0.4.x.md#v0410-cross-service-play-history-deduplication) |
| **v0.4.11** | CLI unification & polish | ✅ Completed | [details](completed/v0.4.x.md#v0411-cli-unification--polish) |
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
| **v0.7.0** | Preference system — rate tracks as hmm/nah/yah/star | ✅ Completed | [details](completed/v0.7.0-1.md#v070-preference-system) |
| **v0.7.1** | Preference sync from likes — imported likes become preferences with original dates | ✅ Completed | [details](completed/v0.7.0-1.md#v071-preference-sync-from-likes) |
| **v0.7.2** | Tagging system — categorize tracks by mood, energy, context | ✅ Completed | [details](completed/v0.7.2-3.md#v072-tagging-system) |
| **v0.7.3** | Playlist browser — browse & import Spotify playlists | ✅ Completed | [details](completed/v0.7.2-3.md#v073-playlist-browser) |
| **v0.7.4** | Tag & preference bootstrap — bulk-map playlists to tags/preferences | ✅ Completed | [details](completed/v0.7.4-5.md#v074-tag--preference-bootstrap) |
| **v0.7.5** | Workflow integration & quick filters | ✅ Completed | [details](completed/v0.7.4-5.md#v075-workflow-integration--quick-filters) |
| **v0.7.6** | Tag maintenance & single-playlist Spotify polish — tag mgmt page, force-refresh, route integration tests | ✅ Completed | [details](completed/v0.7.6.md#v076-tag-maintenance--single-playlist-polish) |
| **v0.7.7** | Operation Run Log — persisted import history + post-run toast | ✅ Completed | [details](completed/v0.7.7.md#v077-operation-run-log) |
| **v0.7.8** | Mobile responsiveness + visual regression baseline (Playwright `toHaveScreenshot`) | ✅ Completed | [details](completed/v0.7.8.md#v078-mobile-responsiveness) |
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
| **v0.8.18** | Identity integrity — confidence repair, ISRC guards, drift metrics (2026-07 research D1; gates v0.12.1 artist identity, v0.11.x Apple Music, v1.1.x–v1.2.x sharing) | 🚀 Shipped | [details](v0.8.18.md#v0818-identity-integrity) |
| **v0.9.0** | Workflow assistant — right-panel agentic chat (couplefins v1.8.x port) + parity-classified tool registry | 🚀 Shipped | [details](v0.9.x.md#v090-workflow-assistant-agentic-foundation) |
| **v0.9.1** | Full capability parity — every read/mutation as a confirmable tool; agent activity visible in the UI | 🚀 Shipped | [details](v0.9.x.md#v091-full-capability-parity-in-app) |
| **v0.9.2** | Agentic depth — programmatic tool calling, research subagent, tool search, context management, page-contextual tool routing | 🚀 Shipped | [details](v0.9.x.md#v092-agentic-depth) |
| **v0.9.3** | MCP server — mixd as a tool surface (stdio, stateless; Tasks for long ops; consumes the registry) | 🚀 Shipped | [details](v0.9.x.md#v093-mcp-server-mixd-as-a-tool-surface) |
| **v0.9.4** | Follow-ups & hardening — v0.9.x full-series review with fixes applied; page-contextual routing refinement; §6.5 user-flows rewrite | 🚀 Shipped | [details](v0.9.x.md#v094-follow-ups--hardening) |
| **v0.9.5** | Remote MCP server — the tool registry over authenticated Streamable-HTTP on the Fly/Neon production deployment (OAuth 2.1 resource server; per-user, from any agent) | 🚀 Shipped | [details](v0.9.x.md#v095-remote-mcp-server-your-production-library-from-any-agent) |
| **v0.10.0** | Convergent play history — order-free, re-import-safe canonical plays projected from the observation ledger; lands before the first at-scale import ([findings](play-import-convergence-findings.md)) | 🚀 Shipped | [details](v0.10.x.md#v0100-convergent-play-history) |
| **v0.10.1** | Continuous play polling — Spotify recently-played, demand-driven + adaptive | 🔜 Not Started | [details](v0.10.x.md#v0101-continuous-play-polling) |
| **v0.10.2** | Mapping supersession & resolution event log (pulled forward so v0.11.x connectors are supersession-native) | 🔜 Not Started | [details](v0.10.x.md#v0102-mapping-supersession--resolution-event-log) |
| **v0.11.0** | Apple Music foundation — auth, client, ISRC-conservative resolution, play channel | 🔜 Not Started | [details](v0.11.x.md#v0110-apple-music-foundation) |
| **v0.11.1** | Discogs foundation — BYO-token client + collection snapshot | 🔜 Not Started | [details](v0.11.x.md#v0111-discogs-foundation) |
| **v0.12.0** | Entity representation spike — artists/albums across five services, real data (docs-only) | 🔜 Not Started | [details](v0.12.x.md#v0120-entity-representation-spike) |
| **v0.12.1** | First-class artists | 🔜 Not Started | [details](v0.12.x.md#v0121-first-class-artists) |
| **v0.12.2** | First-class albums | 🔜 Not Started | [details](v0.12.x.md#v0122-first-class-albums) |
| **v0.13.0** | Apple Music integration — library, likes sync, deferred-play re-resolution | 🔜 Not Started | [details](v0.13.x.md#v0130-apple-music-integration) |
| **v0.13.1** | Physical media & Discogs | 🔜 Not Started | [details](v0.13.x.md#v0131-physical-media--discogs) |
| **v0.13.2** | Manual listens | 🔜 Not Started | [details](v0.13.x.md#v0132-manual-listens) |
| **v0.14.0** | Data quality tools | 🔜 Not Started | [details](v0.14.x.md#v0140-data-quality) |
| **v0.14.1** | Rekordbox connector + audio quality enrichment | 🔜 Not Started | [details](v0.14.x.md#v0141-rekordbox-connector) |
| **v0.15.0** | Data sovereignty — continuous archive & exit rights (direction-neutral under PDR-001) | 🔜 Not Started | [details](v0.15.x.md#v0150-data-sovereignty--archive--exit-rights) |
| **v0.15.1** | Onboarding & multi-user hardening | 🔜 Not Started | [details](v0.15.x.md#v0151-onboarding--multi-user-hardening) |
| **v1.0.0** | The gate — maturity review, doors open to invited testers (no new features) | 🔜 Not Started | [details](v1.0.x.md#v100-maturity-gate) |
| **v1.1.0** | Cross-user track identity (social opener; trust machinery parked per 2026-07 revision) | 🔜 Not Started | [details](v1.1.x.md#v110-cross-user-track-identity) |
| **v1.1.1** | Privacy controls & public profiles | 🔜 Not Started | [details](v1.1.x.md#v111-privacy-controls--public-profiles) |
| **v1.1.2** | Social graph & follows | 🔜 Not Started | [details](v1.1.x.md#v112-social-graph--follows) |
| **v1.1.3** | Activity feed & social context | 🔜 Not Started | [details](v1.1.x.md#v113-activity-feed--social-context) |
| **v1.1.4** | Sharing & growth | 🔜 Not Started | [details](v1.1.x.md#v114-sharing--growth) |
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
| v0.9.x | Agentic workspace — in-app assistant + MCP surface | Delegates the Sunday ritual sentence by sentence — batch maintenance, analysis, imports with live progress | The parity contract is the draw: anything the app can do, their agents (in-app or via MCP from Claude Desktop/Cursor) can do | THE adoption enabler — natural-language workflows change who can use mixd; everyday actions without learning the UI |
| v0.10.x | Play history & mapping integrity | Play counts finally trustworthy and live (poll + export + Last.fm converge) | Observation ledger, rebuild command, append-only mapping history to script against | History "just works" no matter what they connect or re-import |
| v0.11.x | Connector foundations (Apple, Discogs) | Apple listening joins the canonical history; the Discogs on-ramp | Two new connector surfaces to explore | Groundwork for "connect Apple Music" being a first-class button |
| v0.12.x | First-class artists & albums | Favorites and artist-driven playlists on identity measured across five services | Entity design in the open (spike findings doc); rich relational model | Browse-by-artist/album navigation every music app is expected to have |
| v0.13.x | Completing the collection | Full reclamation — streaming, physical, and manual history unified | Re-resolution machinery + collection import to script | Apple Music at parity with Spotify |
| v0.14.x | Data quality + Rekordbox | Fixes mappings, finds gaps, trusts automated results | Rekordbox: purchased music + BPM/key/lossless workflow filters | Fewer wrong-song moments |
| v0.15.x | Sovereignty & gate readiness | Disaster insurance for years of curation | Data sovereignty made tangible | Ownership without self-hosting |
| v1.0.x | The gate | Confidence the data model is stable before guests arrive | An instance they can invite friends to | They can finally get an account |
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
