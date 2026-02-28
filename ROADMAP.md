
# Project Narada Roadmap

**Current Development Version**: 0.2.7
**Current Initiative**: Database Migration (v0.3.0)

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

| Capability | v0.2.7 (CLI) | v0.6.0 (Web UI) | v1.0.0 (Multi-User <10) |
|------------|--------------|-----------------|-------------------------|
| **Testing** | ✅ 867 tests, <1min | ✅ + E2E (Chromium only) | ✅ Same as v0.6.0 |
| **CI/CD** | ⚠️ Manual | ✅ GitHub Actions (pytest, ruff) | ✅ Same as v0.6.0 |
| **Deployment** | ✅ Poetry install | ✅ Docker + Fly.io | ✅ Same as v0.6.0 |
| **Observability** | ✅ Loguru JSON logs | ✅ Same as v0.2.7 | ✅ + Email alerts (optional) |
| **Authentication** | ❌ Not needed | ❌ Not needed | ✅ Email/password + Spotify OAuth |
| **Database** | ✅ SQLite | ✅ PostgreSQL (migrated v0.3.0) | ✅ PostgreSQL |
| **Caching** | ❌ Not needed | ✅ Tanstack Query + lru_cache | ✅ Same as v0.6.0 |
| **Security** | ✅ Env vars, secrets | ✅ + CORS | ✅ + HTTPS, bcrypt |

**Legend**: ✅ Ready | ⚠️ Needs work | ❌ Not needed

**Note**: Right-sized for hobbyist project (<10 users). No Redis, CDN, MFA, load testing, or enterprise observability. Focus on quality code over production infrastructure.

---

## Technology Decision Records

Key architecture & tech choices (see CLAUDE.md for migration details):

- **Python 3.14+ & attrs**: Modern type syntax (`str | None`, `class Foo[T]`), immutable domain entities with slots
- **PostgreSQL (v0.3.0)**: Migrated from SQLite as a prerequisite for remote hosting and parallel Prefect execution. `asyncpg` driver, managed hosting via Neon/Supabase (dev) or Fly.io Postgres (prod). Repository pattern means zero application-layer code changes.
- **Vite 6+ / Vitest**: 10x faster HMR than Webpack, native ESM + TypeScript
- **Tailwind CSS v4**: Rust engine (10x performance), @theme design tokens
- **Pydantic v2**: 5-50x faster validation, `from_attributes=True`
- **Clean Architecture + DDD**: Composable workflows, isolated APIs, testable logic (see docs/ARCHITECTURE.md)

---

## Planned Versions

| Version | Goal | Status |
|---------|------|--------|
| **v0.2.7** | Advanced workflow features + DRY consolidation | 🔄 In Progress |
| **v0.3.0** | SQLite → PostgreSQL migration | 🔜 Not Started |
| **v0.3.1** | Containerization + Fly.io deployment | 🔜 Not Started |
| **v0.4.0** | Data visibility layer | 🔜 Not Started |
| **v0.4.1** | UX polish + reliability | 🔜 Not Started |
| **v0.5.0** | Track management completion | 🔜 Not Started |
| **v0.6.0** | Web UI MVP | 🔜 Not Started |
| **v0.7.0** | Interactive workflow editor | 🔜 Not Started |
| **v0.8.0** | LLM-assisted workflow creation | 🔜 Not Started |
| **v1.0.0** | Production-ready multi-user platform | 🔜 Not Started |

### v0.2.7: Advanced Workflow Features
Extend workflow capabilities with sophisticated transformation and analysis features. Remaining: sort by date first/last played, production workflow templates.

### v0.3.0: Database Migration
Migrate from SQLite to PostgreSQL, enabling remote hosting and Prefect parallel task execution. The Repository + UoW pattern already fully abstracts database access — only connection config, driver, and a handful of SQLite-specific SQL constructs change.

### v0.3.1: Deployment Foundation
Containerize the application and deploy to Fly.io. Doing this before the UI build means every subsequent feature ships into a real hosted environment from day one.

### v0.4.0: Data Visibility Layer
Expose connector linkage, sync state, and metadata freshness already in the database. Web UI needs this data to show which tracks are mapped where and how fresh the data is.

### v0.4.1: User Experience and Reliability
Polish CLI experience (shell completion), harden type safety (audit `Any` suppressions), set up CI/CD (GitHub Actions), and add data integrity monitoring.

### v0.5.0: Track Management Completion
Fill CRUD gaps for tracks: generic listing with pagination, search, single track details, aggregate statistics. Prerequisites for web UI track browser.

### v0.6.0: Web UI MVP
FastAPI service + React application for CRUD operations and workflow visualization (read-only). Clean Architecture compliance — web layer is pure interface, all operations delegate to existing use cases. Stack: Vite 6+, React 18+, TypeScript, Tailwind CSS v4, Tanstack Query.

### v0.7.0: Interactive Workflow Editor
Drag-and-drop node creation, configuration panels, edge management, and workflow persistence. Deferred from v0.6.0 to ship web UI faster — v0.6.0 provides read-only visualization + execution.

### v0.8.0: LLM-Assisted Workflow Creation
Natural language workflow creation via LLM integration. Conversational interface for creating and refining workflows, with visualization confirmation.

### v1.0.0: Production-Ready Multi-User Platform
Authentication (email/password + Spotify OAuth), per-user data isolation, workflow version control, and production monitoring. Enables sharing with friends at hobbyist scale (<10 users).
