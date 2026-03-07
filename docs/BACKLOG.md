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

- [x] **SSE Progress Provider**
    - Status: ✅ Completed (2026-03-03)
    - Effort: M
    - What: `SSEProgressProvider` implementing `ProgressSubscriber` protocol — serializes `ProgressEvent` to SSE `data:` frames
    - Why: Real-time progress is the defining UX for import operations; this validates the progress architecture
    - Dependencies: FastAPI app (v0.3.0)
    - Notes:
        - Implements same `ProgressSubscriber` protocol as `RichProgressProvider` (CLI)
        - Registered with `AsyncProgressManager.subscribe()` — same pub/sub mechanism
        - Endpoints: `GET /operations/{id}/progress` (SSE stream), `GET /operations` (list active)
        - Used FastAPI's built-in `EventSourceResponse` (Starlette) — no `sse-starlette` dependency needed
        - Production headers: `X-Accel-Buffering: no`, `Cache-Control: no-cache`
        - Concurrent operation limit (HTTP 429 with `Retry-After`) prevents resource exhaustion

#### Import Endpoints Epic

- [x] **Import API Routes**
    - Status: ✅ Completed (2026-03-03)
    - Effort: S
    - What: Import trigger endpoints backed by existing use cases
    - Why: Users need to trigger imports from the web UI
    - Dependencies: SSE Progress Provider
    - Notes:
        - `POST /imports/spotify/likes` → `SyncLikesUseCase`
        - `POST /imports/lastfm/history` → `ImportPlayHistoryUseCase` (Last.fm mode)
        - `POST /imports/spotify/history` → `ImportPlayHistoryUseCase` (Spotify GDPR mode)
        - `POST /imports/lastfm/likes` → Last.fm likes export
        - `GET /imports/checkpoints` → sync checkpoint query
        - Non-blocking execution via `asyncio.create_task` — SSE streams progress back

#### Imports Frontend Epic

- [x] **Imports Page + Progress UI**
    - Status: ✅ Completed (2026-03-03)
    - Effort: M
    - What: Imports page with operation triggers, SSE progress display, and checkpoint status
    - Why: The web UI isn't useful until users can trigger operations
    - Dependencies: Import API Routes
    - Notes:
        - `useOperationProgress` hook using `eventsource-parser` v3 + native `fetch()` (not `@microsoft/fetch-event-source` or native `EventSource`)
        - `connectToSSE` adapter in `sse-client.ts` for testability — async iterable over parsed SSE events
        - Abort-aware `Promise.race` loop prevents dangling async iterators in React cleanup
        - `OperationProgress` shared component (progress bar, status messages, ETA, items/sec)
        - Imports page (`/imports`): four import operations with per-mutation toast error handling
        - Checkpoint data fetched once at page level, passed as props (single TanStack Query subscription)
        - Orval-generated query keys for automatic cache invalidation on operation complete

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
            - Connector metadata (Spotify explicit flag, Last.fm play counts, etc.)
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
**Goal**: Landing page with aggregate statistics and per-connector breakdowns.

**Context**: The dashboard ties everything together — it's the first thing users see. Consolidated the originally-planned 4 use cases into a single `GetDashboardStatsUseCase` that collects all counts in one UoW transaction.

**Deferred to future versions**: Sync status overview (last synced timestamps), metadata freshness tracking, recent activity feed, connector health badges. These are lower-value without more connectors and will fit naturally into v0.4.0+ when workflow execution history exists.

#### Dashboard Stats Epic

- [x] **Dashboard Stats Use Case**
    - Effort: S
    - Status: ✅ Completed (2026-03-06)
    - What: `GetDashboardStatsUseCase` — single use case collecting aggregate counts from 5 repositories
    - Why: Dashboard needs counts: "1,234 tracks total, 456 liked, 56,789 plays, 12 playlists"
    - Dependencies: None
    - Notes:
        - Consolidated planned `GetTrackStatsUseCase` + `GetConnectorMappingStatsUseCase` into one use case
        - Output: `DashboardStatsResult` (total_tracks, total_plays, total_playlists, total_liked, tracks_by_connector, liked_by_connector)
        - New repository count methods: `count_all_tracks()`, `count_all_plays()`, `count_all_playlists()`, `count_total_liked()`, `count_tracks_by_connector()`, `count_liked_by_service()`
        - Batch `GROUP BY` query for liked-by-service (avoids N+1 per connector)
        - Protocol methods added to all 5 repository interfaces

