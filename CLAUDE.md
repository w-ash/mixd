# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Mixd Does

**Music metadata hub** — reclaims listening data from Spotify, Last.fm, and MusicBrainz, unifies it across services, and lets users build smart playlists via declarative workflow pipelines. Users own their data, not platforms.

## Core Principles

- **Clean breaks** over backward compatibility
- **Batch-first** — design for collections, single items are degenerate cases
- **Immutable domain** — pure transformations only
- **User goal-focused** — design around "what is the user trying to accomplish?" not "what can our APIs do?"
- **Multi-user** — design for concurrency, multi-tenancy (RLS), and shared-cache thundering-herd; `DEFAULT_USER_ID = "default"` is local-dev only

## Architecture

**Dependency Flow**: Interface → Application → Domain ← Infrastructure

**Layers**:
- **Domain** (`src/domain/`) - Pure business logic: track matching, playlist diff, metadata transforms. Zero external deps.
- **Application** (`src/application/`) - Use case orchestration: workflow primitives (enrich, filter, sort), sync likes, import history. `async with uow:` for transactions.
- **Infrastructure** (`src/infrastructure/`) - API adapters (Spotify/Last.fm/MusicBrainz), SQLAlchemy repos, metadata providers.
- **Interface** (`src/interface/`) - CLI via Typer + Rich, Web via FastAPI + React.

**Layer invariants** (hold even when creating a new file, before the path-scoped rule loads):
- `domain/`: entities `@define(frozen=True, slots=True)`, pure (no I/O), imports only domain + stdlib
- each layer imports inward only (Interface → Application → Domain ← Infrastructure); never reach sideways/outward
- `interface/` data access only via `execute_use_case()`; API route handlers stay 5–10 lines
- `application/` owns transactions (`async with uow:`); Commands/Results are frozen attrs

The last two have documented exceptions — credential/OAuth surfaces, and the `ChatUseCase` streaming loop. Both are written out in full in `.claude/rules/interface-patterns.md` and `application-patterns.md`, which load whenever you're editing the files they cover.

Layer-specific rules auto-load from `.claude/rules/` when editing matching files (note: they fire on read/edit, not always on new-file creation — the invariants above are the always-loaded safety net).

→ See docs/architecture/layers-and-patterns.md for full layer responsibilities

## Essential Commands

Docker must be running — tests use testcontainers PostgreSQL.

⚠️ **`.env.local`'s DATABASE_URL points at PROD and auto-loads for every command** — a "local" `mixd` invocation or ad-hoc script hits the production database unless you export `DATABASE_URL='postgresql+psycopg://mixd:mixd@localhost:5432/mixd'` first. Plain `uv run pytest` is safe (testcontainers). `mixd whoami` shows which DB/user a CLI invocation resolves to.

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
pnpm dev                             # Start everything: PostgreSQL + API + Vite
pnpm --prefix web dev                # Vite only (when API is already running)
pnpm --prefix web test               # Vitest component tests
pnpm --prefix web check              # Biome lint + format check
pnpm --prefix web build              # Production build
pnpm --prefix web sync-api           # Export OpenAPI schema + Orval codegen

# User-facing CLI
mixd workflow                      # Interactive workflow browser
mixd workflow run                  # Execute a workflow
mixd history import-lastfm         # Import listening history
mixd likes import-spotify          # Backup liked tracks
```

→ See docs/development.md for setup, versioning, and recipes

## Coding Patterns

Layer-specific coding patterns (attrs, SQLAlchemy, repository, use cases) live in `.claude/rules/` and load automatically when editing the relevant layer. Python 3.14+ conventions are in `.claude/rules/python-conventions.md`.

## Planning a Feature

Every feature serves a persona ([docs/personas.md](docs/personas.md)) and a user story ([docs/web-ui/01-user-flows.md](docs/web-ui/01-user-flows.md)) backed by a backlog spec ([docs/backlog/](docs/backlog/)). Verify the story's Given/When/Then criteria pass after implementing, and propose updates to 01-user-flows.md if development reveals gaps.

## Testing

Write tests for every change (happy path + at least one error/edge case).
Right test level — domain=unit, use case=unit+mocks, repository=integration.
Use existing factories from `tests.fixtures` (`make_track`, `make_mock_uow`).

The `slow`/`performance`/`diagnostic` markers are excluded by default — `uv run pytest -m ""` runs everything (~3.5min).

**Before committing**: `uv run pytest` and `pnpm --prefix web test`.

**The full release gate** — slow tests with coverage floor, basedpyright, ruff, vulture, suppression ratchet, backlog hygiene, dead-code, production build, Playwright visual gate — is `/ship` Step 6, which mirrors `.github/workflows/ci.yml`. Run that rather than reconstructing the list by hand.

## Documentation Map

- **Architecture** → docs/architecture/README.md
- **Development** → docs/development.md (setup, versioning, recipes)
- **Deployment** → docs/deployment.md (local dev, Fly.io deploy, CI/CD)
- **Database** → docs/architecture/database.md
- **Workflows** → docs/guides/workflows.md
- **Planning & Backlog** → docs/backlog/ (includes completed/ archive, unscheduled.md)
- **Product Decision Records** → docs/decisions/ (slow-burn decisions held open with evidence ledgers + activation triggers; append evidence rows when research surfaces relevant findings)
- **Personas** → docs/personas.md (proto-personas, anti-personas, audit findings)
- **Web UI Specs** → docs/web-ui/README.md
