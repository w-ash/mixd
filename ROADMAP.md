
# Project Narada Backlog

**Current Development Version**: 0.2.7
**Current Initiative**: Advanced Workflow Features

This document is a high level overview of Project Narada's development roadmap. It primarily explains the why, at a product manager level, of our future features. It also includes high level architectural decisions, with a focus on the why of those descriptions.

**Work Tracking**: See `.claude/work/WORK.md` or root `WORK.md` for active epic/task tracking. This ROADMAP.md is for strategic planning and version milestones, not day-to-day task execution.

## Reference Guide 📋

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

## Version Change-log 🆕

### v0.2.1: Like Sync
Sync Spotify likes → Narada → Last.fm with checkpoint-based resumable operations.
- Import Spotify Likes | Export to Last.fm | Database Checkpoints

### v0.2.2: Play History
Import complete listening history from Spotify GDPR exports and Last.fm API.
- Spotify GDPR JSON Import | Last.fm History Import | Enhanced Track Resolution

### v0.2.3: Clean Architecture Foundation
Rebuilt codebase with Clean Architecture + DDD for reliability and future growth.
- /src Structure Migration | Service Layer Reorganization | Matcher System Modernization | Workflow Node Architecture

### v0.2.4: Playlist Updates
Intelligent playlist automation with differential updates preserving metadata and ordering.
- Comprehensive CRUD Operations | Differential Update Algorithms | Playlist Diff Engine | UnitOfWork Pattern

### v0.2.5: Workflow Transformation Expansion
Filter and sort playlists based on listening history and play patterns.
- Play History Filter/Sort Nodes | Time Window Support | Import Quality Foundation | Database Performance Indexes

### v0.2.6: Enhanced Playlist Naming
Dynamic playlist names/descriptions using template parameters ({track_count}, {date}, {time}).
- Template-Based Naming | Parameter Substitution | Create/Update Node Support

---

## Infrastructure Readiness Matrix

Visual guide to infrastructure capabilities across version milestones (hobbyist scale: <10 users):

| Capability | v0.2.7 (CLI) | v0.5.0 (Web UI) | v1.0.0 (Multi-User <10) |
|------------|--------------|-----------------|-------------------------|
| **Testing** | ✅ 827 tests, <1min | ✅ + E2E (Chromium only) | ✅ Same as v0.5.0 |
| **CI/CD** | ⚠️ Manual | ✅ GitHub Actions (pytest, ruff) | ✅ Same as v0.5.0 |
| **Deployment** | ✅ Poetry install | ✅ Docker + Fly.io | ✅ Same as v0.5.0 |
| **Observability** | ✅ Loguru JSON logs | ✅ Same as v0.2.7 | ✅ + Email alerts (optional) |
| **Authentication** | ❌ Not needed | ❌ Not needed | ✅ Email/password + Spotify OAuth |
| **Database** | ✅ SQLite | ✅ SQLite | ✅ SQLite (PostgreSQL if needed) |
| **Caching** | ❌ Not needed | ✅ Tanstack Query + lru_cache | ✅ Same as v0.5.0 |
| **Security** | ✅ Env vars, secrets | ✅ + CORS | ✅ + HTTPS, bcrypt |

**Legend**: ✅ Ready | ⚠️ Needs work | ❌ Not needed

**Note**: Right-sized for hobbyist project (<10 users). No Redis, CDN, MFA, load testing, or enterprise observability. Focus on quality code over production infrastructure.

---

## Technology Decision Records

Key architecture & tech choices (see CLAUDE.md for migration details):

- **Python 3.14+ & attrs**: Modern type syntax (`str | None`, `class Foo[T]`), immutable domain entities with slots
- **SQLite → PostgreSQL**: SQLite for <10 users; migrate if write lock contention occurs
- **Vite 6+ / Vitest**: 10x faster HMR than Webpack, native ESM + TypeScript
- **Tailwind CSS v4**: Rust engine (10x performance), @theme design tokens
- **Pydantic v2**: 5-50x faster validation, `from_attributes=True`
- **Clean Architecture + DDD**: Composable workflows, isolated APIs, testable logic (see docs/ARCHITECTURE.md)

