# Narada Web UI Documentation

Documentation for the Narada web interface, organized by concern.

## Documents

| Document | Purpose | Stability |
|----------|---------|-----------|
| [01-user-flows.md](01-user-flows.md) | **Primary spec.** Deep user journeys with triggers, steps, backend calls, and edge cases. Start here. | Evolving -- updated as features are designed |
| [02-information-architecture.md](02-information-architecture.md) | Page hierarchy, URLs, navigation, empty states, responsive behavior. Derived from user flows. | Stable once flows settle |
| [03-api-contracts.md](03-api-contracts.md) | REST endpoints, schemas, SSE format, error conventions. Maps each endpoint to its backing use case. | v0.3.x–v0.4.1 endpoints concrete; v0.4.2+ endpoints are stubs |
| [04-frontend-architecture.md](04-frontend-architecture.md) | React stack decisions, project structure, component strategy, state management, testing. | Reflects v0.4.1 implementation |

## Backlog Alignment

| Milestone | What it unlocks for the web UI |
|-----------|-------------------------------|
| v0.3.0 -- Web UI Foundation + Playlists | FastAPI + React + design system. Playlist CRUD — first vertical slice |
| v0.3.1 -- Imports & Progress | SSE streaming, import operations with real-time progress |
| v0.3.2 -- Library & Search | Track browsing, pagination, search, detail views |
| v0.3.3 -- Dashboard & Stats | Aggregate statistics, connector health, data quality signals |
| v0.4.0 -- Workflow Persistence & Visualization | Workflow database table, CRUD API, template system, React Flow DAG (read-only) |
| v0.4.1 -- Workflow Execution & Run History | One-click execution, live per-node DAG status, run history with node inspection |
| v0.4.2 -- Visual Workflow Editor & Preview | Drag-and-drop node palette, config panel, undo/redo, dry-run preview |
| v0.4.3 -- Connector Playlist Linking | Link canonical playlists to Spotify/Apple Music, push/pull sync |
| v0.4.4 -- CI/CD & Quality | GitHub Actions, E2E tests, accessibility audit |
| v0.5.0 -- PostgreSQL, Deployment & OAuth | Production infrastructure, Docker, Fly.io, Spotify OAuth |
| v0.6.0 -- Apple Music & Data Quality | Unmapped track filters in Library, manual mapping UI on Track Detail, DQ alerts on Dashboard |
| v0.7.0 -- Advanced Workflow Features | Sub-flows, workflow versioning & diff, import/export |
| v0.8.0 -- LLM-Assisted Workflows | Natural language workflow creation via chat interface |

## How to Use These Docs

- **Designing a feature?** Start with `01-user-flows.md` to understand the user journey.
- **Building a page?** Check `02-information-architecture.md` for routes and empty states.
- **Implementing an API endpoint?** See `03-api-contracts.md` for the contract and which use case backs it.
- **Setting up the frontend?** See `04-frontend-architecture.md` for stack and project structure.