- [x] **Dashboard Stats Endpoint**
    - Effort: XS
    - Status: ✅ Completed (2026-03-06)
    - What: `GET /api/v1/stats/dashboard` — single endpoint for all dashboard data
    - Why: Dashboard page needs aggregate stats in one request
    - Dependencies: Dashboard Stats Use Case
    - Notes:
        - `DashboardStatsSchema` with `from_attributes=True` for zero-copy attrs→Pydantic conversion
        - OpenAPI schema + Orval codegen for typed frontend hooks

- [x] **Dashboard Page**
    - Effort: M
    - Status: ✅ Completed (2026-03-06)
    - What: Landing page with stat cards and per-connector breakdowns
    - Why: First thing users see — ties the whole app together
    - Dependencies: Dashboard Stats Endpoint
    - Notes:
        - 4 stat cards: Total Tracks (hero), Total Plays, Liked Tracks, Playlists
        - Per-connector breakdowns with `ConnectorIcon` on Tracks and Liked cards
        - `DashboardSkeleton` loading state, `EmptyState` with Settings CTA for fresh databases
        - Error state with typed error messages
        - `formatCount()` utility for locale-aware thousand separators
        - Staggered fade-up entrance animations

---

### v0.4.0: Workflow Persistence & Visualization (Vertical Slice 5a)
**Goal**: Persist workflow definitions to the database and display them as interactive DAGs in the web UI. Foundation for execution and editing in subsequent milestones.

**Context**: Workflows are Narada's core value proposition — declarative pipelines composing user-defined criteria. Currently stored as JSON files and run via CLI only. This milestone adds database persistence, a CRUD API, a template system for built-in workflows, and React Flow DAG visualization. No execution from the web yet — users can see and manage workflows visually, but run them via CLI until v0.4.1.

**What this unlocks**: Web-based workflow management, visual pipeline understanding, template-based onboarding for new users.

**Key tech choices**:
- **React Flow (xyflow) v12+** with **ELKjs** for layered auto-layout (superior to Dagre for complex DAGs)
- **Zustand** for React Flow canvas state (nodes, edges, viewport) — React Flow's officially recommended state management pattern. All server state remains in Tanstack Query.
- **7 custom node components** color-coded by category: Source (blue), Enricher (purple), Filter (orange), Sorter (gold), Selector (teal), Combiner (pink), Destination (green)

#### Workflow Persistence Epic

- [ ] **Workflow Database Table + Repository**
    - Effort: S
    - Status: 🔜 Not Started
    - What: New `workflows` table, Alembic migration, `WorkflowRepositoryProtocol`, SQLAlchemy implementation
    - Why: Foundation for all web workflow features
    - Dependencies: None
    - Notes:
        - **New `workflows` table**: `id INTEGER PK`, `name VARCHAR(255) NOT NULL`, `description TEXT`, `definition JSON NOT NULL`, `is_template BOOLEAN DEFAULT FALSE`, `source_template VARCHAR(100)`, `created_at DATETIME(tz)`, `updated_at DATETIME(tz)`
        - `definition` column stores the complete `WorkflowDef` serialization (id, name, description, version, tasks[]). JSON on SQLite, JSONB on PostgreSQL.
        - Domain entity: `Workflow` (attrs, frozen) in `domain/entities/workflow.py` — wraps `WorkflowDef` with database identity + template metadata
        - Repository: `WorkflowRepositoryProtocol` in `domain/repositories/`, impl in `infrastructure/persistence/repositories/workflow/`

- [ ] **Workflow CRUD Use Cases**
    - Effort: S
    - Status: 🔜 Not Started
    - What: List, Get, Create, Update, Delete use cases for workflow persistence
    - Why: API needs use cases to back route handlers
    - Dependencies: Workflow Database Table
    - Notes:
        - 5 use cases: `ListWorkflowsUseCase`, `GetWorkflowUseCase`, `CreateWorkflowUseCase`, `UpdateWorkflowUseCase`, `DeleteWorkflowUseCase`
        - Create/Update validates definition via existing `validate_workflow_def()` before persisting
        - Templates are read-only — Update/Delete reject `is_template=True` workflows with `403 Forbidden`
        - **Template seeding**: on startup, upsert JSON definitions from `definitions/` as templates with `is_template=True`

