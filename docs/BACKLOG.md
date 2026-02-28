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
    - Why: Codebase had accumulated overlapping abstractions, dead code, and CLI-coupled interface logic blocking v0.5.0 web UI
    - Dependencies: None
    - Notes:
        - **Python 3.14 Modernization**: `@override` decorators, `TypeIs` mapper guards, error classifier hierarchy simplification
        - **Dead Code Removal**: Deleted empty modules (conversions.py, setup_commands.py, status_commands.py), removed orphan protocols
        - **Module Consolidation**: Merged failure handling files, matching provider files, extracted shared ISRC utilities
        - **Interface Restructuring**: Moved CLI-specific code out of shared/, extracted interactive menu pattern, moved async executor to CLI layer
        - **Web Readiness**: Created `application/runner.py` with `execute_use_case[TResult]()` — both CLI and FastAPI share this runner

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

### v0.3.0: Database Migration
**Goal**: Migrate from SQLite to a networked relational database, enabling remote hosting and unlocking Prefect parallel task execution.

**Context**: SQLite's file-based, single-writer model ties the application to a local machine and prevents concurrent writes. The Repository + UoW pattern already fully abstracts database access — only the connection config, driver, and a handful of SQLite-specific SQL constructs need to change.

**Why before Web UI**: Hard prerequisite for any deployment outside a local machine. Also unlocks:
- **Remote hosting**: Fly.io, Railway, Render, or any cloud host (no SQLite volume trickery)
- **Prefect `.submit()` parallelism**: MVCC concurrent writes remove the `SharedSessionProvider` constraint; source/enricher nodes can execute in parallel
- **Concurrent web requests**: FastAPI + multiple simultaneous users require concurrent write capability
- **Prefect task caching**: Currently blocked by non-serializable context dict (live `AsyncSession`, connector instances, loggers) — evaluate separately after migration

#### Database Selection Epic

- [ ] **Evaluate and Select Database**
    - Effort: S
    - What: Research database options, evaluate against selection criteria, document ADR (Architecture Decision Record)
    - Why: Commit to one technology before migration implementation begins
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - **Leading candidate — PostgreSQL**:
            - `asyncpg` driver: fastest Python async PostgreSQL driver, mature SQLAlchemy 2.0 support
            - MVCC concurrency: multiple concurrent writers without locking
            - JSONB columns: indexable, operators — better than SQLite JSON
            - Managed hosting: Neon/Supabase (dev free tier), Fly.io Postgres (prod)
            - Alembic already supports PostgreSQL; existing migration chain can be regenerated as a fresh baseline
        - **Alternative — Turso (LibSQL)**:
            - Distributed SQLite fork; near-zero SQL dialect migration cost (SQL stays identical)
            - HTTP wire protocol; async Python driver (`libsql-experimental`) still maturing
            - Embedded replicas: local read cache + remote write, interesting for edge deployment
            - Risk: smaller ecosystem, fewer managed hosting options
        - **Evaluation criteria**:
            - Async Python driver maturity and SQLAlchemy 2.0 compatibility
            - Managed hosting options with free/cheap tiers
            - Write concurrency model (MVCC vs WAL-over-network)
            - Migration cost from SQLite (schema + query compat)
            - Operational simplicity at hobbyist scale (<10 users)
        - **Expected outcome**: PostgreSQL via `asyncpg`

#### Migration Implementation Epics

- [ ] **Driver and Connection Config**
    - Effort: S
    - What: Swap `aiosqlite` → `asyncpg`, update engine/session config, remove SQLite-specific PRAGMAs
    - Why: Core connectivity change
    - Dependencies: Database Selection
    - Status: 🔜 Not Started
    - Notes:
        - `db_connection.py`: URL → `postgresql+asyncpg://`, switch `NullPool` → `AsyncAdaptedQueuePool` (pool_size=5, max_overflow=10)
        - Remove `@event.listens_for` PRAGMA hook (WAL, foreign_keys, busy_timeout not applicable to PostgreSQL)
        - Remove SQLite connect args (`check_same_thread`, `timeout`)
        - `alembic.ini` + `alembic/env.py`: remove `render_as_batch=True` (PostgreSQL supports `ALTER TABLE` natively)
        - Add `DATABASE_URL` env var; remove hardcoded `data/db/narada.db` path

