# Project Narada Backlog

Detailed epics and task breakdowns for planned versions.
For strategic overview, see [ROADMAP.md](../ROADMAP.md).
For future ideas and research, see [IDEAS.md](IDEAS.md).

---

## Reference Guide

### Effort Estimates

Never estimate time, always estimate based on relative effort.

| Size    | Complexity Factors           | Criteria                                                                       |
| ------- | ---------------------------- | ------------------------------------------------------------------------------ |
| **XS**  | Well known, isolated         | Minimal unknowns, fits existing components, no dependencies                    |
| **S**   | A little integration         | Simple feature, 1–2 areas touched, low risk, clear requirements                |
| **M**   | Cross-module feature         | 3–4 areas involved, small unknowns, minor dependencies                         |
| **L**   | Architectural impact         | ≥3 subsystems, integrations, external APIs, moderate unknowns                  |
| **XL**  | High unknowns & coordination | Cross-team, backend + frontend + infra, regulatory/security concerns           |
| **XXL** | High risk & exploration      | New platform, performance/security domains, prototype-first, many dependencies |

### Status Options
- 🔜 Not Started
- 🔄 In Progress
- 🛑 Blocked
- ✅ Completed

---

### v0.2.7: Advanced Workflow Features
Extend workflow capabilities with sophisticated transformation and analysis features.

- [x] **Narada Data Source Nodes**
    - Status: ✅ Completed (2025-08-11)
    - Effort: M
    - What: Create workflow source nodes that tap directly into Narada's rich canonical track database
    - Why: Enable workflows based on listening history and preferences without requiring playlist containers
    - Dependencies: v0.2.6 completion (Enhanced Playlist Naming foundation)
    - Notes:
        - **`source.liked_tracks`**: ✅ Access liked tracks across all services with optional connector filtering
        - **`source.played_tracks`**: ✅ Source tracks from play history with time range and frequency filters
        - **Performance Safeguards**: ✅ Maximum 10,000 tracks per source, configurable limits to prevent overwhelming workflows
        - **Built-in Filtering**: ✅ Basic filters (date ranges, service filters, play count thresholds) to keep initial trackists manageable
        - **Discovery Enablement**: ✅ Unlock workflow patterns like "tracks I loved but haven't heard recently" without playlist management overhead

- [x] **DRY Consolidation & Web Interface Readiness**
    - Status: ✅ Completed (2026-02-16)
    - Effort: L
    - What: Eliminate module duplication, modernize to Python 3.14 idioms, restructure interface layer so FastAPI can reuse all application logic
    - Why: Codebase had accumulated overlapping abstractions, dead code, and CLI-coupled interface logic blocking v0.3.0 web UI
    - Dependencies: None
    - Notes:
        - **Python 3.14 Modernization**: `@override` decorators, `TypeIs` mapper guards, error classifier hierarchy simplification
        - **Dead Code Removal**: Deleted empty modules (conversions.py, setup_commands.py, status_commands.py), removed orphan protocols
        - **Module Consolidation**: Merged failure handling files, matching provider files, extracted shared ISRC utilities
        - **Interface Restructuring**: Moved CLI-specific code out of shared/, extracted interactive menu pattern, moved async executor to CLI layer
        - **Web Readiness**: Created `application/runner.py` with `execute_use_case[TResult]()` — both CLI and FastAPI share this runner

- [x] **Advanced Transformer Workflow nodes**
    - Status: ✅ Completed (2026-03-01)
    - Effort: M
    - What: Implement additional transformer nodes for workflow system
    - Why: More transformation options enable more powerful workflows
    - Dependencies: v0.2.6 completion (Enhanced Playlist Naming foundation)
    - Notes:
        - `combiner.merge_playlists` - Combines multiple playlists (concatenates)
        - `combiner.concatenate_playlists` - Joins playlists in specified order
        - `combiner.interleave_playlists` - Interleaves tracks from multiple sources
        - `selector.limit_tracks` - Selection with methods: first, last, random
        - `sorter.weighted_shuffle` - Randomization with configurable shuffle strength (0.0-1.0)
        - `sorter.by_first_played` - Sort by date first played
        - `sorter.by_last_played` - Sort by date most recently played
        - 18 production workflow templates in `definitions/`

### v0.3.0: Web UI Foundation + Playlists (Vertical Slice 1)
**Goal**: Stand up FastAPI + React with the dark editorial design system. Deliver two foundational features — playlist CRUD and connector settings — proving the full-stack architecture end-to-end using entirely existing use cases.

**Context**: 11 of 15 use cases already exist. The `execute_use_case()` runner (v0.2.7) is ready for both CLI and FastAPI. All 5 playlist use cases work today. This milestone adds no new backend logic — it builds the API + UI layers and validates the architecture with a real feature. Settings provides the canonical entry point for connecting services.

**Why playlists first**: Full use case coverage already exists (`ListPlaylists`, `CreateCanonicalPlaylist`, `ReadCanonicalPlaylist`, `UpdateCanonicalPlaylist`, `DeleteCanonicalPlaylist`). Maximum frontend validation with minimum backend work.

> **Detailed specifications** — user journeys, API contracts, information architecture, and frontend architecture — live in [`docs/web-ui/`](web-ui/README.md). This backlog tracks *what to build and when*; the web-ui docs specify *how it should work*.

#### FastAPI Foundation Epic

- [x] **FastAPI Application Setup**
    - Effort: M
    - Status: ✅ Completed (2026-03-02)
    - What: Create FastAPI service with playlist REST API endpoints and health check
    - Why: Web interface needs programmatic access to use cases; playlists are the first slice
    - Dependencies: None (use cases already exist)
    - Notes:
        - Pydantic v2 with `from_attributes=True`; settings via `pydantic_settings.BaseSettings` + `@lru_cache()`
        - Project structure: `src/interface/api/` with app factory, routers, schemas, dependencies
        - Dependency injection via `execute_use_case()` runner (✅ already built)
        - Error handling middleware with `NotFoundError` → 404, `ValueError` → 400, `Exception` → 500
        - Security: CORS `allow_origins = ["http://localhost:5173"]` for dev
        - Authentication: None (single-user local development); OAuth deferred to v0.5.0
        - **Endpoints**: `GET /health`, `GET /connectors`, `GET /playlists`, `POST /playlists`, `GET /playlists/{id}`, `PATCH /playlists/{id}`, `DELETE /playlists/{id}`, `GET /playlists/{id}/tracks`
        - SPA catch-all route serves `index.html` for client-side routing
        - 41 integration tests (httpx ASGITransport + isolated SQLite per test)