- [ ] **Workflow API Routes**
    - Effort: S
    - Status: 🔜 Not Started
    - What: REST endpoints for workflow CRUD + validation + node reference
    - Why: Web UI needs API access to workflow data
    - Dependencies: Workflow CRUD Use Cases
    - Notes:
        - `GET /workflows` (with `?include_templates=true`), `POST /workflows`, `GET /workflows/{id}`, `PATCH /workflows/{id}`, `DELETE /workflows/{id}`
        - `POST /workflows/validate` — structural validation without execution, returns `{ valid, errors[] }`
        - `GET /workflows/nodes` — node type reference for editor (type key, category, description, required/optional config keys). Introspects the existing node registry.
        - Pydantic schemas: `WorkflowSummarySchema`, `WorkflowDetailSchema`, `ValidationErrorSchema`, `NodeTypeInfoSchema`

#### Workflow Frontend Epic

- [ ] **Workflow List Page**
    - Effort: S
    - Status: 🔜 Not Started
    - What: Workflow listing with template badges, type indicators, CRUD actions
    - Why: Entry point for workflow management in the web UI
    - Dependencies: Workflow API Routes
    - Notes:
        - Table: Name, Description (truncated), Task Count, Node Type badges (colored category dots), Template badge, Created, Actions
        - "New Workflow" button, "Use Template" clone action for template rows
        - Empty state: "No workflows yet. Create your first workflow or start from a template." [Create Workflow] [Browse Templates]
        - Orval codegen for typed hooks + MSW mocks

- [ ] **React Flow DAG Visualization**
    - Effort: M
    - Status: 🔜 Not Started
    - What: Read-only React Flow DAG on Workflow Detail page with custom nodes per category
    - Why: Visual pipeline understanding is the key differentiator for the web UI
    - Dependencies: Workflow API Routes
    - Notes:
        - **`@xyflow/react` v12+** with **ELKjs** for auto-layout (layered, left-to-right)
        - 7 custom node components in `web/src/components/workflow/nodes/`: `SourceNode`, `EnricherNode`, `FilterNode`, `SorterNode`, `SelectorNode`, `CombinerNode`, `DestinationNode`
        - `BaseWorkflowNode` shared component: category color, icon, type label, config summary
        - Minimap in bottom-right corner, zoom controls, pan interaction
        - **Zustand store** (`useWorkflowStore`) for React Flow canvas state (nodes, edges, viewport)
        - Read-only in v0.4.0: no drag, connect, or delete interactions
        - Responsive: DAG fills available width, auto-zoom to fit on load

---

### v0.4.1: Workflow Execution & Run History (Vertical Slice 5b)
**Goal**: Execute workflows from the web with live per-node status on the DAG and persist complete run history.

**Context**: v0.4.0 delivered workflow persistence and read-only visualization. This milestone adds one-click execution with SSE progress streaming, per-node status visualization on the DAG (the "live pipeline" experience), and run history with per-node inspection. Leverages SSE infrastructure from v0.3.1 and Prefect execution engine from v0.2.7.

**What this unlocks**: Full web-based workflow lifecycle — create, visualize, execute, review history. Users see their pipeline executing in real time, node by node.

**Innovative patterns** (borrowed from n8n, Temporal, Prefect):
- **Per-node live status on DAG**: Pending -> Running (animated pulse) -> Completed (green + track count) -> Failed (red + error)
- **Definition snapshots**: Each run stores the exact `WorkflowDef` JSON used, so editing a workflow never breaks historical run views
- **Pre-flight connector validation**: Checks required connectors before execution starts, not after the first node fails
- **Execution timeline**: Horizontal bar chart of per-node durations showing where time was spent

#### Execution Infrastructure Epic