- [ ] **Schema and Query Compatibility**
    - Effort: M
    - What: Audit and migrate SQLite-specific SQL constructs to PostgreSQL-compatible equivalents
    - Why: Several SQLite dialect features won't work on PostgreSQL
    - Dependencies: Driver and Connection Config
    - Status: 🔜 Not Started
    - Notes:
        - **Bulk upsert**: `sqlite_insert().on_conflict_do_update()` → `postgresql_insert().on_conflict_do_update()` (same semantics, dialect swap in `base_repo.py`)
        - **JSON → JSONB**: Change column types in `db_models.py` + migration; unlocks `@>` containment queries and GIN indexes for connector metadata
        - **DateTime → TIMESTAMPTZ**: Verify no tz-naive inserts leak through; PostgreSQL is stricter about timezone-aware datetimes
        - **Partial indexes**: `WHERE is_primary = TRUE` syntax is identical in PostgreSQL ✅
        - **Alembic**: Generate fresh `initial_schema` migration for PostgreSQL as new baseline; archive SQLite chain in `alembic/archive/`
        - **Test fixtures**: Replace `sqlite+aiosqlite:///:memory:` with `pytest-postgresql` ephemeral instances or dedicated test schema

- [ ] **Prefect Parallel Execution**
    - Effort: M
    - What: Remove `SharedSessionProvider`, migrate to per-task sessions, enable `.submit()`-based parallel node execution
    - Why: `SharedSessionProvider` was a SQLite workaround; PostgreSQL's connection pool safely handles concurrent sessions
    - Dependencies: Schema and Query Compatibility
    - Status: 🔜 Not Started
    - Notes:
        - Remove `SharedSessionProvider` from workflow engine
        - Each Prefect task creates its own session from the pool (standard UoW pattern)
        - Replace manual topological sort with `.submit()` + Prefect-native future dependency resolution
        - Source nodes and enricher nodes become concurrently executable where the DAG allows; linear pipelines remain linear (no forced parallelism)
        - Prefect task caching: separate concern — requires making context dict serializable (remove live `AsyncSession`, connector instances, loggers from context); evaluate after migration

- [ ] **Development and CI Environment**
    - Effort: S
    - What: Update local dev setup and CI pipeline for PostgreSQL
    - Why: `sqlite+aiosqlite:///:memory:` test databases must be replaced
    - Dependencies: Driver and Connection Config
    - Status: 🔜 Not Started
    - Notes:
        - `docker-compose.yml`: PostgreSQL service for local development (replaces SQLite file)
        - `conftest.py`: Update `db_session` fixture to use `pytest-postgresql` ephemeral instances
        - GitHub Actions: Add `services: postgres:` block to test workflow
        - `.env.example`: Document `DATABASE_URL`, `DATABASE_TEST_URL`
        - Remove `data/db/` directory and SQLite file references from `.gitignore`, `alembic.ini`

---

### v0.3.1: Deployment Foundation
**Goal**: Containerize the application and establish a repeatable deployment pipeline to Fly.io, making Narada hostable outside a local machine.

**Context**: The database migration (v0.3.0) established PostgreSQL as the data layer. This milestone wraps the application in Docker and gets it running on a real host. Doing this before the UI build means every subsequent feature ships into a real hosted environment from day one — no deployment surprises when it's time to launch v0.6.0.

**Why before Web UI**: The Web UI assumes a reachable hosted backend. Solving containerization and deployment as a standalone concern is cleaner than baking it into the UI milestone. The Dockerfile will be extended in v0.6.0 to bundle Vite production assets, but the deployment infrastructure — Fly.io config, secrets management, migrations-on-deploy — is established here.

#### Containerization Epics

- [ ] **Dockerfile and Docker Compose**
    - Effort: S
    - What: Multi-stage Dockerfile for the Python backend + docker-compose.yml for local development with PostgreSQL
    - Why: Reproducible, portable build; local dev environment matches production topology
    - Dependencies: Database Migration (v0.3.0)
    - Status: 🔜 Not Started
    - Notes:
        - **Dockerfile** (multi-stage):
            - Stage 1 (`builder`): Python 3.14 slim + Poetry; install dependencies into `/venv`
            - Stage 2 (`runtime`): copy `/venv` + `src/`; non-root `narada` user; expose port 8000
            - No Vite assets at this stage — extended in v0.6.0 to add a `node` build stage
        - **docker-compose.yml** (local development):
            - `app` service: bind-mount `src/` for live reload (`uvicorn --reload`)
            - `postgres` service: `postgres:16-alpine`, named volume for data persistence
            - `.env` file supplies `DATABASE_URL`, API credentials
            - Depends-on + healthcheck: app waits for postgres to be ready
        - **Startup**: `alembic upgrade head` runs before `uvicorn` starts (entrypoint script)
        - **.dockerignore**: exclude `.venv/`, `data/`, `*.pyc`, `.git`, `node_modules/`