#### React Application Foundation Epic

- [x] **React App + Design System Foundation**
    - Effort: L
    - Status: ✅ Completed (2026-03-02)
    - What: Vite 7 + React 19 + TypeScript project with pnpm, shadcn/ui, Tailwind v4 editorial design tokens, and Tanstack Query
    - Why: Establish the frontend stack and distinctive visual identity in one go — every subsequent milestone adds pages to a working app
    - Dependencies: FastAPI Application Setup
    - Notes:
        - Tailwind v4 `@theme` tokens: dark editorial OKLCH palette, Space Grotesk / Newsreader / JetBrains Mono typography
        - shadcn/ui primitives customized to warm dark aesthetic (Button, Card, Table, Input, Dialog, Toast, Skeleton)
        - App shell: Sidebar navigation, PageLayout with ErrorBoundary, React Router routes
        - Orval v8 codegen: tags-split mode, Tanstack Query hooks, MSW mock handlers from `web/openapi.json`
        - `customFetch()` mutator + `ApiError` class in `web/src/api/client.ts`
        - Biome for lint+format (not ESLint/Prettier)
        - 69 Vitest tests across 12 test files

#### Playlists Page Epic

- [x] **Playlists Pages**
    - Effort: M
    - Status: ✅ Completed (2026-03-02)
    - What: Playlist List page, Playlist Detail page, Create/Edit/Delete playlist modals
    - Why: First working vertical slice — proves the full stack end-to-end
    - Dependencies: React App + Design System Foundation
    - Notes:
        - Playlist List (`/playlists`): table with name, description, track count, linked connector icons, last updated, pagination
        - Playlist Detail (`/playlists/:id`): stats bar, track table, edit/delete dialogs
        - Create Playlist modal: name + description, mutation with cache invalidation
        - Empty states for no playlists and empty playlists

#### Settings Page Epic

- [x] **Settings Page**
    - Effort: S
    - Status: ✅ Completed (2026-03-02)
    - What: Connector status display with silent Spotify token refresh
    - Why: Users need to see which connectors are connected from day one; Settings is the canonical entry point for connecting services
    - Dependencies: FastAPI Application Setup
    - Notes:
        - Settings (`/settings`): connector cards showing auth status per service
        - `GET /connectors` endpoint: reads `.spotify_cache` + env vars, returns connection status, account name, token expiry
        - Silent Spotify token refresh when expired (bypasses browser OAuth fallback)
        - MusicBrainz = always available, Apple Music = stub (under development)

---

### v0.3.1: Imports & Real-Time Progress (Vertical Slice 2)
**Goal**: Make the web UI operational for day-to-day use — trigger imports, watch real-time SSE progress. First milestone with new backend work (`SSEProgressProvider`).

**Context**: Import use cases already exist (`SyncLikesUseCase`, `ImportPlayHistoryUseCase`). The `ProgressSubscriber` protocol (domain layer) is already display-agnostic. This milestone adds the SSE subscriber and the frontend hooks that consume it.

#### SSE Progress Epic

- [ ] **SSE Progress Provider**
    - Effort: M
    - What: `SSEProgressProvider` implementing `ProgressSubscriber` protocol — serializes `ProgressEvent` to SSE `data:` frames
    - Why: Real-time progress is the defining UX for import operations; this validates the progress architecture
    - Dependencies: FastAPI app (v0.3.0)
    - Status: 🔜 Not Started
    - Notes:
        - Implements same `ProgressSubscriber` protocol as `RichProgressProvider` (CLI)
        - Registered with `AsyncProgressManager.subscribe()` — same pub/sub mechanism
        - Endpoints: `GET /operations/{id}/progress` (SSE stream), `GET /operations/{id}` (snapshot fallback), `GET /operations` (recent)
        - Standardize progress reporting across all long-running operations (ETA calculations where possible)
        - **Backend**: Use `sse-starlette` library for SSE response streaming
        - **Production headers**: Set `X-Accel-Buffering: no` (prevents Nginx/reverse proxy from buffering SSE chunks) and `Cache-Control: no-cache`
        - **Disconnect detection**: Check `request.is_disconnected()` in the SSE generator loop to clean up resources when clients drop
        - **Memory**: Each SSE connection maintains a buffer — monitor connection count in production

#### Import Endpoints Epic

- [ ] **Import API Routes**
    - Effort: S
    - What: Import trigger endpoints backed by existing use cases
    - Why: Users need to trigger imports from the web UI
    - Dependencies: SSE Progress Provider
    - Status: 🔜 Not Started
    - Notes:
        - `POST /imports/spotify/likes` → `SyncLikesUseCase`
        - `POST /imports/lastfm/history` → `ImportPlayHistoryUseCase` (Last.fm mode)
        - `POST /imports/spotify/history` → `ImportPlayHistoryUseCase` (Spotify GDPR mode)
        - `GET /imports/checkpoints` → sync checkpoint query
        - Non-blocking execution via `BackgroundTask` — SSE streams progress back

#### Imports Frontend Epic

- [ ] **Imports Page + Progress UI**
    - Effort: M
    - What: Imports page with operation triggers, SSE progress display, and activity feed
    - Why: The web UI isn't useful until users can trigger operations
    - Dependencies: Import API Routes
    - Status: 🔜 Not Started
    - Notes:
        - `useSSE` hook wrapping `@microsoft/fetch-event-source` (not native `EventSource` — supports POST, custom headers) with `Last-Event-ID` reconnection and typed `ProgressEvent` parsing
        - `useOperation` hook composing SSE + Tanstack Query
        - `OperationProgress` shared component (progress bar, status messages, completion summary)
        - Imports page (`/imports`): available operations, checkpoint status per connector, activity feed
        - Sidebar badge indicator when operations are running
        - Persistent toast for background operation completion

---

