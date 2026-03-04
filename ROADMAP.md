
# Project Narada Roadmap

**Current Development Version**: 0.3.1
**Current Initiative**: Imports & Real-Time Progress

Strategic overview of Project Narada's development direction. Explains the why at a product level for future features, plus key architectural decisions.

→ See [docs/BACKLOG.md](docs/BACKLOG.md) for detailed epics and task breakdowns.
→ See [docs/IDEAS.md](docs/IDEAS.md) for future ideas and research notes.

---

## Version Changelog

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

### v0.2.7: Advanced Workflow Features + DRY Consolidation
Workflow transformer nodes, Python 3.14 modernization, and web interface readiness.
- Data Source Nodes | Transformer Nodes | Module Consolidation | Interface Layer Restructuring | Use Case Runner

---

## Infrastructure Readiness Matrix

Visual guide to infrastructure capabilities across version milestones (hobbyist scale: <10 users):

| Capability | v0.2.7 (CLI) | v0.3.0 (Web Local) | v0.5.0 (Deployed) | v1.0.0 (Multi-User) |
|------------|--------------|-------------------|-------------------|---------------------|
| **Testing** | ✅ pytest suite, <1min | ✅ + Vitest components | ✅ + E2E (Playwright) | ✅ Same |
| **CI/CD** | ⚠️ Manual | ⚠️ Manual | ✅ GitHub Actions | ✅ Same |
| **Deployment** | ✅ Poetry install | ✅ Local (SQLite) | ✅ Docker + Fly.io | ✅ Same |
| **Observability** | ✅ Loguru JSON logs | ✅ Same | ✅ Same | ✅ + Email alerts |
| **Authentication** | ❌ Not needed | ❌ Env var tokens | ✅ Spotify OAuth | ✅ + Email/password |
| **Database** | ✅ SQLite | ✅ SQLite | ✅ PostgreSQL | ✅ PostgreSQL |
| **Caching** | ❌ Not needed | ✅ Tanstack Query | ✅ + lru_cache | ✅ Same |
| **Security** | ✅ Env vars, secrets | ✅ + CORS (localhost) | ✅ + HTTPS | ✅ + bcrypt |

**Legend**: ✅ Ready | ⚠️ Needs work | ❌ Not needed

**Note**: Right-sized for hobbyist project (<10 users). No Redis, CDN, MFA, load testing, or enterprise observability. Focus on quality code over production infrastructure.

---

## Technology Decision Records

Key architecture & tech choices (see CLAUDE.md for migration details):

- **Python 3.14+ & attrs**: Modern type syntax (`str | None`, `class Foo[T]`), immutable domain entities with slots
- **PostgreSQL (v0.5.0)**: Migrated from SQLite for remote hosting and parallel Prefect execution. `asyncpg` driver, managed hosting via Neon/Supabase (dev) or Fly.io Postgres (prod). Repository pattern means zero application-layer code changes. Web UI developed on SQLite first (v0.3.x), migrated at deployment time.
- **Vite 6+ / Vitest**: 10x faster HMR than Webpack, native ESM + TypeScript
- **Tailwind CSS v4**: Rust engine (10x performance), @theme design tokens
- **Pydantic v2**: 5-50x faster validation, `from_attributes=True`
- **Clean Architecture + DDD**: Composable workflows, isolated APIs, testable logic (see docs/ARCHITECTURE.md)

---

## Planned Versions

Each milestone delivers a **vertical slice** — backend API + frontend page together — so every increment is testable end-to-end. Web UI development starts immediately on SQLite; PostgreSQL and deployment arrive when features are validated.