---

## Planned Roadmap 🚀

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

- [ ] **Advanced Transformer Workflow nodes**
    - Status: 🔄 In Progress (~40% complete)
    - Effort: M
    - What: Implement additional transformer nodes for workflow system
    - Why: More transformation options enable more powerful workflows
    - Dependencies: v0.2.6 completion (Enhanced Playlist Naming foundation)
    - Notes:
        - ✅ **COMPLETED**:
            - `combiner.merge_playlists` - Combines multiple playlists (concatenates)
            - `combiner.concatenate_playlists` - Joins playlists in specified order
            - `combiner.interleave_playlists` - Interleaves tracks from multiple sources
            - `selector.limit_tracks` - Selection with methods: first, last, random
            - `sorter.weighted_shuffle` - Randomization with configurable shuffle strength (0.0-1.0)
        - 🔜 **REMAINING**:
            - Sort by date first played
            - Sort by date most recently played
            - Additional combining strategies (if needed)
            - Production workflow templates showcasing new capabilities

### v0.3.0: Data Visibility Layer
**Goal**: Expose rich metadata already in database to prepare for web interface - connector linkage, sync state, and freshness tracking.

**Context**: Infrastructure exploration revealed extensive metadata exists (connector mappings, sync timestamps, freshness tracking) but lacks use case layer exposure. Web UI needs visibility into this data to show users which tracks/playlists are linked to which connectors, when data was last synced, and metadata staleness.

#### Connector Mapping Visibility Epics

- [ ] **Track Connector Mappings**
    - Effort: S
    - What: Use case `GetTrackConnectorMappingsUseCase` - retrieve which connectors have mappings for a given track
    - Why: Web UI needs to show "This track is on: Spotify, Last.fm" with confidence scores and match methods
    - Dependencies: None (data already exists in track_mappings table)
    - Status: 🔜 Not Started
    - Notes:
        - Input: track_id
        - Output: List of (connector_name, connector_id, is_primary, confidence, match_method)
        - Repository support already exists: `get_connector_mappings()`
        - Just needs use case wrapper

- [ ] **Connector Mapping Statistics**
    - Effort: S
    - What: Use case `GetConnectorMappingStatsUseCase` - aggregate mapping statistics
    - Why: Dashboard needs "5,234 tracks mapped to Spotify, 3,891 to Last.fm"
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Output: Counts per connector, unmapped track counts, confidence distribution
        - Queries track_mappings table with aggregations
        - CLI: Enhance `narada status --mappings` (currently stub)

- [ ] **Unmapped Tracks Query**
    - Effort: S
    - What: Use case `GetUnmappedTracksUseCase` - find tracks without mappings to specific connector
    - Why: Identify gaps like "tracks liked on Spotify but not mapped to Last.fm"
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Input: connector_name (optional), limit, offset
        - Output: Paginated list of tracks without mappings
        - Useful for data quality checks

#### Sync State Dashboard Epics

- [ ] **Sync Status Overview**
    - Effort: M
    - What: Use case `GetSyncStatusUseCase` - comprehensive sync state for all connectors
    - Why: Users need "Last synced with Spotify: 2 days ago, Last.fm: 5 hours ago"
    - Dependencies: None (sync_checkpoints table has all data)
    - Status: 🔜 Not Started
    - Notes:
        - Queries sync_checkpoints table
        - Output: Per-connector, per-entity (likes/plays) last sync timestamps
        - Domain: `SyncStatusDashboard` value object with formatted output
        - CLI: Enhance `narada status --sync` to show timestamps, staleness warnings

#### Metadata Freshness Visibility Epics

- [ ] **Metadata Freshness Tracking**
    - Effort: S
    - What: Use case `GetMetadataFreshnessUseCase` - when was track data last updated from connectors?
    - Why: Show "Spotify metadata: updated 3 days ago" in web UI
    - Dependencies: None (connector_tracks.last_updated exists)
    - Status: 🔜 Not Started
    - Notes:
        - Input: track_ids, connector_name
        - Output: Per-track, per-connector last update timestamps
        - Uses existing `get_metadata_timestamps()` repository method