### v0.3.2: Library & Search (Vertical Slice 3)
**Goal**: Track browsing with pagination, text search, and detail views. New use cases built alongside the React pages that consume them — validates API shape in real-time.

**Context**: Current track operations are limited to filtered views (`GetLikedTracksUseCase`, `GetPlayedTracksUseCase`). The web UI needs generic listing, pagination, search, and single-track detail. Building these use cases alongside their frontend consumer prevents speculative API design.

#### Track Use Cases Epic

- [ ] **List All Tracks Use Case**
    - Effort: M
    - What: `ListTracksUseCase` — generic track listing with true pagination (offset/limit)
    - Why: Web UI needs "show all tracks" without liked/played filtering
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Support offset/limit pagination (not fixed 10,000 limit like current use cases)
        - Support multi-criteria filtering (liked + played + time period + connector)
        - Support sorting by: title, artist, album, release_date, duration_ms, added_at
        - Repository already supports batch operations, just needs pagination wrapper
        - Return: Paginated `TrackList` with total count, offset, limit metadata

- [ ] **Search Tracks Use Case**
    - Effort: M
    - What: `SearchTracksUseCase` — full-text search by title/artist/album
    - Why: Essential for web UI track browser — users need to find specific tracks
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Search across: track title, primary artist, album name
        - Support pagination (offset/limit)
        - Support sorting (relevance, title, artist, release_date)
        - Repository layer: Add `search_tracks()` method to TrackRepository
        - Minimum viable: Simple LIKE queries, optimize later if needed

- [ ] **Get Track Details Use Case**
    - Effort: S
    - What: `GetTrackDetailsUseCase` — single track with full assembled metadata
    - Why: Web UI track detail view needs comprehensive track information
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Input: track_id (internal canonical ID)
        - Output: Track with enriched metadata including:
            - Connector mappings (connector_name, connector_id, is_primary, confidence, match_method)
            - Like status per connector
            - Play history summary (total plays, last played, first played)
            - Connector metadata (Spotify popularity, Last.fm play counts, etc.)
        - Composes data from multiple repositories

- [ ] **Track Connector Mappings Use Case**
    - Effort: S
    - What: `GetTrackConnectorMappingsUseCase` — retrieve which connectors have mappings for a given track
    - Why: Track Detail page needs "This track is on: Spotify, Last.fm" with confidence scores
    - Dependencies: None (data already exists in track_mappings table)
    - Status: 🔜 Not Started
    - Notes:
        - Repository support already exists: `get_connector_mappings()`
        - Just needs use case wrapper

#### Track API Routes Epic

- [ ] **Track API Endpoints**
    - Effort: S
    - What: REST endpoints for tracks: list, search, detail, mappings
    - Why: Frontend needs API access to track data
    - Dependencies: Track use cases above
    - Status: 🔜 Not Started
    - Notes:
        - `GET /tracks` → `ListTracksUseCase` (with query params for filtering, sorting, pagination)
        - `GET /tracks/search?q=...` → `SearchTracksUseCase`
        - `GET /tracks/{id}` → `GetTrackDetailsUseCase`
        - `GET /tracks/{id}/mappings` → `GetTrackConnectorMappingsUseCase`

#### Library Frontend Epic

- [ ] **Library + Track Detail Pages**
    - Effort: L
    - What: Library page with paginated track table, search, and Track Detail page with full metadata
    - Why: The Library is the most data-dense page — building it validates pagination, search, and component reuse
    - Dependencies: Track API Endpoints
    - Status: 🔜 Not Started
    - Notes:
        - Library page (`/library`): paginated table, search bar with debounce, filter dropdowns (connector, liked status), column sorting
        - Track Detail page (`/library/:id`): metadata card, connector mapping badges, like status per service, play history summary
        - `TrackRow` shared component (reusable across Library, Playlist Detail, Search)
        - `AlbumArt` component with warm glow effect against dark canvas
        - `ConnectorIcon` component (Spotify green, Last.fm red, Apple pink)
        - Empty states for library and track detail sub-sections

---

### v0.3.3: Dashboard & Stats (Vertical Slice 4)
**Goal**: Landing page with aggregate statistics, connector health, and data quality signals.

**Context**: The dashboard ties everything together — it's the first thing users see and provides contextual navigation to Library, Playlists, and Imports. Requires new aggregation use cases.

#### Stats Use Cases Epic

- [ ] **Track Statistics Use Case**
    - Effort: S
    - What: `GetTrackStatsUseCase` — aggregate statistics without loading all entities
    - Why: Dashboard needs counts: "15,234 tracks total, 8,456 liked, 12,891 played"
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Output: Total tracks, tracks per connector, liked count, played count
        - Optimized aggregation queries (COUNT, not fetching rows)
        - Can include: duplicate detection counts, unmapped track counts

- [ ] **Connector Mapping Statistics Use Case**
    - Effort: S
    - What: `GetConnectorMappingStatsUseCase` — aggregate mapping statistics
    - Why: Dashboard needs "5,234 tracks mapped to Spotify, 3,891 to Last.fm"
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Output: Counts per connector, unmapped track counts, confidence distribution
        - Queries track_mappings table with aggregations

- [ ] **Sync Status Overview Use Case**
    - Effort: M
    - What: `GetSyncStatusUseCase` — comprehensive sync state for all connectors
    - Why: Users need "Last synced with Spotify: 2 days ago, Last.fm: 5 hours ago"
    - Dependencies: None (sync_checkpoints table has all data)
    - Status: 🔜 Not Started
    - Notes:
        - Queries sync_checkpoints table
        - Output: Per-connector, per-entity (likes/plays) last sync timestamps
        - Domain: `SyncStatusDashboard` value object with formatted output

- [ ] **Metadata Freshness Tracking Use Case**
    - Effort: S
    - What: `GetMetadataFreshnessUseCase` — when was track data last updated from connectors?
    - Why: Show "Spotify metadata: updated 3 days ago" on dashboard
    - Dependencies: None (connector_tracks.last_updated exists)
    - Status: 🔜 Not Started
    - Notes:
        - Input: track_ids, connector_name
        - Output: Per-track, per-connector last update timestamps
        - Uses existing `get_metadata_timestamps()` repository method