- [ ] **Environment Configuration Hardening**
    - Effort: S
    - What: Audit all configuration for env-var-driven config; document in `.env.example`
    - Why: App has scattered hardcoded paths that break in containers
    - Dependencies: Database Migration (v0.3.0)
    - Status: 🔜 Not Started
    - Notes:
        - `DATABASE_URL` already added in v0.3.0 ✅
        - API credentials: `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `LASTFM_API_KEY`, `LASTFM_API_SECRET`
        - Log config: `LOG_LEVEL` (default `INFO`); stdout in container (Fly.io aggregates)
        - `.env.example`: document all required + optional variables with descriptions
        - Startup validation: `pydantic-settings` fails fast with clear error if required vars are missing

#### Credential Architecture Epics

- [ ] **Spotify Token Persistence**
    - Effort: S
    - What: Move Spotify access + refresh tokens from `.spotify_cache` local file to a new `oauth_tokens` database table
    - Why: Local file token storage is a hard blocker for containerization — containers have no persistent local filesystem. Tokens must survive restarts.
    - Dependencies: Database Migration (v0.3.0)
    - Status: 🔜 Not Started
    - Notes:
        - **Root cause**: `SpotifyTokenManager` hardcodes `cache_path: Path = Path(".spotify_cache")` (`auth.py:77`); `_load_cache()`/`_save_cache()` read/write this file directly
        - **New `oauth_tokens` table**: `service VARCHAR(32)`, `access_token TEXT`, `refresh_token TEXT`, `expires_at DATETIME`, `scope TEXT`, `updated_at DATETIME` — keyed by service name
        - **`TokenStorage` protocol**: define in domain or infrastructure; `FileTokenStorage` (existing behavior, for local CLI) and `DatabaseTokenStorage` (new, for hosted); inject into `SpotifyTokenManager`
        - **Local CLI**: `SPOTIFY_REDIRECT_URI = http://localhost:8888/callback` — browser-based initial auth still works locally via `FileTokenStorage`
        - **Hosted environment**: `DatabaseTokenStorage` is used; initial auth requires the web OAuth flow (v0.6.0); until then, Spotify features gracefully degrade
        - **Last.fm**: password-based mobile session, session key in-memory only — acceptable for hosted use; `LASTFM_PASSWORD` must be a managed secret (`fly secrets set`)

- [ ] **Local Data Migration Tooling**
    - Effort: S
    - What: One-time script to export existing local SQLite data and import it to the hosted PostgreSQL database
    - Why: The schema migrates in v0.3.0, but existing listening history, liked tracks, and play data live in the local SQLite file and would otherwise be lost
    - Dependencies: Database Migration (v0.3.0), Fly.io Deployment
    - Status: 🔜 Not Started
    - Notes:
        - **Approach**: SQLAlchemy-based read from SQLite (source engine) → write to PostgreSQL (target engine); same `db_models.py` models, different connection strings
        - **Tables to migrate**: `tracks`, `connector_tracks`, `track_mappings`, `track_metrics`, `track_likes`, `track_plays`, `connector_plays`, `playlists`, `connector_playlists`, `playlist_mappings`, `playlist_tracks`, `sync_checkpoints`
        - **Order matters**: insert parent tables before child tables (tracks before track_mappings, etc.) to satisfy FK constraints
        - **Idempotent**: use `INSERT ... ON CONFLICT DO NOTHING` so the script is safe to re-run
        - **CLI command**: `narada db migrate-to-remote --source sqlite:///data/db/narada.db --target $DATABASE_URL`

#### Deployment Epics