- [ ] **Stale Tracks Detection**
    - Effort: S
    - What: Use case `GetStaleTracksUseCase` - identify tracks with outdated connector metadata
    - Why: Automated data quality monitoring
    - Dependencies: MetadataFreshnessUseCase
    - Status: 🔜 Not Started
    - Notes:
        - Configurable staleness threshold (e.g., 30 days)
        - CLI: `narada status --freshness` with counts and examples
        - Could trigger refresh workflows

#### Playlist Discovery Epics

- [ ] **Playlist Listing Enhancement**
    - Effort: S
    - What: Use case `ListPlaylistsUseCase` already exists - enhance CLI to show connector linkage
    - Why: Users need to see "Playlist XYZ → linked to Spotify (playlist_id: abc123)"
    - Dependencies: None (ListPlaylistsUseCase exists, just enhance output)
    - Status: 🔜 Not Started
    - Notes:
        - Current: Returns playlists with basic metadata
        - Enhancement: CLI formatting to show connector_playlist_identifiers
        - Support filtering by connector, sorting by last_updated
        - Foundation for web UI playlist browser


### v0.3.1: User Experience and Reliability
**Goal**: Polish the user experience and improve system reliability

#### Enhanced CLI Experience Epic

- [ ] **Shell Completion Support**
    - Effort: S
    - What: Add shell completion for bash/zsh/fish
    - Why: Improves CLI usability and discoverability
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - Use Typer's built-in completion support
        - Generate completion scripts for major shells
        - Include dynamic completion for workflows and connectors

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
        - **Dependency Security** (`.github/workflows/security.yml`):
            - poetry audit (known vulnerabilities)
            - safety check (dependency scanning)
            - trivy (container scanning, for v0.5.0+)
        - **Pre-merge Requirements**:
            - All tests passing
            - Coverage threshold met
            - Zero linting/type errors
            - No security vulnerabilities
        - **Performance**: Cache Poetry dependencies, run fast tests first

- [ ] **Automated Changelog Generation**
    - Effort: XS
    - What: Conventional Commits + automated CHANGELOG.md generation
    - Why: Clear release notes, semantic versioning, user transparency
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - **Conventional Commits**: Enforce commit message format
            - feat: New feature (MINOR version bump)
            - fix: Bug fix (PATCH version bump)
            - BREAKING CHANGE: Breaking change (MAJOR version bump)
            - chore, docs, refactor: No version bump
        - **Tools**:
            - commitlint: Validate commit messages
            - standard-version or semantic-release: Auto-generate CHANGELOG.md
            - GitHub Actions: Automated release notes on tag push
        - **CHANGELOG.md Format**:
            - Keep current ROADMAP.md version history
            - Add machine-readable CHANGELOG.md (Keep a Changelog format)
            - Auto-generate from commits on release

#### Data Integrity & Monitoring Epics

- [ ] **Progress Reporting Consistency**
    - Effort: S
    - What: Standardize progress reporting across all long-running operations
    - Why: Users need consistent feedback on operation status
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - Use unified progress provider interface
        - Add ETA calculations where possible
        - Include operation-specific progress details

- [ ] **Matcher Status Feedback**
    - Effort: S
    - What: Implement better progress reporting for matcher operations
    - Why: Matching is a long-running process with no visibility
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - Add progress indicators for batch operations
        - Show success/failure counts in real-time
        - Implement optional verbose mode for detailed progress
        - Report service-specific rate limiting information
        - Include estimated completion time


- [ ] **Data Integrity Monitoring System**
    - Effort: M
    - What: Implement automated health checks and monitoring for data consistency
    - Why: Need early detection of data integrity issues, especially primary mapping violations
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - **Primary Mapping Checks**: Monitor for multiple primary mappings per (track_id, connector_name)
        - **Missing Primary Checks**: Ensure tracks with connector mappings always have exactly one primary
        - **Orphaned Mapping Detection**: Find mappings referencing non-existent connector tracks
        - **Duplicate Track Detection**: Identify potential duplicate canonical tracks
        - **Health Check Commands**: Add `narada status --health` and `narada status --integrity` CLI commands
        - **Automated Reporting**: Generate summary reports with counts and examples
        - **Configuration**: Use `settings.py` for monitoring thresholds and schedules