#### Dashboard API + Frontend Epic

- [ ] **Dashboard Stats Endpoints**
    - Effort: S
    - What: `GET /stats/dashboard` (aggregate stats), `GET /connectors` (connector status)
    - Why: Dashboard page needs a single endpoint for all summary data
    - Dependencies: Stats use cases above
    - Status: 🔜 Not Started

- [ ] **Dashboard Page**
    - Effort: M
    - What: Landing page with stat cards, connector health badges, freshness alerts, and activity feed
    - Why: First thing users see — ties the whole app together
    - Dependencies: Dashboard Stats Endpoints
    - Status: 🔜 Not Started
    - Notes:
        - Dashboard (`/`): stat cards (total tracks, playlists, plays), connector health badges, freshness alerts
        - Recent activity feed (last imports, last workflow runs)
        - Freshness alerts link to Import Center; connector issues link to Settings (informational, not action buttons)

---

### v0.4.0: Workflows & Connector Links (Vertical Slice 5)
**Goal**: Workflow visualization (React Flow DAG), execution with SSE progress, and connector playlist linking. Completes the web UI feature set.

**Context**: Workflows are Narada's differentiator — declarative pipelines composing user-defined criteria. This milestone brings them to the web with read-only visualization and one-click execution. Connector playlist linking enables push/pull sync from the web.

#### Workflow Persistence Epic

- [ ] **Workflow CRUD Use Cases + Table**
    - Effort: M
    - What: Workflow persistence (new `workflows` table), CRUD use cases, execution endpoint
    - Why: Currently workflows are JSON files — need database persistence for web CRUD and execution history
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - **New `workflows` table**: `id UUID`, `name VARCHAR`, `description TEXT`, `definition JSONB`, `created_at`, `updated_at`
        - Use cases: `ListWorkflows`, `GetWorkflow`, `CreateWorkflow`, `UpdateWorkflow`, `DeleteWorkflow`, `RunWorkflow`
        - Alembic migration (works on both SQLite JSON and PostgreSQL JSONB)
        - `RunWorkflow` delegates to existing `run_workflow()` in `prefect.py`
        - Execution endpoint streams progress via SSEProgressProvider (v0.3.1)

#### Workflow API + Frontend Epic

- [ ] **Workflow Pages**
    - Effort: L
    - What: Workflow List, Workflow Detail with React Flow DAG visualization, JSON editor, and execution
    - Why: Workflows are the core product — users need to see and run them from the web
    - Dependencies: Workflow CRUD Use Cases
    - Status: 🔜 Not Started
    - Notes:
        - Workflow List (`/workflows`): name, description, last run status, run button
        - Workflow Detail (`/workflows/:id`): React Flow DAG visualization (read-only), execution history, "Run" button with SSE progress
        - Workflow Editor (`/workflows/:id/edit`): JSON editor with validation (visual drag-and-drop builder deferred to v0.7.0)
        - React Flow integration: custom node components for source/enricher/transform/destination, edge styling

#### Playlist Connector Links Epic

- [ ] **Playlist Links Management**
    - Effort: M
    - What: Connector playlist linking — push/pull sync from the web
    - Why: Users need to link canonical playlists to Spotify/Apple Music playlists and sync changes
    - Dependencies: None (use cases exist: `CreateConnectorPlaylistUseCase`, `UpdateConnectorPlaylistUseCase`)
    - Status: 🔜 Not Started
    - Notes:
        - API: `GET /playlists/{id}/links`, `POST /playlists/{id}/links`, `POST /playlists/{id}/links/{id}/sync`
        - Backed by existing `CreateConnectorPlaylistUseCase`, `UpdateConnectorPlaylistUseCase`
        - Playlist Links sub-page (`/playlists/:id/links`): linked connectors, sync direction, push/pull buttons
        - Enhance playlist listing to show connector linkage (which playlists are linked where)

---

### v0.4.1: CI/CD & Quality Hardening
**Goal**: Pause on features to harden the stack. CI pipeline, test suites, type audit, accessibility.

**Context**: The web UI has 6+ working pages (Dashboard, Library, Playlists, Imports, Workflows, Settings). Before adding more features, establish regression protection and quality gates.

#### Continuous Integration Epic

- [ ] **Automated Testing & Quality Pipeline**
    - Effort: S
    - What: GitHub Actions workflows for testing, linting, type checking
    - Why: Catch regressions before merge, enforce code quality standards automatically
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - **Test Workflow** (`.github/workflows/test.yml`):
            - Trigger: PR, push to main
            - Matrix: Python 3.13, 3.14
            - Steps: poetry install → pytest -m "" (all tests) → coverage report
            - Upload coverage to Codecov/Coveralls
            - Fail if coverage < 85%
        - **Code Quality Workflow** (`.github/workflows/quality.yml`):
            - ruff check (linting)
            - ruff format --check (formatting)
            - basedpyright (type checking)
            - bandit (security scanning)
            - Fail on any violations
        - **Pre-merge Requirements**: all tests passing, coverage threshold, zero lint/type errors
        - **Playwright CI notes**:
            - Set `timeout-minutes` on GitHub Actions jobs to prevent hung Playwright workers
            - Use `trace: 'on-first-retry'` for CI debugging — captures trace only on flaky retries
            - Cache Playwright browser binaries aggressively (`~/.cache/ms-playwright`)
            - Use `webServer` config in `playwright.config.ts` to auto-start Vite in CI
            - Disable MSW service worker in E2E — MSW intercepts interfere with Playwright's real network requests

- [ ] **Automated Changelog Generation**
    - Effort: XS
    - What: Conventional Commits + automated CHANGELOG.md generation
    - Why: Clear release notes, semantic versioning
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Conventional Commits format (feat/fix/BREAKING CHANGE)
        - commitlint + semantic-release or standard-version
        - GitHub Actions: automated release notes on tag push

#### Test Suite Epic

