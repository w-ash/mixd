# Narada Architecture

Narada is a personal music metadata hub that integrates Spotify, Last.fm, and MusicBrainz. By maintaining local representations of music entities with cross-service mappings, it enables features that transcend individual platform limitations — sorting Spotify playlists by Last.fm play counts, syncing likes between services, and building sophisticated playlists using cross-service data.

## Dependency Flow

```
Interface → Application → Domain ← Infrastructure ← External Services
```

Dependencies only flow inward, creating a stable core surrounded by adaptable interfaces.

## Documents

| Document | Purpose | Key Topics |
|----------|---------|------------|
| [Layers & Patterns](layers-and-patterns.md) | How the system is structured | Clean Architecture layers, UoW, Repository, Command, Strategy, Capability protocols, async patterns |
| [Data Model](data-model.md) | How entities relate and evolve | Entity resolution, Spotify stale ID handling, temporal design, hard delete |
| [Workflow System](workflow-system.md) | How workflows operate on data | Database-first design, persistence flow, source node pattern, anti-patterns |
| [Session Management](session-management.md) | How database sessions are managed | Transaction philosophy, 3 session patterns, SQLite configuration |
| [Tech Stack](tech-stack.md) | Why we chose these technologies | Core decisions, supporting libraries, extensibility, scalability |
| [Database Schema](database.md) | Physical schema reference | Tables, columns, relationships, indexes, migrations |

## Quick Navigation

- **New to the codebase?** Start with [Layers & Patterns](layers-and-patterns.md) for the big picture
- **Adding a new entity?** See [Data Model](data-model.md) for resolution and temporal patterns
- **Writing a workflow node?** Read [Workflow System](workflow-system.md) — the database-first constraint is critical
- **Touching database sessions?** See [Session Management](session-management.md) for the 3 session patterns
- **Adding a new connector?** See [Tech Stack](tech-stack.md) for the self-contained connector structure

## See Also

- **[Development](../development.md)** — Developer setup and common recipes
- **[CLI Reference](../guides/cli.md)** — CLI command reference
- **[Workflow Guide](../guides/workflows.md)** — Workflow authoring and node catalog
- **[Likes Sync Guide](../guides/likes-sync.md)** — Likes synchronization guide
- **[web-ui/](../web-ui/README.md)** — Web UI specs
- **[backlog/](../backlog/README.md)** — Project roadmap
- **[CLAUDE.md](../../CLAUDE.md)** — Development commands and coding conventions