#### Enhanced Mapping Capabilities Epics
- [ ] **Manual Track Mapping & Data Quality Management**
    - Effort: L
    - What: Comprehensive user control over track mapping and library organization
    - Why: Music services disagree on track identity, regional differences, and catalog changes require user authority over their music library organization
    - Dependencies: Primary Connector Mapping Foundation (v0.2.5)
    - Status: Not Started
    - Notes:
        - **User Problems Solved**: Service catalog disagreements (remastered vs original), version preferences (explicit vs clean), regional catalog differences, low-confidence automated matches
        - **Key Capabilities**: Manual mapping override, duplicate track detection and merging, confidence-based review workflows, bulk data cleanup tools
        - **User Experience**: Interactive wizards for common scenarios, quality metrics dashboard, step-through interfaces for bulk operations

---

### v0.4.0: Track Management Completion
**Goal**: Fill CRUD gaps for tracks to enable comprehensive track browsing in web interface.

**Context**: Current track operations limited to filtered views (GetLikedTracksUseCase, GetPlayedTracksUseCase). Web UI needs generic track listing, pagination, search, and single track retrieval for track browser functionality.

#### Generic Track Listing Epic

- [ ] **List All Tracks Use Case**
    - Effort: M
    - What: `ListTracksUseCase` - generic track listing with true pagination (offset/limit)
    - Why: Web UI needs "show all tracks" without liked/played filtering
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Support offset/limit pagination (not fixed 10,000 limit like current use cases)
        - Support multi-criteria filtering (liked + played + time period + connector)
        - Support sorting by: title, artist, album, release_date, duration_ms, added_at
        - Repository already supports batch operations, just needs pagination wrapper
        - Return: Paginated `TrackList` with total count, offset, limit metadata

#### Single Track Operations Epic

- [ ] **Get Track Details Use Case**
    - Effort: S
    - What: `GetTrackDetailsUseCase` - retrieve single track with full metadata
    - Why: Web UI track detail view needs comprehensive track information
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Input: track_id (internal canonical ID)
        - Output: Track with enriched metadata including:
            - Connector mappings (from GetTrackConnectorMappingsUseCase)
            - Like status per connector
            - Play history summary (total plays, last played, first played)
            - Connector metadata (Spotify popularity, Last.fm play counts, etc.)
        - Wrapper around existing `TrackRepository.get_track()`
        - Composes data from multiple repositories

#### Track Search Epic

- [ ] **Search Tracks Use Case**
    - Effort: M
    - What: `SearchTracksUseCase` - full-text search by title/artist/album
    - Why: Essential for web UI track browser - users need to find specific tracks
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Search across: track title, primary artist, album name
        - Support pagination (offset/limit)
        - Support sorting (relevance, title, artist, release_date)
        - Repository layer: Add `search_tracks()` method to TrackRepository
        - Database: Consider SQLite FTS5 for full-text search performance
        - Minimum viable: Simple LIKE queries, optimize later if needed

#### Track Statistics Epic

- [ ] **Track Statistics Use Case**
    - Effort: S
    - What: `GetTrackStatsUseCase` - aggregate statistics without loading all entities
    - Why: Dashboard needs counts: "15,234 tracks total, 8,456 liked, 12,891 played"
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - Output: Total tracks, tracks per connector, liked count, played count
        - Optimized aggregation queries (COUNT, not fetching rows)
        - Can include: duplicate detection counts, unmapped track counts
        - Foundation for data quality dashboard

---

### v0.5.0: Web UI MVP
**Goal**: FastAPI service + React application for CRUD operations and workflow visualization (read-only)

**Context**: v0.3.0 (Data Visibility) and v0.4.0 (Track Management) provide comprehensive use cases for tracks, playlists, connector mappings, and sync state. v0.5.0 wraps these with REST API and builds minimal web interface. Focus: read-only workflow visualization + execution, defer interactive editing to v0.6.0.

**Architecture**: Clean Architecture compliance - web layer is pure interface, zero business logic. All operations delegate to existing use cases.