- [ ] **Workflow Run Tables + Repository**
    - Effort: S
    - Status: 🔜 Not Started
    - What: `workflow_runs` + `workflow_run_nodes` tables, Alembic migration, repository
    - Why: Persist execution history with per-node granularity
    - Dependencies: v0.4.0 (workflows table)
    - Notes:
        - **`workflow_runs` table**: `id INTEGER PK`, `workflow_id INTEGER FK(workflows.id, CASCADE)`, `status VARCHAR(20) NOT NULL` (PENDING/RUNNING/COMPLETED/FAILED/CANCELLED), `definition_snapshot JSON NOT NULL`, `started_at DATETIME(tz)`, `completed_at DATETIME(tz)`, `duration_ms INTEGER`, `output_track_count INTEGER`, `output_playlist_id INTEGER FK(playlists.id, SET NULL)`, `error_message TEXT`
        - **`workflow_run_nodes` table**: `id INTEGER PK`, `run_id INTEGER FK(workflow_runs.id, CASCADE)`, `node_id VARCHAR(100) NOT NULL`, `node_type VARCHAR(100) NOT NULL`, `status VARCHAR(20) NOT NULL`, `started_at DATETIME(tz)`, `completed_at DATETIME(tz)`, `duration_ms INTEGER`, `input_track_count INTEGER`, `output_track_count INTEGER`, `error_message TEXT`, `execution_order INTEGER NOT NULL`
        - `definition_snapshot`: frozen copy of WorkflowDef JSON at execution time — critical for historical run DAG accuracy

- [ ] **RunWorkflow + GetWorkflowRuns Use Cases**
    - Effort: M
    - Status: 🔜 Not Started
    - What: Execute workflow with run recording, list/get runs with node details
    - Why: Web needs execution with history tracking
    - Dependencies: Workflow Run Tables
    - Notes:
        - `RunWorkflowUseCase`: snapshot definition, create run record, delegate to existing `run_workflow()` in `prefect.py`, update node records via callback
        - Pre-flight validation: check required connectors before execution (return 503 with `{ required_connectors: ["spotify"] }`)
        - `GetWorkflowRunsUseCase`: paginated run list + single run with per-node details
        - `execute_node` in `prefect.py` gains a callback to update `workflow_run_nodes` and emit SSE `node_status` events

- [ ] **Workflow Execution API Routes + SSE Enhancement**
    - Effort: S
    - Status: 🔜 Not Started
    - What: Run endpoint, runs list/detail endpoints, SSE node_status events
    - Why: Web UI needs execution trigger and history access
    - Dependencies: RunWorkflow Use Case
    - Notes:
        - `POST /workflows/{id}/run` -> `{ operation_id, run_id }` (409 Conflict if already running)
        - `GET /workflows/{id}/runs` (paginated), `GET /workflows/{id}/runs/{run_id}` (with per-node data)
        - New SSE event type: `node_status` with `{ node_id, node_type, status, input_track_count?, output_track_count?, duration_ms?, error_message? }`
        - Extends existing `OperationRegistry` + `SSEProgressProvider` infrastructure

#### Execution Frontend Epic

- [ ] **Live DAG Execution Visualization**
    - Effort: M
    - Status: 🔜 Not Started
    - What: Per-node status animation on React Flow DAG during workflow execution
    - Why: The "live pipeline" experience is the web UI's key differentiator
    - Dependencies: React Flow DAG (v0.4.0)
    - Notes:
        - Node status states: Pending (grey dashed border) -> Running (blue pulse animation) -> Completed (green + track count badge) -> Failed (red + error icon)
        - Edge particle animation on completed edges showing data flow direction
        - Current node glow effect using `--shadow-glow` design token
        - SSE `node_status` events drive Zustand store updates -> React Flow re-renders
        - Overall progress bar + message area below DAG
        - `useOperationProgress` hook extended with `onNodeStatus` callback

- [ ] **Run History & Node Inspection**
    - Effort: M
    - Status: 🔜 Not Started
    - What: Execution history table, run detail with per-node inspection, historical run DAG overlay
    - Why: Users need to review past executions and diagnose failures
    - Dependencies: Live DAG Visualization
    - Notes:
        - Execution History table below DAG: Run #, Started, Duration, Status badge, Output track count, "View" action
        - Viewing a historical run renders the DAG from `definition_snapshot` (not current definition) with execution overlay
        - Per-node inspection panel: click a node to see track count delta (e.g., "Filter removed 78 of 120 tracks"), execution time, sample output tracks (first 10 titles)
        - Execution timeline: horizontal bar chart of per-node durations (Temporal-inspired)

