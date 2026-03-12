# Narada Development Guide

## What Narada Does

**Personal music metadata hub** - Own your data, create playlists using YOUR criteria, not proprietary algorithms.

### User Problem
- Streaming algorithms are opaque and non-customizable
- Listening history/playlists locked per service
- No cross-service operations (can't sort Spotify by Last.fm counts)
- Users want control: "show me liked tracks unplayed 6mo" is impossible

### Solution: Workflows
Declarative pipelines composing user-defined criteria:
- "Current Obsessions" = liked → 8+ plays last 30d → top 20
- "Hidden Gems" = liked → 3+ plays but untouched 6mo → playlist
- "Discovery Mix" = interleave recent plays + old favorites → random 40

Powered by: import history, backup likes/playlists, cross-service identity mapping, enrichment from multiple sources.

## Core Principles (YOU MUST FOLLOW)

- **Python 3.14+ Required** - Modern features, type safety, 2026 best practices
- **Ruthlessly DRY** - No code duplication in single-maintainer codebase
- **Clean Breaks** - No backward compatibility or legacy adapters
- **Batch-First** - Design for collections, single items are degenerate cases
- **Immutable Domain** - Pure transformations, no side effects
- **User Goal-Focused** - Design features around "what is the user trying to accomplish?" not "what can our APIs do?"

## Architecture

**Dependency Flow**: Interface → Application → Domain ← Infrastructure

**Layers**:
- **Domain** (`src/domain/`) - Pure business logic: track matching, playlist diff, metadata transforms. Zero external deps.
- **Application** (`src/application/`) - Use case orchestration: workflow primitives (enrich, filter, sort), sync likes, import history. `async with uow:` for transactions.
- **Infrastructure** (`src/infrastructure/`) - API adapters (Spotify/Last.fm/MusicBrainz), SQLAlchemy repos, metadata providers.
- **Interface** (`src/interface/`) - CLI via Typer + Rich, Web via FastAPI + React.

**Stack**: Python 3.14+, SQLite + SQLAlchemy 2.0 async, Prefect 3.0, attrs, Typer + Rich, FastAPI, React 19, Vite 7, Tanstack Query, Tailwind v4

Layer-specific enforcement rules live in `.claude/rules/` and load automatically per path.

→ See docs/architecture/layers-and-patterns.md for full layer responsibilities

## Essential Commands

```bash
# Development
uv run pytest                    # Fast tests (<1min)
uv run pytest -m ""              # All tests including slow (~3.5min)
uv run ruff check . --fix        # Lint + autofix
uv run ruff format .             # Format
uv run basedpyright src/         # Type check

# Database
uv run alembic upgrade head      # Migrate
uv run alembic revision --autogenerate -m "description"  # Generate migration

# Web UI (frontend)
pnpm --prefix web dev                # Dev server (Vite, port 5173)
pnpm --prefix web test               # Vitest component tests
pnpm --prefix web check              # Biome lint + format check
pnpm --prefix web build              # Production build
pnpm --prefix web sync-api           # Export OpenAPI schema + Orval codegen

# User-facing CLI
narada workflow                      # Interactive workflow browser
narada workflow run                  # Execute a workflow
narada history import-lastfm         # Import listening history
narada likes import-spotify          # Backup liked tracks
```

→ See docs/development.md for setup, versioning, and recipes

## Coding Patterns

Layer-specific coding patterns (attrs, SQLAlchemy, repository, use cases) live in `.claude/rules/` and load automatically when editing the relevant layer. Python 3.14+ conventions are in `.claude/rules/python-conventions.md`.

## Testing

**Tests are mandatory for every implementation.** A feature is not done until tests exist and pass.

Self-check after implementing:
1. Write tests (happy path + at least one error/edge case)
2. Right test level — domain=unit, use case=unit+mocks, repository=integration
3. Use existing factories from `tests.fixtures` (`make_track`, `make_mock_uow`)
4. Run: `uv run pytest tests/path/to/test_file.py -x`

### When to Run What

**During implementation** — targeted tests ONLY:
- Run the specific test file for the code you changed: `uv run pytest tests/path/to/test_file.py -x`
- Use `-k "test_name"` to iterate on a single failing test
- Use `--lf` to rerun only previously-failed tests
- Frontend: `pnpm --prefix web test src/path/to/Component.test.tsx`

**Before committing** — full fast suite:
- `uv run pytest` (automatically excludes slow/diagnostic)
- `pnpm --prefix web test` (all frontend tests)

**On version bump or explicit request only**:
- `uv run pytest -m ""` (all tests including slow)
- `uv run basedpyright src/` + `uv run ruff check .`
- `pnpm --prefix web check && pnpm --prefix web build`

**NEVER** run the full suite after every small edit — it breaks the feedback loop.

## Documentation Map

- **Architecture** → docs/architecture/README.md
- **Development** → docs/development.md (setup, versioning, recipes)
- **Database** → docs/architecture/database.md
- **Workflows** → docs/guides/workflows.md
- **Likes Sync** → docs/guides/likes-sync.md
- **CLI Reference** → docs/guides/cli.md
- **REST API Reference** → docs/web-ui/03-api-contracts.md
- **Planning & Backlog** → docs/backlog/ (includes completed/ archive)
- **Unscheduled Ideas** → docs/backlog/unscheduled.md
- **Web UI Specs** → docs/web-ui/README.md