#### FastAPI Service Foundation Epics

- [ ] **FastAPI Application Setup**
    - Effort: M
    - What: Create FastAPI service with REST API endpoints using modern Pydantic v2 patterns
    - Why: Web interface needs programmatic access to all use cases
    - Dependencies: v0.4.0 completion (track use cases)
    - Status: 🔜 Not Started
    - Notes:
        - **Tech Stack**:
            - FastAPI with Pydantic v2 (use `from_attributes=True` for SQLAlchemy models, not deprecated `orm_mode`)
            - Settings management via `pydantic_settings.BaseSettings` with `@lru_cache()`
            - Python 3.14+ with strict type checking
        - **Project Structure** (domain-based, Netflix Dispatch pattern):
            - `src/api/tracks/` - router.py, schemas.py, dependencies.py
            - `src/api/playlists/` - router.py, schemas.py, dependencies.py
            - `src/api/workflows/` - router.py, schemas.py, dependencies.py
            - `src/api/status/` - router.py, schemas.py, dependencies.py
            - Follows "thin routes, fat services" - routers delegate to use cases
        - **Core Infrastructure**:
            - Pydantic v2 schemas for all request/response models
            - Dependency injection for use cases (follows existing UnitOfWork pattern)
            - Error handling middleware with consistent HTTP responses
            - CORS configuration for local development
            - Automatic OpenAPI/Swagger documentation
        - **API Endpoints**:
            - `/api/playlists` - list, get, create, update, delete (uses existing playlist use cases)
            - `/api/tracks` - list, get, search, stats (uses v0.4.0 use cases)
            - `/api/status` - sync state, connector mappings, freshness (uses v0.3.0 use cases)
            - `/api/workflows` - list, get, execute (read-only, uses existing workflow engine)
        - **Architecture Alignment**:
            - Layered architecture: Router → Use Case → Repository
            - No business logic in routers (delegates to application layer)
            - Clean Architecture compliance (web layer is pure interface)
        - **Authentication**: None for v0.5.0 (single-user), add in v1.0

#### React Application Epics

- [ ] **React App Foundation**
    - Effort: M
    - What: Modern Vite + React + TypeScript application with 2025 best practices
    - Why: Fast, type-safe development environment optimized for modern tooling
    - Dependencies: FastAPI Service
    - Status: 🔜 Not Started
    - Notes:
        - **Tech Stack (2025 Best Practices)**:
            - **Vite 6+** for build tooling (esbuild transpilation, fast HMR, optimized builds)
            - **React 18+** with **TypeScript 5.7+** (strict mode enabled)
            - **pnpm** for package management (faster, more efficient than npm/yarn)
            - **Tailwind CSS v4** for styling (Rust engine, 10x performance, 90%+ faster builds)
            - **Vitest** for testing (native ESM + TypeScript support, Jest-compatible API)
            - **React Router** for client-side routing
            - **Tanstack Query** for API state management and caching
            - **ESLint** (flat config) + **Prettier** for code quality
        - **Modern Features**:
            - Native ESM modules throughout
            - TypeScript strict mode for maximum type safety
            - Tailwind v4 CSS-first `@theme` for design tokens
            - Built-in `.env` support (no dotenv package needed)
        - **API Client**:
            - Typed fetch wrapper generated from FastAPI OpenAPI schema
            - Error handling with toast notifications
            - Request/response interceptors
            - Automatic retry logic with exponential backoff
        - **Layout**:
            - Navigation sidebar (Playlists, Tracks, Workflows, Status)
            - Header with app title
            - Responsive design (mobile-friendly, Tailwind breakpoints)
            - Dark mode support via Tailwind v4 CSS variables