- [ ] **WorkflowSummary Last Run Integration**
    - Effort: XS
    - Status: 🔜 Not Started
    - What: Add `last_run` to WorkflowSummary, update list page with Run button + status badges
    - Why: Users need to see run status at a glance and trigger runs from the list
    - Dependencies: Run History
    - Notes:
        - `GET /workflows` response adds `last_run: { id, status, completed_at, output_track_count } | null`
        - Status badges: Never Run (grey), Running (blue animated), Completed (green), Failed (red)
        - "Run" button per row with confirmation dialog

---

### v0.4.2: Visual Workflow Editor & Preview (Vertical Slice 5c)
**Goal**: Create and edit workflows visually with a drag-and-drop graph editor, real-time validation, and dry-run preview.

**Context**: v0.4.0 added persistence and visualization, v0.4.1 added execution and run history. This milestone completes the web workflow lifecycle with visual creation and editing. Uses React Flow's interactive mode with a node palette sidebar and configuration panel — the full n8n/Windmill-style visual builder experience.

**What this unlocks**: Full self-service workflow lifecycle from the web. Users no longer need CLI access or filesystem editing to create/modify workflows.

**Key UX patterns** (borrowed from n8n, Windmill, React Flow Pro):
- **Three-panel layout**: Node Palette (left) | React Flow Canvas (center) | Config Panel (right)
- **Drag-and-drop from palette**: DnD context + `screenToFlowPosition` for precise node placement
- **Undo/redo history stack**: Zustand state snapshots for every editor action
- **Edge validation**: Prevents self-loops, duplicate edges, and invalid node connections
- **Auto-layout on demand**: ELKjs re-arranges nodes after manual edits

#### Editor Infrastructure Epic

- [ ] **Workflow Preview/Dry-Run Endpoint**
    - Effort: M
    - Status: 🔜 Not Started
    - What: Execute workflow without destination writes, return per-node output summaries
    - Why: Users need to test workflows before committing to playlist changes
    - Dependencies: RunWorkflow Use Case (v0.4.1)
    - Notes:
        - `PreviewWorkflowUseCase`: execute with `dry_run=True` flag in WorkflowContext — destination nodes become no-ops
        - `POST /workflows/{id}/preview` (saved) and `POST /workflows/preview` (unsaved definition)
        - Returns: output tracks (first 20), per-node track counts, per-node sample tracks (first 10 titles per node)
        - Enricher nodes still call external APIs for realistic output — only destination writes are skipped
        - SSE progress during preview execution (reuses `useOperationProgress` hook)

#### Editor Frontend Epic

- [ ] **Visual Workflow Editor Canvas**
    - Effort: L
    - Status: 🔜 Not Started
    - What: Interactive React Flow editor with drag, connect, delete, select, and auto-layout
    - Why: Users need to build workflows visually without editing JSON
    - Dependencies: React Flow DAG (v0.4.0), Workflow API (v0.4.0)
    - Notes:
        - **React Flow interactive mode**: drag nodes, connect edges, delete nodes/edges, select + multi-select
        - **Zustand editor store** extends the read-only viewer store: adds undo/redo (history stack of node/edge snapshots), add/remove node, add/remove edge, update node config
        - **ELKjs auto-layout** button: re-arrange nodes automatically after manual edits
        - **Edge validation**: `onConnect` handler validates connections (no self-loops, no duplicate edges, type compatibility)
        - **Connection rules**: source nodes have no inputs, destination nodes have no outputs, combiners accept multiple inputs
        - Create (`/workflows/new`) and Edit (`/workflows/:id/edit`) routes share the same editor component

- [ ] **Node Palette Sidebar**
    - Effort: M
    - Status: 🔜 Not Started
    - What: Draggable node type sidebar organized by category with search
    - Why: Users need to discover and add available node types to the canvas
    - Dependencies: Node reference API endpoint (v0.4.0), Editor Canvas
    - Notes:
        - Left sidebar with 7 category accordions: Source (3 nodes), Enricher (3), Filter (9), Sorter (8), Selector (2), Combiner (4), Destination (2)
        - Drag-and-drop from palette to canvas using React Flow's DnD pattern (DnD context + `screenToFlowPosition`)
        - Visual ghost preview during drag
        - Search/filter within the palette
        - Each entry shows: category-colored icon, type name, brief description
        - Data sourced from `GET /workflows/nodes` endpoint