- [ ] **Fly.io Deployment**
    - Effort: M
    - What: Deploy the containerized backend to Fly.io with managed PostgreSQL
    - Why: Primary hosting target; hobby tier sufficient for personal use
    - Dependencies: Dockerfile and Docker Compose
    - Status: 🔜 Not Started
    - Notes:
        - `fly.toml`: app config, port 8000, health check on `/health`
        - **Health check**: Add `/health` endpoint to FastAPI (200 + DB connectivity probe)
        - **Database**: Fly.io Postgres (managed); app connects via internal private network `DATABASE_URL`
        - **Secrets**: `fly secrets set SPOTIFY_CLIENT_ID=... LASTFM_API_KEY=...`
        - **Migrations**: `alembic upgrade head` as release command — runs before new version receives traffic
        - **HTTPS**: Automatic via Fly.io (no Let's Encrypt setup needed)
        - **Volumes**: None — PostgreSQL is managed; logs go to stdout
        - **Scaling**: Single machine, 256MB RAM; scale up only if needed

- [ ] **Deployment Documentation**
    - Effort: XS
    - What: Single `DEPLOYMENT.md` covering local Docker dev, first Fly.io deploy, updates, and database backups
    - Why: Reproducible deploys; useful reference when the app hasn't been touched in months
    - Dependencies: Fly.io Deployment
    - Status: 🔜 Not Started
    - Notes:
        - **Sections**: Prerequisites → Local dev with Docker Compose → First deploy → Updating → PostgreSQL backups → Troubleshooting
        - **Database backup**: `fly postgres connect` + `pg_dump` piped to a local file
        - **Rolling updates**: `fly deploy` (zero-downtime redeploy for single-user)
        - Keep it concise — if a step needs more than one command, something is wrong with the setup

---

### v0.4.0: Data Visibility Layer
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


### v0.4.1: User Experience and Reliability
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

#### Type Safety Hardening Epic

- [ ] **Audit and Resolve `# type: ignore` and `Any` Suppressions**
    - Effort: M
    - What: Systematic review of all pyright suppressions and `Any` annotations introduced during the v0.2.7 type cleanup, distinguishing legitimate architectural boundaries from papering over real type gaps
    - Why: The basedpyright 0-warning baseline was achieved partly by relaxing strictness settings (`reportUnknownVariableType`, `reportUnknownArgumentType` etc.) and adding targeted `# pyright: ignore` comments. Some of these are correct (e.g. the `isinstance` guard on the workflow context dict, where the annotation is intentionally narrower than the runtime type). Others may indicate real architectural issues — missing TypedDicts, weak Pydantic models, or places where `Any` leaks across layer boundaries.
    - Dependencies: None
    - Status: 🔜 Not Started
    - Notes:
        - **Audit approach**: `grep -rn "type: ignore\|pyright: ignore\|Any" src/` to enumerate all suppressions; categorise each as (a) legitimate boundary, (b) gap to fix, or (c) remove entirely
        - **Known legitimate suppressions** (do not remove):
            - `prefect.py` — `isinstance` guard on workflow context dict (annotation is `dict[str, NodeResult]` but caller passes full context); guard is correct, annotation documents intent
            - `domain/entities/operations.py` — `TYPE_CHECKING` import for Spotify `PersonalData` (unavoidable circular import)
        - **Likely fixable gaps** to investigate:
            - `dict[str, Any]` in connector response parsing not yet covered by Pydantic models (e.g. Spotify search results, playlist objects)
            - `Any` in repository mapper layers where SQLAlchemy column types are widened
            - `reportUnknownMemberType` suppressed on SQLAlchemy mapped columns
        - **Strictness settings** (`pyrightconfig.json`): review which rules were relaxed and re-tighten where possible
        - **Goal**: Maintain 0 errors/warnings baseline while raising the floor — fewer blanket suppressions, more targeted annotations or Pydantic models
        - **Blocker for web UI**: FastAPI route handlers and Pydantic response schemas depend on correct types flowing from the application layer; unresolved `Any` leaks will surface as runtime serialisation errors

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
            - trivy (container scanning, for v0.6.0+)
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

### v0.5.0: Track Management Completion
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

### v0.6.0: Web UI MVP
**Goal**: FastAPI service + React application for CRUD operations and workflow visualization (read-only)

**Context**: v0.4.0 (Data Visibility) and v0.5.0 (Track Management) provide comprehensive use cases for tracks, playlists, connector mappings, and sync state. v0.6.0 wraps these with REST API and builds minimal web interface. Focus: read-only workflow visualization + execution, defer interactive editing to v0.7.0.

**Architecture**: Clean Architecture compliance - web layer is pure interface, zero business logic. All operations delegate to existing use cases.

**Foundation already in place** (from v0.2.7 DRY Consolidation):
- `application/runner.py` — `execute_use_case[TResult]()` handles session/UoW lifecycle; FastAPI routes call this via `Depends`
- CLI-specific code (async executor, Rich/Typer helpers, interactive menus) isolated in `interface/cli/`, no leakage into application layer
- `interface/shared/` eliminated — nothing in `interface/` assumes CLI anymore

#### FastAPI Service Foundation Epics

- [ ] **FastAPI Application Setup**
    - Effort: M
    - What: Create FastAPI service with REST API endpoints using modern Pydantic v2 patterns
    - Why: Web interface needs programmatic access to all use cases
    - Dependencies: v0.5.0 completion (track use cases)
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
            - Dependency injection via `execute_use_case()` runner (✅ already built in v0.2.7)
            - Error handling middleware with consistent HTTP responses
            - CORS configuration for local development
            - Automatic OpenAPI/Swagger documentation
        - **API Endpoints**:
            - `/api/playlists` - list, get, create, update, delete (uses existing playlist use cases)
            - `/api/tracks` - list, get, search, stats (uses v0.5.0 use cases)
            - `/api/status` - sync state, connector mappings, freshness (uses v0.4.0 use cases)
            - `/api/workflows` - list, get, execute (uses existing workflow engine)
        - **Workflow Execution Model** (non-blocking):
            - `POST /api/workflows/{id}/run` → launches workflow as `BackgroundTask`, returns `{"run_id": "..."}` immediately
            - `GET /api/workflows/{run_id}/progress` → Server-Sent Events stream; new `SSEProgressProvider` subscribes to existing `AsyncProgressManager` (the `ProgressSubscriber` protocol is already display-agnostic; CLI uses `RichProgressProvider`)
            - Prefect flows run in-process (`await run_workflow(...)`) — no Prefect server needed; validate that `@flow` functions work correctly when called from within a FastAPI event loop (Prefect 3 supports this, but `get_run_logger()` context must be verified)
        - **Security baseline**:
            - Basic rate limiting via `slowapi` middleware — prevents runaway scripts even in single-user mode
            - CORS: `allow_origins = [settings.app_url]` for production (not `*`); localhost allowed in dev mode
        - **Architecture Alignment**:
            - Layered architecture: Router → Use Case → Repository
            - No business logic in routers (delegates to application layer)
            - Clean Architecture compliance (web layer is pure interface)
        - **Authentication**: None for v0.6.0 (single-user), add in v1.0

- [ ] **Spotify OAuth Web Flow**
    - Effort: M
    - What: Replace the current browser-on-localhost OAuth flow with a proper hosted OAuth callback that works in a container
    - Why: `SpotifyTokenManager._run_browser_auth()` uses `webbrowser.open()` and a blocking `HTTPServer` on `localhost:8888` — completely non-functional in a headless server. This is a hard blocker for any Spotify read/write functionality in the hosted app.
    - Dependencies: FastAPI Application Setup, Spotify Token Persistence (v0.3.1)
    - Status: 🔜 Not Started
    - Notes:
        - **New routes** (`src/api/auth/`):
            - `GET /auth/spotify` — generates Spotify authorization URL and redirects user's browser to it
            - `GET /auth/spotify/callback` — receives the OAuth code from Spotify, exchanges it for tokens, stores via `DatabaseTokenStorage` (from v0.3.1); redirects user to the app UI
        - **Config**: `SPOTIFY_REDIRECT_URI = https://{FLY_APP_HOSTNAME}/auth/spotify/callback`; register this URI in Spotify Developer Dashboard (document in `DEPLOYMENT.md`)
        - **`SpotifyTokenManager`**: already uses injected `TokenStorage` (from v0.3.1); no further changes needed — the web flow just calls `_save_cache()` via the DB storage backend
        - **Graceful degradation**: endpoints requiring Spotify return `503 Service Unavailable` with a link to `/auth/spotify` if no valid token exists; read-only data (already imported) remains accessible
        - **Local CLI**: unchanged — still uses `FileTokenStorage` + localhost server; the hosted OAuth callback is a web-only code path

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

#### Deployment Update

Extend the Dockerfile established in v0.3.1 to add a `node` build stage: install pnpm deps, run `vite build`, copy `dist/` into the runtime image. FastAPI serves the built static files via `StaticFiles`. `docker-compose.yml` and Fly.io config require no changes — the same deployment pipeline from v0.3.1 applies.

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
        - **Performance Targets** (realistic for <10 users):
            - Time to Interactive (TTI): <5s
            - First Contentful Paint (FCP): <2s
            - API response time: p95 <500ms (PostgreSQL on Fly.io internal network)
            - Lighthouse score: >70 (good enough for hobbyist project)

### v0.7.0: Interactive Workflow Editor
**Goal**: Full editing capabilities with intuitive graphical interface

**Context**: Deferred from v0.6.0 to ship web UI faster. v0.6.0 provides read-only workflow visualization + execution, which is sufficient for MVP. Users can edit workflow JSON files manually until v0.7.0 adds graphical editing.

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

### v0.8.0: LLM-Assisted Workflow Creation
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
    - Dependencies: FastAPI Service (v0.6.0)
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
    - Dependencies: FastAPI Service (v0.6.0)
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

PostgreSQL migration completed in v0.3.0 as a prerequisite for remote hosting and web deployment. No additional database scaling work expected for <10 users at v1.0.0 scale. If write contention surfaces, evaluate read replicas or connection pooling (PgBouncer) before considering sharding.

---