- [ ] **Design System Foundation**
    - Effort: S
    - What: Tailwind v4 design system with @theme tokens and reusable component patterns
    - Why: Consistent styling, maintainable components, dark mode support, accessibility
    - Dependencies: React App Foundation
    - Status: 🔜 Not Started
    - Notes:
        - **Design Tokens (Tailwind v4 @theme)**:
            - Color palette (primary, secondary, accent, neutral, semantic colors)
            - Typography scale (font families, sizes, weights, line heights)
            - Spacing system (consistent margins, padding, gaps)
            - Breakpoints for responsive design
            - Shadow and border radius tokens
        - **Component Library** (composable, reusable):
            - Button variants (primary, secondary, outline, ghost, danger)
            - Card component for content containers
            - Table component with sorting/pagination support
            - Input components (text, select, checkbox, radio)
            - Modal/Dialog for overlays
            - Toast/Alert for notifications
            - Loading states and skeletons
        - **Best Practices**:
            - Component composition over @apply (avoid @apply overuse)
            - CSS-first approach using @theme variables
            - Accessible by default (ARIA labels, keyboard navigation, focus states)
            - Dark mode via CSS variables (no theme toggle logic needed)
        - **Accessibility Standards** (2025 Best Practice):
            - **WCAG 2.2 Level AA compliance** (target, not optional)
            - **Keyboard Navigation**: Full keyboard support (no mouse required)
                - Tab order: Logical focus flow
                - Focus indicators: 2px outline, 3:1 contrast ratio
                - Shortcuts: Standardized (Esc closes modals, Enter submits forms)
            - **Screen Reader Support**:
                - Semantic HTML (header, nav, main, article, aside)
                - ARIA labels for interactive elements
                - Live regions for dynamic updates (aria-live)
                - Skip links for main content navigation
            - **Color & Contrast**:
                - Text contrast: 4.5:1 for normal text, 3:1 for large text
                - UI component contrast: 3:1 (buttons, inputs, icons)
                - No color-only information (use icons + color)
            - **Responsive & Zoom**:
                - Support 200% zoom without horizontal scroll
                - Mobile-friendly touch targets (44x44px minimum)
                - Responsive breakpoints: 320px (mobile), 768px (tablet), 1024px (desktop)
            - **Testing Tools**:
                - Automated: @axe-core/react, Lighthouse accessibility audit
                - Manual: Keyboard navigation testing, screen reader testing (NVDA, JAWS)
                - Continuous: Pre-commit hooks for a11y linting
        - **Documentation**:
            - Component usage examples
            - Design token reference
            - Accessibility guidelines

- [ ] **Core Views Implementation**
    - Effort: L
    - What: Build four core views: Playlists, Tracks, Workflows, Status
    - Why: Essential functionality for web UI MVP
    - Dependencies: React App Foundation
    - Status: 🔜 Not Started
    - Notes:
        - **Playlist Browser**:
            - List view with filtering by connector, sorting
            - Detail view showing tracks, connector linkage
            - Create/update/delete operations
        - **Track Browser**:
            - List view with search, pagination, filtering
            - Detail view showing metadata, connector mappings, play history
            - Displays connector linkage (which services have this track)
        - **Status Dashboard**:
            - Sync state overview (last synced timestamps per connector)
            - Connector mapping statistics (track counts per connector)
            - Metadata freshness indicators
        - **Workflow Browser**:
            - List of available workflows
            - Read-only visualization using React Flow
            - Execute workflow button (triggers backend execution)
            - No editing (deferred to v0.6.0)

#### Testing Epic

- [ ] **API & Frontend Test Suite**
    - Effort: M
    - What: Practical testing strategy (unit, integration, E2E) - no enterprise patterns
    - Why: Ensure quality for hobbyist project, prevent regressions
    - Dependencies: Core Views Implementation
    - Status: 🔜 Not Started
    - Notes:
        - **Backend Testing** (pytest):
            - **Unit Tests**: Use case logic, domain transformations (existing ✅)
            - **Integration Tests**: Repository → database (existing ✅)
            - **API Tests**:
                - FastAPI TestClient for endpoint testing
                - Pydantic schema validation (request/response)
                - OpenAPI schema validation (ensure spec accuracy)
                - Authentication tests (JWT for v1.0)
        - **Frontend Testing** (Vitest):
            - **Component Tests**: Button, Card, Table, Input (unit)
            - **Integration Tests**: API client + React Query hooks
            - **Accessibility Tests**:
                - @axe-core/react for automated a11y testing
                - Keyboard navigation tests (Tab, Enter, Escape)
                - Screen reader compatibility (ARIA labels)
                - WCAG 2.2 Level AA compliance target
        - **E2E Testing** (Playwright - desktop Chromium only):
            - **Critical User Flows**:
                - User authentication (login, logout - v1.0)
                - Playlist CRUD (create, view, update, delete)
                - Track search and filtering
                - Workflow execution from UI
            - **Browser**: Chromium only (modern browsers work the same)
            - **Viewport**: Desktop only (primary use case)
            - **Execution**: CI/CD (on PR), local (on-demand)
        - **Coverage Targets** (relaxed for hobbyist scale):
            - Backend: 80% overall, 85% domain/application layers
            - Frontend: 60% overall (UI testing is expensive)
            - E2E: 100% critical user flows