- [ ] **API & Frontend Test Suite**
    - Effort: M
    - What: Testing strategy for backend API, frontend components, and E2E flows
    - Why: Ensure quality, prevent regressions across the full stack
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - **Backend** (pytest): FastAPI TestClient, Pydantic schema validation, OpenAPI spec accuracy
        - **Frontend** (Vitest): Component tests with React Testing Library, API hook integration via MSW, accessibility via @axe-core/react
        - **MSW mocks**: If Orval codegen is set up in v0.3.0, MSW handlers are auto-generated with Faker.js data — dramatically reduces manual mock authoring effort
        - **E2E** (Playwright): Chromium only, desktop only, critical user flows (playlist CRUD, track search, workflow execution, import with progress)
        - **Coverage targets**: Backend 80% overall / 85% domain+application, Frontend 60%, E2E 100% critical flows
        - Frontend testing strategy in [04-frontend-architecture.md](web-ui/04-frontend-architecture.md)
        - **API route tests**: `tests/integration/api/` — auto-marked integration, share `db_session` fixture
        - **E2E location**: `web/e2e/` — co-located with frontend, runs against dev server
        - **Frontend tests**: Co-located with source in `web/src/`, MSW handlers in `web/src/test/`

#### Type Safety & Quality Epic

- [ ] **Audit and Resolve `# type: ignore` and `Any` Suppressions**
    - Effort: M
    - What: Systematic review of all pyright suppressions and `Any` annotations
    - Why: FastAPI route handlers and Pydantic response schemas depend on correct types; unresolved `Any` leaks surface as runtime serialisation errors
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - **Audit approach**: enumerate all suppressions; categorise as (a) legitimate boundary, (b) gap to fix, or (c) remove
        - **Known legitimate suppressions** (do not remove):
            - `prefect.py` — `isinstance` guard on workflow context dict
            - `domain/entities/operations.py` — `TYPE_CHECKING` import for Spotify `PersonalData`
        - **Likely fixable gaps**: `dict[str, Any]` in connector response parsing, `Any` in repository mappers
        - **Goal**: Maintain 0 errors/warnings baseline while raising the floor

- [ ] **Data Integrity Monitoring System**
    - Effort: M
    - What: Automated health checks for data consistency
    - Why: Early detection of primary mapping violations, orphaned mappings, duplicate tracks
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Primary mapping checks, missing primary checks, orphaned mapping detection, duplicate track detection
        - CLI: `narada status --health` and `narada status --integrity`
        - Automated reporting with counts and examples

#### CLI Polish Epic

- [ ] **Shell Completion Support**
    - Effort: S
    - What: Add shell completion for bash/zsh/fish
    - Why: Improves CLI usability and discoverability
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Use Typer's built-in completion support
        - Dynamic completion for workflows and connectors

- [ ] **Matcher Status Feedback**
    - Effort: S
    - What: Better progress reporting for matcher operations
    - Why: Matching is a long-running process with limited visibility
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Progress indicators for batch operations, success/failure counts in real-time
        - Service-specific rate limiting information, estimated completion time

---

### v0.5.0: PostgreSQL, Deployment & OAuth
**Goal**: Production-grade infrastructure. Now that the web UI works locally on SQLite, migrate to PostgreSQL, containerize, deploy to Fly.io, and implement web OAuth.

**Context**: The Repository + UoW pattern already fully abstracts database access — only connection config, driver, and a handful of SQLite-specific SQL constructs change. This milestone was deferred from v0.3.0 because the web UI works identically on SQLite for local development.

**What this unlocks**:
- **Remote hosting**: Fly.io, Railway, Render, or any cloud host
- **Prefect `.submit()` parallelism**: MVCC concurrent writes remove the `SharedSessionProvider` constraint
- **Web OAuth**: Spotify/Last.fm auth flows that work in a browser (not just CLI)
- **Concurrent web requests**: PostgreSQL connection pool handles multiple simultaneous users

#### Database Migration Epics

- [ ] **Evaluate and Select Database**
    - Effort: S
    - What: Document ADR (Architecture Decision Record) for PostgreSQL selection
    - Why: Commit to one technology before migration
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - **Leading candidate — PostgreSQL**: `asyncpg` driver, MVCC concurrency, JSONB columns, managed hosting (Neon/Supabase dev, Fly.io prod)
        - **Alternative — Turso (LibSQL)**: near-zero SQL migration cost, but smaller ecosystem
        - **Expected outcome**: PostgreSQL via `asyncpg`

- [ ] **Driver and Connection Config**
    - Effort: S
    - What: Swap `aiosqlite` → `asyncpg`, update engine/session config, remove SQLite PRAGMAs
    - Why: Core connectivity change
    - Dependencies: Database Selection
    - Status: 🔜 Not Started
    - Notes:
        - `db_connection.py`: URL → `postgresql+asyncpg://`, switch `NullPool` → `AsyncAdaptedQueuePool` (pool_size=5, max_overflow=10)
        - Remove `@event.listens_for` PRAGMA hook; remove SQLite connect args
        - `alembic.ini` + `alembic/env.py`: remove `render_as_batch=True`
        - Add `DATABASE_URL` env var

- [ ] **Schema and Query Compatibility**
    - Effort: M
    - What: Audit and migrate SQLite-specific SQL constructs
    - Why: Bulk upsert dialect, JSON → JSONB, DateTime → TIMESTAMPTZ
    - Dependencies: Driver and Connection Config
    - Status: 🔜 Not Started
    - Notes:
        - `sqlite_insert()` → `postgresql_insert()` in `base_repo.py`
        - JSON → JSONB column types in `db_models.py`; unlocks `@>` containment queries and GIN indexes
        - Verify no tz-naive inserts leak through
        - Generate fresh `initial_schema` migration for PostgreSQL; archive SQLite chain
        - Test fixtures: replace `sqlite+aiosqlite:///:memory:` with `pytest-postgresql`

- [ ] **Prefect Parallel Execution**
    - Effort: M
    - What: Remove `SharedSessionProvider`, enable per-task sessions, `.submit()` parallelism
    - Why: `SharedSessionProvider` was a SQLite workaround; PostgreSQL handles concurrent sessions
    - Dependencies: Schema and Query Compatibility
    - Status: 🔜 Not Started
    - Notes:
        - Each Prefect task creates its own session from the pool
        - Replace manual topological sort with `.submit()` + Prefect-native future resolution
        - Source and enricher nodes become concurrently executable where DAG allows

#### Containerization & Deployment Epics