- [ ] **Node Configuration Panel**
    - Effort: M
    - Status: 🔜 Not Started
    - What: Dynamic form panel for configuring the selected node's parameters
    - Why: Users need to configure node behavior without editing JSON
    - Dependencies: Editor Canvas
    - Notes:
        - Right sidebar appears when a node is selected on the canvas
        - Dynamic form generated from node's config schema (required/optional fields)
        - Field types: text input, number input, select dropdown, boolean toggle, date picker
        - Validation: required field indicators, type checking, range validation
        - Changes update the node's `data.config` in Zustand store immediately
        - Header shows: node category badge, type name, description

- [ ] **Preview/Dry-Run Panel**
    - Effort: S
    - Status: 🔜 Not Started
    - What: Preview results display panel showing track output and per-node summaries
    - Why: Users need to test workflows before running them for real
    - Dependencies: Preview endpoint, Editor Canvas
    - Notes:
        - Bottom drawer or right panel shows preview results
        - Output track count, first 20 track titles with artist/album
        - Per-node summary: track count at each stage through the pipeline
        - "Preview mode -- no playlists were created or modified" banner
        - SSE progress during preview execution

- [ ] **Editor Toolbar**
    - Effort: S
    - Status: 🔜 Not Started
    - What: Top toolbar with save, preview, run, undo/redo, and layout actions
    - Why: Users need quick access to editor actions
    - Dependencies: Editor Canvas, Preview Panel
    - Notes:
        - Buttons: Save, Preview (dry-run), Run, Undo, Redo, Auto-Layout, Zoom to Fit, Delete Selected
        - Save serializes React Flow state -> WorkflowDef JSON -> `POST /workflows` or `PATCH /workflows/{id}`
        - Unsaved changes indicator (dot on Save button, browser `beforeunload` warning)
        - Keyboard shortcuts: Ctrl+S (save), Ctrl+Z (undo), Ctrl+Shift+Z (redo), Delete (remove selected), Ctrl+A (select all)

---

### v0.4.3: Connector Playlist Linking (Vertical Slice 6)
**Goal**: Link canonical playlists to external service playlists and sync changes from the web.

**Context**: Use cases already exist (`CreateConnectorPlaylistUseCase`, `UpdateConnectorPlaylistUseCase`). This milestone adds the API routes, frontend UI, and bidirectional sync controls. Completes the web UI feature set.

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

### v0.4.4: CI/CD & Quality Hardening
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

### v0.7.0: Advanced Workflow Features
**Goal**: Power-user workflow capabilities building on the visual editor foundation from v0.4.2.

**Context**: v0.4.2 delivered the core visual drag-and-drop workflow editor with node palette, configuration panel, undo/redo, and preview. This milestone adds advanced features for power users who build complex multi-branch pipelines.

#### Advanced Editor Epics

- [ ] **Sub-Flows & Node Grouping**
    - Effort: M
    - What: Group related nodes into collapsible sub-flows for complex workflows
    - Why: Large workflows (10+ nodes) become hard to navigate without grouping
    - Dependencies: v0.4.2 (Visual Editor)
    - Status: 🔜 Not Started
    - Notes:
        - React Flow's `parentId` and `extent: 'parent'` for nested nodes
        - Labeled group nodes as collapsible containers
        - Expand/collapse toggle to show/hide group internals
        - Groups can be named and reused as "snippets"

- [ ] **Workflow Versioning & Diff**
    - Effort: L
    - What: Track workflow definition changes over time with visual diff
    - Why: Users need to understand what changed between versions and revert if needed
    - Dependencies: v0.4.2 (Visual Editor)
    - Status: 🔜 Not Started
    - Notes:
        - `workflow_versions` table: version number, definition snapshot, created_at, change_summary
        - Side-by-side DAG diff view: added nodes (green), removed nodes (red), modified nodes (yellow)
        - Revert to any previous version
        - Auto-version on save (not every keystroke)

- [ ] **Workflow Import/Export**
    - Effort: S
    - What: Export workflow definitions as JSON files, import from file or URL
    - Why: Share workflows between Narada instances, backup/restore
    - Dependencies: v0.4.2 (Visual Editor)
    - Status: 🔜 Not Started
    - Notes:
        - Export: download WorkflowDef as `.json` file
        - Import: upload `.json` file, validate, create new workflow
        - Share via URL (encoded definition in query param for small workflows)

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