#### Deployment Infrastructure Epic

- [ ] **Containerization & Local Development**
    - Effort: M
    - What: Simple Docker setup for FastAPI + React (hobbyist-friendly)
    - Why: Reproducible development environment, easy deployment to Fly.io
    - Dependencies: FastAPI Application Setup
    - Status: 🔜 Not Started
    - Notes:
        - **Single Dockerfile** (no multi-stage complexity):
            - Base: Python 3.14 slim image
            - Install Poetry dependencies
            - Build Vite production assets (during image build)
            - FastAPI serves both API + static React files
            - Non-root user for security
        - **docker-compose.yml** (local development only):
            - Single service: app (FastAPI + SQLite)
            - SQLite volume mount for persistence
            - Port mapping: 8000:8000
            - Environment: .env file for configuration
        - **Development**:
            - Run Vite dev server locally (npm run dev)
            - Run FastAPI with uvicorn --reload (poetry run)
            - No separate Docker services (keeps it simple)
        - **Production**:
            - Single container deployment to Fly.io
            - SQLite volume attached (Fly.io supports this)
            - HTTPS via Fly.io (automatic)
            - Alembic migrations on startup
        - **Database**:
            - SQLite volume persistence
            - Manual backup via `flyctl ssh console` + scp (simple enough for <10 users)