- [ ] **Dockerfile and Docker Compose**
    - Effort: S
    - What: Multi-stage Dockerfile (Python builder + Vite builder + runtime) + docker-compose.yml
    - Why: Reproducible, portable build
    - Dependencies: Database Migration
    - Status: 🔜 Not Started
    - Notes:
        - Stage 1 (`python-builder`): Python 3.14 slim + Poetry → `/venv`
        - Stage 2 (`node-builder`): Node + pnpm → `web/dist/`
        - Stage 3 (`runtime`): copy both + `src/`; non-root `narada` user; expose port 8000
        - docker-compose.yml: `app` service + `postgres` service for local dev
        - Startup: `alembic upgrade head` before `uvicorn`

- [ ] **Environment Configuration Hardening**
    - Effort: S
    - What: Audit all configuration for env-var-driven config; document in `.env.example`
    - Why: Scattered hardcoded paths break in containers
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - `DATABASE_URL`, API credentials, `LOG_LEVEL`
        - `pydantic-settings` fails fast with clear error if required vars missing

- [ ] **Fly.io Deployment**
    - Effort: M
    - What: Deploy to Fly.io with managed PostgreSQL
    - Why: Primary hosting target; hobby tier sufficient
    - Dependencies: Dockerfile and Docker Compose
    - Status: 🔜 Not Started
    - Notes:
        - `fly.toml`: port 8000, health check on `/health`
        - Fly.io Postgres (managed), internal network `DATABASE_URL`
        - `fly secrets set` for API credentials
        - `alembic upgrade head` as release command
        - HTTPS automatic via Fly.io

- [ ] **Deployment Documentation**
    - Effort: XS
    - What: Single `DEPLOYMENT.md` covering local Docker dev, Fly.io deploy, updates, backups
    - Why: Reproducible deploys; reference for months-later return
    - Dependencies: Fly.io Deployment
    - Status: 🔜 Not Started

#### OAuth & Credential Epics

- [ ] **Spotify Token Persistence**
    - Effort: S
    - What: Move tokens from `.spotify_cache` file to `oauth_tokens` database table
    - Why: Containers have no persistent filesystem; tokens must survive restarts
    - Dependencies: Database Migration
    - Status: 🔜 Not Started
    - Notes:
        - New `oauth_tokens` table: `service`, `access_token`, `refresh_token`, `expires_at`, `scope`, `updated_at`
        - `TokenStorage` protocol: `FileTokenStorage` (CLI) and `DatabaseTokenStorage` (hosted)
        - Local CLI unchanged: `FileTokenStorage` + localhost server

