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
- **Interface** (`src/interface/`) - CLI via Typer + Rich.

**Stack**: Python 3.14+, SQLite + SQLAlchemy 2.0 async, Prefect 3.0, attrs, Typer + Rich

Layer-specific enforcement rules live in `.claude/rules/` and load automatically per path.

→ See docs/ARCHITECTURE.md for full layer responsibilities

## Essential Commands

```bash
# Development
poetry run pytest                    # Fast tests (<1min)
poetry run pytest -m ""              # All tests including slow (~3.5min)
poetry run ruff check . --fix        # Lint + autofix
poetry run ruff format .             # Format
poetry run basedpyright src/         # Type check

# Database
poetry run alembic upgrade head      # Migrate
poetry run alembic revision --autogenerate -m "description"  # Generate migration

# User-facing CLI
narada workflow                      # Interactive workflow browser
narada workflow run                  # Execute a workflow
narada history import-lastfm         # Import listening history
narada likes import-spotify          # Backup liked tracks
```

→ See docs/DEVELOPMENT.md for full reference

## Required Coding Patterns

### Python 3.14+ Syntax (REQUIRED)

- **Generics**: `class Repository[TModel, TDomain]:` NOT `class Repository(Generic[TModel, TDomain])`
- **Unions**: `str | None` NOT `Optional[str]` or `Union[str, None]`
- **Type annotations**: `-> Track` NOT `-> "Track"` (PEP 649)
- **Cast**: `cast(Track, obj)` NOT `cast("Track", obj)`
- **Async**: `asyncio.run(coro)` or `asyncio.Runner()` NOT `get_event_loop()`
- **Timestamps**: `datetime.now(UTC)` NOT `datetime.now()` or `datetime.utcnow()`
- **UUID**: `uuid7()` for database IDs, `uuid4()` for random IDs only
- **Type guards**: `def is_valid(x: Any) -> TypeIs[str]:` over `hasattr()` + `# type: ignore`
- **No TYPE_CHECKING** unless circular imports
- **Concurrency**: `async with asyncio.TaskGroup() as tg:` NOT `asyncio.gather()` — structured cancellation on failure
- **Logging**: `get_logger(__name__).bind(service="...")` NOT `logging.getLogger()` — loguru with context binding
- **Loguru exception capture**: `logger.opt(exception=True).error(msg)` NOT `logger.error(msg, exc_info=True)` — loguru ignores `exc_info`; it lands in `.extra` as dead data instead of populating `.record.exception`
- **httpx event hooks on AsyncClient**: hooks MUST be `async def` — `AsyncClient` always awaits them; a sync `def` hook returns `None` and `await None` raises `TypeError`

### Domain Models (attrs - REQUIRED)

**Use `@define(frozen=True, slots=True)` for all domain entities:**
- `frozen=True` → immutable (pure transformations, safe concurrency)
- `slots=True` → memory efficient (operating on thousands of tracks)
- Apply to: Track, Playlist, Artist, Progress, all domain value objects
- Command/Result objects: `@define(frozen=True)` for use case inputs/outputs

### Repository + Unit of Work (DDD - REQUIRED)

**Repository**: Domain defines `Protocol` interfaces (`src/domain/repositories/`), Infrastructure implements (`src/infrastructure/persistence/repositories/`). Application injects via constructor.

**Unit of Work**: Manages atomic transactions across multiple repositories:
```python
async with uow:
    tracks = await uow.get_track_repository().save_batch(enriched_tracks)
    await uow.get_playlist_repository().update(playlist.with_tracks(tracks))
    await uow.commit()  # All succeed or all rollback
```

### SQLAlchemy 2.0 (CRITICAL)

- `selectinload()` for ALL relationships — lazy loading 1000 tracks = 1001 queries, selectinload = 2
- `expire_on_commit=False` in session config
- Async sessions: `async with AsyncSession() as session:`
- Batch operations: `save_batch()`, `get_by_ids()`, `delete_batch()`

### Prefect 3.0 Workflows (REQUIRED)

- **Shared session per workflow** (NOT session-per-task) — prevents SQLite "database locked"
- **Transactional semantics**: workflow commits OR rolls back atomically
- **Dependency injection**: use `SharedSessionProvider`
- **Declarative pipelines**: Source → Enricher → Filter → Sorter → Selector → Destination

→ See docs/workflow_guide.md for node catalog

### Use Case Execution (REQUIRED)

All use cases run through `application/runner.py`:
```python
# execute_use_case() handles session creation, UoW wiring, cleanup
result = await execute_use_case(lambda uow: SyncLikesUseCase(uow).execute(cmd))

# CLI wraps with run_async() (sync Typer bridge); FastAPI calls directly
```

- Single responsibility per use case: `SyncLikesUseCase`, `CreatePlaylistUseCase`, `ImportPlayHistoryUseCase`
- Constructor injection for dependencies
- Use case owns commit/rollback — delegates logic to domain

### Batch-First Design (REQUIRED)

- Design APIs for `list[Track]`, single items use single-element lists
- Repository methods: `save_batch()`, `get_by_ids()`, `delete_batch()`
- API calls: batch requests (Spotify: 50 tracks/request)
- Use `application/utilities/` batch processors

## Testing

**Tests are mandatory for every implementation.** A feature is not done until tests exist and pass.

- `poetry run pytest` for fast tests, `poetry run pytest -m ""` for all
- **ALWAYS** use `db_session` fixture, NEVER `get_session()`
- No `--timeout` flag configured

### Self-Check (after every implementation)
1. Did I write tests? If not, write them before considering the task complete
2. Right level? Domain=unit, UseCase=unit+mocks, Repository=integration
3. Beyond happy path? Error cases, edge cases, validation
4. Using existing factories? `make_track`, `make_mock_uow` from `tests.fixtures`
5. Right directory? Mirror the source path under `tests/unit/` or `tests/integration/`
6. Tests pass? `poetry run pytest tests/path/to/test_file.py -x`
7. Complex feature? For multi-layer implementations, consult `test-pyramid-architect` subagent for strategy review

## Documentation Map

- **Architecture** → docs/ARCHITECTURE.md
- **Development** → docs/DEVELOPMENT.md
- **Database** → docs/DATABASE.md
- **Workflows** → docs/workflow_guide.md
- **CLI Reference** → docs/API.md
- **Backlog** → docs/BACKLOG.md
- **Ideas** → docs/IDEAS.md