- [ ] **Deployment Documentation**
    - Effort: XS
    - What: Single DEPLOYMENT.md guide for Fly.io hosting
    - Why: Simple deployment for hobbyist project
    - Dependencies: Containerization epic
    - Status: 🔜 Not Started
    - Notes:
        - **DEPLOYMENT.md** (single consolidated guide):
            - **Docker**: Build and run locally
            - **Fly.io Deployment**: `fly launch` + `fly deploy` commands
            - **Environment Variables**: How to set secrets in Fly.io
            - **Database Backup**: Manual SQLite export/import via flyctl
            - **HTTPS**: Automatic via Fly.io (no Let's Encrypt setup needed)
            - **Auth Setup (v1.0)**: Email SMTP config, Spotify OAuth credentials
        - **Hosting Platform**: Fly.io only (free tier, simple, supports SQLite)
        - **No separate docs**: Operations, security, monitoring all in DEPLOYMENT.md

#### Performance Optimization Epic

- [ ] **Caching & Performance Strategy**
    - Effort: S
    - What: Simple, maintainable caching for hobbyist web UI (<10 users)
    - Why: Reduce database queries without external dependencies
    - Dependencies: FastAPI Application Setup
    - Status: 🔜 Not Started
    - Notes:
        - **Backend Caching** (in-process only, no Redis):
            - **Python lru_cache**: `@functools.lru_cache(maxsize=1024)` for pure functions
                - Track metadata lookups
                - Connector mapping stats
                - Expensive aggregations
            - **HTTP Caching**: ETags + Last-Modified headers
                - Static resources: max-age=31536000 (1 year)
                - API responses: ETag for conditional requests (304 Not Modified)
            - **Database Query Optimization**:
                - selectinload() for relationships (already implemented ✅)
                - Minimize N+1 queries
        - **Frontend Caching**:
            - **Tanstack Query** (already planned ✅):
                - Stale-while-revalidate pattern
                - Background refetching for data freshness
                - Optimistic updates for instant UI feedback
            - **Browser Storage**:
                - LocalStorage for user preferences only
                - No IndexedDB, no Service Workers (PWA deferred)
        - **Performance Targets** (realistic for SQLite + <10 users):
            - Time to Interactive (TTI): <5s
            - First Contentful Paint (FCP): <2s
            - API response time: p95 <1s (SQLite is slower than PostgreSQL, OK)
            - Lighthouse score: >70 (good enough for hobbyist project)

### v0.6.0: Interactive Workflow Editor
**Goal**: Full editing capabilities with intuitive graphical interface

**Context**: Deferred from v0.5.0 to ship web UI faster. v0.5.0 provides read-only workflow visualization + execution, which is sufficient for MVP. Users can edit workflow JSON files manually until v0.6.0 adds graphical editing.

#### Interactive Editing System Epics
- [ ] **Drag-and-Drop Node Creation**
    - Effort: M
    - What: Implement drag-and-drop node creation from node palette
    - Why: Need intuitive workflow creation experience
    - Dependencies: fastapi
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
    - Dependencies: fastapi
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

- [ ] **Workflow Persistence**
    - Effort: S
    - What: Add save/load functionality for workflows
    - Why: Users need to persist their work
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

### v0.7.0: LLM-Assisted Workflow Creation
**Goal**: Natural language workflow creation with LLM integration

#### AI-Powered Creation Epics
- [ ] **LLM Integration Endpoint**
    - Effort: M
    - What: Create API endpoint for LLM-assisted workflow generation
    - Why: Foundation for natural language workflow creation
    - Dependencies: fastapi implementaiton
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
    - Dependencies: FastAPI Service (v0.5.0)
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
    - Dependencies: Team Collaboration Features
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
    - Dependencies: FastAPI Service (v0.5.0)
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

#### Database Scaling Epic

- [ ] **PostgreSQL Migration Path (Optional)**
    - Effort: M
    - What: Support PostgreSQL as alternative to SQLite for multi-user deployments
    - Why: SQLite write concurrency limits (~1-10 concurrent writes), PostgreSQL scales to 100s of users
    - Dependencies: v1.0.0 Multi-User Platform
    - Status: 🔜 Not Started (evaluate based on actual load)
    - Notes:
        - **When to Migrate**:
            - Trigger: >10 concurrent users experiencing write lock contention
            - Symptom: "database is locked" errors under load
            - Recommendation: Start with SQLite, migrate only if needed
        - **Migration Strategy**:
            - Repository pattern already abstracts database (no application code changes) ✅
            - Change DATABASE_URL: `postgresql+asyncpg://user:pass@host/db`
            - Alembic migrations work with both SQLite and PostgreSQL ✅
            - Data migration: Export SQLite → import to PostgreSQL (script)
        - **PostgreSQL Benefits**:
            - Write concurrency: 100s of simultaneous transactions
            - JSONB for semi-structured data (connector metadata)
            - Full-text search (FTS) for track/playlist search
            - Advanced indexing (GIN, BRIN for time-series play history)
        - **Trade-offs**:
            - SQLite: Zero-config, local file, perfect for single-user
            - PostgreSQL: Requires server, adds complexity, scales better
        - **Recommendation**: Document migration path, defer until actual need

---

## Future Considerations 💭

### Quality of Life Improvements
- **Background Sync Capabilities** (M) - Scheduled synchronization of play history and likes
- **Two-Way Like Synchronization** (M) - Bidirectional sync between services with conflict resolution
- **Advanced Node Palette** (M) - Enhanced node selection with categories, search, and favorites
- **Discovery Workflow Templates** (S) - Pre-built templates ("Hidden Gems", "Seasonal Favorites", "Rediscovery")
- **Workflow Debugging Tools** (L) - Interactive debugging for workflow testing
- **Playlist Diffing and Merging** (L) - Visualize differences between local and remote playlists

### Lower Priority Ideas
- **Advanced Analytics Dashboard** - Workflow usage and performance metrics
- **Multi-Language Support** - UI translations for international users

### Deferred Clean Architecture Improvements
- **Domain Layer Logging Abstraction** (S) - Remove infrastructure dependency from domain layer

---