| Version | Goal | Status |
|---------|------|--------|
| **v0.2.7** | Advanced workflow features + DRY consolidation | ✅ Completed |
| **v0.3.0** | Web UI foundation + playlists + settings | ✅ Completed |
| **v0.3.1** | Imports + real-time progress | 🔄 In Progress |
| **v0.3.2** | Track library + search | 🔜 Not Started |
| **v0.3.3** | Dashboard + stats | 🔜 Not Started |
| **v0.4.0** | Workflows + connector links | 🔜 Not Started |
| **v0.4.1** | CI/CD + quality hardening | 🔜 Not Started |
| **v0.5.0** | PostgreSQL + deployment + OAuth | 🔜 Not Started |
| **v0.6.0** | Apple Music + data quality | 🔜 Not Started |
| **v0.7.0** | Interactive workflow editor | 🔜 Not Started |
| **v0.8.0** | LLM-assisted workflow creation | 🔜 Not Started |
| **v1.0.0** | Production-ready multi-user platform | 🔜 Not Started |

### v0.2.7: Advanced Workflow Features ✅
Workflow transformer nodes (combiners, selectors, sorters including by_first_played/by_last_played), Python 3.14 modernization, DRY consolidation, and web interface readiness via `execute_use_case()` runner. 18 production workflow templates.

### v0.3.0: Web UI Foundation + Playlists + Settings
Stand up FastAPI + React with the dark editorial design system. Deliver playlist CRUD and connector settings — the first vertical slice. All 5 playlist use cases already exist, so this proves full-stack architecture end-to-end with zero new backend logic. Settings provides the canonical entry point for connecting services (reads CLI-established credentials; web OAuth deferred to v0.5.0). Stack: Vite 6+, React 19+, TypeScript, Tailwind CSS v4, shadcn/ui, Tanstack Query. Dark editorial aesthetic — see `docs/web-ui/04-frontend-architecture.md`.

### v0.3.1: Imports & Real-Time Progress
Make the web UI operational: trigger Spotify likes import, Last.fm history import, and Spotify GDPR import. Watch progress in real-time via SSE. Implements `SSEProgressProvider` (the `ProgressSubscriber` protocol is already display-agnostic from v0.2.7). Uses existing `SyncLikesUseCase` and `ImportPlayHistoryUseCase`.

### v0.3.2: Track Library & Search
Track browsing with pagination, text search, and detail views. New use cases (`ListTracks`, `SearchTracks`, `GetTrackDetails`) built alongside the React pages that consume them — validates API shape in real-time instead of speculatively.

### v0.3.3: Dashboard & Stats
Landing page with aggregate statistics, connector health, and data quality signals. Implements data visibility use cases (`GetTrackStats`, `GetConnectorMappingStats`, `GetMetadataFreshness`).

### v0.4.0: Workflows & Connector Links
Workflow visualization (React Flow DAG), execution with SSE progress, and connector playlist linking. Completes the web UI feature set. Workflow CRUD requires a new `workflows` table for persistence (currently JSON files).

### v0.4.1: CI/CD & Quality Hardening
Pause on features to harden the stack. GitHub Actions (pytest, ruff, basedpyright), E2E tests (Playwright), type safety audit, data integrity monitoring, accessibility audit (WCAG 2.2 AA), shell completion for CLI.

### v0.5.0: PostgreSQL, Deployment & OAuth
Now that the web UI works locally, prepare for production. SQLite → PostgreSQL migration, Docker containerization, Fly.io deployment, Spotify OAuth web flow with `DatabaseTokenStorage`, and local data migration tooling. The Repository + UoW pattern means zero application-layer code changes for the database swap.

### v0.6.0: Apple Music & Data Quality
Add Apple Music as a first-class connector alongside Spotify. Pair with data quality tools (unmapped track detection, manual mapping correction, stale data alerts) since Apple Music creates new mapping scenarios. Shared infrastructure (InwardTrackResolver, BaseMatchingProvider, retry policies) means the connector is mostly wiring.

### v0.7.0: Interactive Workflow Editor
Drag-and-drop node creation, configuration panels, edge management, and visual workflow building. Upgrades the JSON editor from v0.4.0 to a graphical interface.

### v0.8.0: LLM-Assisted Workflow Creation
Natural language workflow creation via LLM integration. Conversational interface for creating and refining workflows, with visualization confirmation.

### v1.0.0: Production-Ready Multi-User Platform
Authentication (email/password + Spotify OAuth), per-user data isolation, workflow version control, and production monitoring. Enables sharing with friends at hobbyist scale (<10 users).