- [ ] **Spotify OAuth Web Flow**
    - Effort: M
    - What: Replace browser-on-localhost OAuth with hosted callback
    - Why: `SpotifyTokenManager._run_browser_auth()` uses `webbrowser.open()` — non-functional headless
    - Dependencies: FastAPI app (v0.3.0), Spotify Token Persistence
    - Status: 🔜 Not Started
    - Notes:
        - Routes: `GET /auth/spotify` (redirect), `GET /auth/spotify/callback` (exchange code, store via `DatabaseTokenStorage`)
        - Config: `SPOTIFY_REDIRECT_URI = https://{FLY_APP_HOSTNAME}/auth/spotify/callback`
        - Graceful degradation: Spotify-dependent endpoints return `503` with link to `/auth/spotify` when no valid token
        - User flow: [01-user-flows.md § 1.1](web-ui/01-user-flows.md#11-connect-spotify)

- [ ] **Local Data Migration Tooling**
    - Effort: S
    - What: One-time script to export SQLite data to hosted PostgreSQL
    - Why: Existing listening history and play data would otherwise be lost
    - Dependencies: Fly.io Deployment
    - Status: 🔜 Not Started
    - Notes:
        - SQLAlchemy-based read from SQLite → write to PostgreSQL; same `db_models.py` models
        - Idempotent: `INSERT ... ON CONFLICT DO NOTHING`
        - CLI: `narada db migrate-to-remote --source sqlite:///data/db/narada.db --target $DATABASE_URL`

#### Performance Epic

- [ ] **Caching & Performance Strategy**
    - Effort: S
    - What: Simple caching for hobbyist web UI
    - Why: Reduce database queries without external dependencies
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - **Backend**: `lru_cache` for pure functions; ETags + Last-Modified; selectinload() (✅ already implemented)
        - **Frontend**: Tanstack Query stale-while-revalidate + optimistic updates
        - **Targets**: TTI <5s, FCP <2s, API p95 <500ms, Lighthouse >70

---

### v0.6.0: Apple Music & Data Quality
**Goal**: Add Apple Music as a connector and build data quality tools. These pair well — Apple Music creates new mapping scenarios, and DQ tools help manage them.

**Context**: Apple Music is functionally equivalent to Spotify (not metadata-only like Last.fm). Shared infrastructure (`InwardTrackResolver`, `BaseMatchingProvider`, retry policies, error classification) means the connector is mostly wiring. An existing stub at `infrastructure/connectors/apple_music/` provides the starting point.

**Key differences from Spotify**: Auth model is developer JWT + music user token (not OAuth 2.0). No "love" API — "Add to Library" is the equivalent of liking. Content equivalence via `catalogId` / `playParams.catalogId` parallels Spotify relinking.

#### Apple Music Connector Epics

- [ ] **Apple Music Auth & API Client**
    - Effort: M
    - What: Developer token (JWT/ES256), music user token, and `AppleMusicAPIClient(BaseAPIClient)` with httpx
    - Why: Foundation for all Apple Music operations
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Pydantic models for JSON:API response shapes
        - Refactor existing `AppleMusicErrorClassifier` to modern hook pattern
        - Reuse: `_shared/http_client.py`, `_shared/retry_policies.py`, `_shared/error_classifier.py`

- [ ] **Apple Music Track Resolution & Matching**
    - Effort: M
    - What: `AppleMusicInwardResolver` and `AppleMusicMatchingProvider`
    - Why: Track identity mapping is core to cross-service operations
    - Dependencies: Apple Music Auth & API Client
    - Status: 🔜 Not Started
    - Notes:
        - ISRC-first matching (excellent coverage in Apple Music catalog)
        - Content equivalence: `catalogId` vs `playParams.catalogId` — reuse dual-mapping pattern
        - Reuse: `_shared/inward_track_resolver.py`, `_shared/matching_provider.py`, `_shared/isrc.py`

- [ ] **Apple Music Library Operations**
    - Effort: M
    - What: Liked tracks (library additions), playlists (CRUD), catalog search
    - Why: User-facing capabilities for Apple Music in Narada workflows
    - Dependencies: Apple Music Auth & API Client, Track Resolution & Matching
    - Status: 🔜 Not Started
    - Notes:
        - Implements `LikedTrackConnector`, `PlaylistConnector`, `TrackMetadataConnector` protocols
        - Does NOT implement `LoveTrackConnector` — no "love" API in Apple Music

- [ ] **Apple Music Connector Facade & Registration**
    - Effort: S
    - What: `AppleMusicConnector(BaseAPIConnector)`, auto-discovery, config keys
    - Why: Plugs into connector ecosystem — all use cases gain Apple Music support automatically
    - Dependencies: Apple Music Library Operations
    - Status: 🔜 Not Started

- [ ] **Apple Music Likes Sync Integration**
    - Effort: S
    - What: Wire Apple Music into `sync_likes.py` as source/target
    - Why: "Like on Spotify, appears in Apple Music library" is a headline feature
    - Dependencies: Apple Music Connector Facade
    - Status: 🔜 Not Started

- [ ] **Apple Music Connector Tests**
    - Effort: M
    - What: Unit and integration tests for the full Apple Music stack
    - Why: Connector correctness is critical — bad resolution means wrong songs
    - Dependencies: All above epics
    - Status: 🔜 Not Started

#### Data Quality Epics

- [ ] **Unmapped Tracks Query**
    - Effort: S
    - What: `GetUnmappedTracksUseCase` — find tracks without mappings to specific connector
    - Why: Identify gaps like "tracks liked on Spotify but not mapped to Apple Music"
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Input: connector_name (optional), limit, offset
        - Output: Paginated list of tracks without mappings

- [ ] **Stale Tracks Detection**
    - Effort: S
    - What: `GetStaleTracksUseCase` — identify tracks with outdated connector metadata
    - Why: Automated data quality monitoring
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Configurable staleness threshold (e.g., 30 days)
        - Could trigger refresh workflows

- [ ] **Manual Track Mapping & Data Quality Management**
    - Effort: L
    - What: User control over track mapping — manual overrides, duplicate merging, confidence-based review
    - Why: Music services disagree on identity; users need authority over their library
    - Dependencies: Apple Music connector (creates mapping scenarios to manage)
    - Status: 🔜 Not Started
    - Notes:
        - API: `PATCH /tracks/{id}/mappings/{id}`, `DELETE /tracks/{id}/mappings/{id}`, `GET /connectors/{connector}/search`
        - Frontend: Manual mapping correction UI on Track Detail, "Unmapped" filter in Library, dashboard DQ alerts
        - Interactive step-through for bulk operations

### v0.7.0: Interactive Workflow Editor
**Goal**: Full editing capabilities with intuitive graphical interface

**Context**: v0.4.0 provides read-only workflow visualization + execution with a JSON editor. This milestone upgrades the JSON editor to a graphical drag-and-drop interface.

> User flow sketch: [01-user-flows.md § 6.5](web-ui/01-user-flows.md#65-visual-workflow-builder-v070-sketch)

#### Interactive Editing System Epics
- [ ] **Drag-and-Drop Node Creation**
    - Effort: M
    - What: Implement drag-and-drop node creation from node palette
    - Why: Need intuitive workflow creation experience
    - Dependencies: v0.4.0 (Workflow pages)
    - Status: Not Started
    - Notes:
        - Create node palette component
        - Implement drag source for node types
        - Add drop target handling
        - Include node positioning logic
        - Support undo/redo

- [ ] **Node Configuration Panel**
    - Effort: L
    - What: Create dynamic configuration panel for node parameters
    - Why: Users need to configure node behavior without JSON editing
    - Dependencies: v0.4.0 (Workflow pages)
    - Status: Not Started
    - Notes:
        - Generate form from node schema
        - Implement validation
        - Add help text and documentation
        - Support complex parameter types
        - Include preset configurations

- [ ] **Edge Management**
    - Effort: M
    - What: Implement interactive edge creation and deletion
    - Why: Users need to visually connect nodes
    - Dependencies: Drag-and-Drop Node Creation
    - Status: Not Started
    - Notes:
        - Add interactive connection points
        - Implement edge validation
        - Support edge deletion
        - Include edge styling
        - Handle edge repositioning

- [ ] **Visual Editor Save/Load**
    - Effort: S
    - What: Add save/load functionality for visual workflow editor
    - Why: Users need to persist their work from the drag-and-drop editor
    - Dependencies: Node Configuration Panel
    - Status: Not Started
    - Notes:
        - Implement save API endpoint
        - Add version control
        - Support auto-save
        - Include export/import
        - Handle validation during save

- [ ] **In-Editor Validation**
    - Effort: M
    - What: Add real-time validation of workflow structure
    - Why: Users need immediate feedback on workflow validity
    - Dependencies: Edge Management
    - Status: Not Started
    - Notes:
        - Validate node configurations
        - Check edge validity
        - Highlight errors
        - Provide guidance
        - Support auto-correction

---

### v0.8.0: LLM-Assisted Workflow Creation
**Goal**: Natural language workflow creation with LLM integration

> User flow sketch: [01-user-flows.md § 6.6](web-ui/01-user-flows.md#66-llm-assisted-workflow-creation-v080-sketch)

#### AI-Powered Creation Epics
- [ ] **LLM Integration Endpoint**
    - Effort: M
    - What: Create API endpoint for LLM-assisted workflow generation
    - Why: Foundation for natural language workflow creation
    - Dependencies: v0.4.0 (Workflow pages) implementation
    - Status: Not Started
    - Notes:
        - Implement secure LLM API wrapper
        - Add prompt engineering system
        - Support conversation context
        - Include result validation
        - Handle rate limiting

- [ ] **Workflow Generation from Text**
    - Effort: L
    - What: Implement system to translate natural language to workflow definitions
    - Why: Enable non-technical users to create workflows
    - Dependencies: LLM Integration Endpoint
    - Status: Not Started
    - Notes:
        - Design specialized prompts
        - Implement node mapping
        - Add configuration extraction
        - Include workflow validation
        - Support complex workflow patterns

- [ ] **Visualization Confirmation UI**
    - Effort: M
    - What: Create interface for reviewing and confirming LLM-generated workflows
    - Why: Users need to verify generated workflows before saving
    - Dependencies: Workflow Generation from Text
    - Status: Not Started
    - Notes:
        - Show visualization of generated workflow
        - Highlight key components
        - Allow immediate adjustments
        - Provide explanation of structure
        - Include confidence indicators

- [ ] **Conversation Interface**
    - Effort: L
    - What: Implement chat-style interface for workflow creation and refinement
    - Why: Natural conversation provides better user experience
    - Dependencies: Visualization Confirmation UI
    - Status: Not Started
    - Notes:
        - Create chat UI component
        - Implement conversation history
        - Add contextual suggestions
        - Support workflow references
        - Include guided assistance

- [ ] **LLM Feedback Loop**
    - Effort: M
    - What: Create system for user feedback on LLM-generated workflows
    - Why: Improve generation quality through user input
    - Dependencies: Conversation Interface
    - Status: Not Started
    - Notes:
        - Implement feedback collection
        - Add result quality tracking
        - Create feedback insights dashboard
        - Support model improvement
        - Include A/B testing

---

### v1.0.0: Production-Ready Multi-User Platform
**Goal**: Transform into production-ready platform with robust user management and collaboration features

**Context**: Adds authentication, per-user data isolation, version control, and production monitoring. Enables sharing with friends (small-scale multi-user, not enterprise SaaS). Per-user isolation is significant architectural change (shared canonical tracks, per-user playlists/workflows/likes/plays).

#### Production Infrastructure Epics
- [ ] **User Authentication System**
    - Effort: M
    - What: Simple, secure authentication for friends (<10 users)
    - Why: Basic auth for trusted users, OWASP security without enterprise complexity
    - Dependencies: FastAPI Service (v0.3.0)
    - Status: 🔜 Not Started
    - Notes:
        - **Authentication** (simplified for hobbyist scale):
            - **Email/Password**: Primary auth method
                - bcrypt password hashing (cost factor 12)
                - Simple password complexity (8+ chars, mixed case, number)
                - Password reset via email link
            - **Optional Spotify OAuth**: Social login (already integrated with Spotify API)
                - No Google, GitHub (reduces OAuth complexity)
            - **JWT Tokens**: Short-lived only (15min expiry)
                - No refresh tokens (users can re-login)
                - Authorization header (not HttpOnly cookies)
        - **Security Baseline** (OWASP without enterprise theater):
            - bcrypt password hashing ✅
            - Parameterized SQL queries (already done ✅)
            - Simple rate limiting: 10,000 requests/hour globally (sanity check)
            - HTTPS via reverse proxy (Caddy/nginx with Let's Encrypt)
            - Email verification optional (friends are trusted)
        - **NOT Included** (too complex for <10 friends):
            - MFA / TOTP / SMS (security theater for trusted users)
            - Refresh tokens (just re-login after 15min)
            - Session revocation (tokens expire naturally)
            - RBAC (you're admin, everyone else is user)
            - Per-user rate limiting
        - **FastAPI Implementation**:
            - `fastapi-users` library (handles OAuth2 + JWT patterns)
            - Dependency injection: `CurrentUser = Depends(get_current_user)`
        - **Database Schema**:
            - users table: id, email, hashed_password, is_active, created_at
            - oauth_accounts table (optional): provider, provider_user_id, access_token

- [ ] **Workflow Version Control**
    - Effort: L
    - What: Implement version tracking and management for workflows
    - Why: Users need to track changes and revert when needed
    - Dependencies: User Authentication System
    - Status: Not Started
    - Notes:
        - Add versioning system
        - Implement diff visualization
        - Support rollback
        - Include branching
        - Add merge capabilities

- [ ] **Production Monitoring System**
    - Effort: XS
    - What: Simple logging for hobbyist debugging (<10 users)
    - Why: Logs are sufficient for troubleshooting with trusted friends
    - Dependencies: FastAPI Service (v0.3.0)
    - Status: 🔜 Not Started
    - Notes:
        - **Monitoring Philosophy** (hobbyist reality):
            - If the app breaks, you'll notice (you're using it)
            - Friends will email/text you if something is broken
            - Logs to file are enough for debugging
        - **Logging** (already implemented ✅):
            - **Loguru JSON logging to file** (current implementation)
            - Rotation: 10MB, Retention: 1 week, Compression: zip
            - Structured fields: level, timestamp, module, message, context
            - Console logs for development, file logs for production
        - **Optional Email Alerts** (if desired):
            - Send email on ERROR/CRITICAL log entries
            - Simple SMTP integration (Gmail, SendGrid)
            - Rate limited (max 10 emails/hour, avoid spam)
        - **Optional Log Viewer** (nice-to-have):
            - Simple web UI endpoint: `/admin/logs` (admin only)
            - View recent logs, filter by level, search by text
            - Tail live logs (SSE streaming)
        - **NOT Included** (too complex for hobbyist):
            - OpenTelemetry instrumentation
            - Grafana + Prometheus + Tempo stack
            - Datadog / New Relic / Honeycomb (paid)
            - Distributed tracing
            - PagerDuty / Slack alerting
            - Metrics dashboards

- [ ] **Workflow Execution Dashboard**
    - Effort: L
    - What: Build visual dashboard for workflow execution monitoring
    - Why: Users need visibility into running workflows
    - Dependencies: Production Monitoring System
    - Status: Not Started
    - Notes:
        - Create real-time execution visualization
        - Add performance metrics
        - Implement log viewer
        - Support debugging tools
        - Include execution history

#### Database

PostgreSQL migration completed in v0.5.0 as a prerequisite for remote hosting and web deployment. No additional database scaling work expected for <10 users at v1.0.0 scale. If write contention surfaces, evaluate read replicas or connection pooling (PgBouncer) before considering sharding.

---

