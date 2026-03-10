# Narada Development Guide

## Getting Started

### Setup
```bash
git clone <repository-url> && cd narada
poetry install && source $(poetry env info --path)/bin/activate
cp .env.example .env  # Edit with your service credentials
poetry run alembic upgrade head
poetry run pytest && poetry run narada --help  # Verify installation
```

## Essential Commands
See `CLAUDE.md` for complete reference:

```bash
# Testing (Fast Development Workflow)
poetry run pytest                               # Fast tests (skip slow/diagnostic, <1min)
poetry run pytest -m "unit"                     # Unit tests only (<10s)
poetry run pytest tests/unit/domain/            # Fast domain tests
poetry run pytest tests/integration/            # Database integration tests
poetry run pytest --cov=narada --cov-report=html # Coverage

# Testing (Complete/CI Workflow)
poetry run pytest -m ""                         # ALL tests including slow/diagnostic
poetry run pytest -m "slow"                     # Slow tests only
poetry run pytest -m "diagnostic"               # Diagnostic/profiling tests only

# Quality
poetry run ruff check . --fix --unsafe-fixes    # Modern Python 3.14+ linting
poetry run ruff format .                        # Format
poetry run basedpyright src/                    # Type check

# Database
poetry run alembic revision --autogenerate     # Generate migration
poetry run alembic upgrade head                # Apply migrations

# API & Code Generation
poetry run export-openapi                      # Export OpenAPI schema → web/openapi.json
pnpm --prefix web generate                     # Orval codegen (types + hooks + MSW)
pnpm --prefix web sync-api                     # Both: export schema + Orval codegen
```

## Version Management

Version is defined **once** in `pyproject.toml` and derived everywhere else:

```
pyproject.toml  ──→  importlib.metadata.version("narada")
                         │
                         ├── src/__version__
                         ├── FastAPI app.version (app.py)
                         ├── Health endpoint (health.py)
                         └── OpenAPI schema → Orval types
```

**To bump the version:**
```bash
# 1. Edit pyproject.toml (the ONE source of truth)
# 2. Regenerate OpenAPI schema + Orval types:
pnpm --prefix web sync-api
# 3. Update docs/backlog/README.md manually (semantic content)
```

## Architecture

### DDD + Hexagonal Layers
**Dependency Flow**: Interface → Application → Domain ← Infrastructure

```
src/
├── config/                  # Settings, logging, constants
│
├── domain/                  # Pure business logic (zero dependencies)
│   ├── entities/           # DDD aggregates (Track, Playlist)
│   └── repositories/       # Abstract repository protocols
│
├── application/            # Use cases and orchestration
│   ├── services/          # Application services
│   └── use_cases/         # DDD use cases
│
├── infrastructure/        # External adapters
│   ├── connectors/       # External service adapters
│   └── persistence/      # Database adapters
│
└── interface/            # Primary adapters
    └── cli/             # CLI entry points
```

### Key Files
- `src/domain/entities/track.py` - Core Track/Artist entities
- `src/domain/entities/playlist.py` - Playlist domain objects  
- `src/infrastructure/persistence/database/db_models.py` - SQLAlchemy models
- `src/infrastructure/persistence/repositories/` - Repository implementations

## Core Patterns

### Repository Pattern
Domain defines ports, infrastructure provides adapters:
```python
# Domain protocol (port)
class TrackRepository(Protocol):
    async def save_batch(self, tracks: list[Track]) -> list[Track]: ...

# Infrastructure adapter  
class SQLAlchemyTrackRepository:
    async def save_batch(self, tracks: list[Track]) -> list[Track]:
        # SQLAlchemy implementation
```

### UnitOfWork Pattern
Transaction boundary control:
```python
async with get_unit_of_work() as uow:
    track_repo = uow.get_track_repository()
    await track_repo.save_batch(tracks)
    await uow.commit()
```

## Testing Strategy

### Test Pyramid (Speed & Coverage) - 2026 Best Practice
- **Unit Tests** (`tests/unit/`) - Fast, isolated, 60%+ of test suite, <100ms each
- **Integration Tests** (`tests/integration/`) - Real database/APIs, 35% of test suite  
- **E2E Tests** - Critical user workflows, 5% of test suite

### Test Structure (Modern pytest Organization)
```
tests/
├── conftest.py                    # Root fixtures: db_session, test_data_tracker
├── unit/                          # Fast, isolated tests (<100ms)
│   ├── domain/                   # Pure business logic tests
│   ├── application/              # Use cases with mocked repositories
│   ├── infrastructure/           # Connector logic with mocks
│   ├── config/                   # Config layer tests
│   └── interface/                # CLI command tests
├── integration/                  # Real database, external services
│   ├── connectors/              # External service integration tests
│   ├── repositories/            # Database integration tests
│   ├── use_cases/               # End-to-end use case tests
│   └── workflows/               # Workflow execution tests
├── fixtures/                     # Shared test data models
├── diagnostics/                  # Diagnostic and investigation tests
└── data/                        # Test data files
```

### Fixture Organization
- **Root** (`tests/conftest.py`) - `db_session`, `test_data_tracker` with automatic cleanup
- **Unit** (`tests/unit/*/conftest.py`) - Mocked repositories and services by layer
- **Integration** (`tests/integration/conftest.py`) - Real database fixtures with cleanup
- **Shared** (`tests/fixtures/`) - Test data models and factory functions

### Test Execution Workflow (2025 Best Practice)

Tests are organized by speed to optimize development feedback loops:

**Fast Development Loop** (default, <1 minute):
```bash
poetry run pytest                    # Skips slow and diagnostic tests
poetry run pytest -m "unit"          # Unit tests only (<10s)
```

**Specific Test Categories**:
```bash
# By marker
poetry run pytest -m "slow"          # Slow tests only (>1s each)
poetry run pytest -m "performance"   # Performance tests only (>5s each)
poetry run pytest -m "diagnostic"    # Diagnostic/profiling tests

# By layer
poetry run pytest -m "unit"                      # Unit tests only
poetry run pytest -m "integration"               # Integration tests only
poetry run pytest -m "integration and not slow"  # Fast integration tests
poetry run pytest tests/unit/                    # All unit tests (by path)
poetry run pytest tests/integration/             # All integration tests (by path)
```

**Complete Test Suite** (CI/CD):
```bash
poetry run pytest -m ""              # Run ALL tests including slow/diagnostic
```

**Marker Definitions** (6 total — applied via `pytestmark` in conftest or per-test decorator):
- `unit`: Fast, isolated tests (<100ms each) — auto-applied to `tests/unit/`
- `integration`: Real DB/APIs (<1s each) — auto-applied to `tests/integration/`
- `e2e`: Full system, critical user flows (<10s) — applied per-test
- `slow`: Tests taking >1s (skipped by default)
- `performance`: Tests taking >5s (skipped by default)
- `diagnostic`: Investigation/profiling tests (skipped by default)

### Testing Patterns

#### Domain Tests (Pure Logic)
```python
# Pure business logic with no external dependencies
def test_track_matching_algorithm():
    matcher = TrackMatcher()
    result = matcher.calculate_confidence("Song Title", "Song Title")
    assert result >= 0.95
```

#### Integration Tests (Real Database)
```python
# Repository with database and automatic cleanup
@pytest.mark.asyncio
async def test_track_persistence(db_session, test_data_tracker):
    uow = get_unit_of_work(db_session)
    track = Track(title="TEST_Song", artists=[Artist(name="TEST_Artist")])
    saved = await uow.get_track_repository().save_track(track)
    test_data_tracker.add_track(saved.id)  # Auto-cleanup
    
    found = await uow.get_track_repository().get_by_id(saved.id)
    assert found.title == "TEST_Song"
```

#### E2E Tests (Complete Workflows)
```python  
# Full CLI command integration
def test_import_command(cli_runner):
    result = cli_runner.run(["likes", "import-spotify", "--limit", "5"])
    assert result.exit_code == 0
    assert "✓ Spotify likes import completed" in result.output
```

### Test Data Fixtures
Use `tests/fixtures/` for test data creation — plain factory functions, not pytest fixtures:
```python
from tests.fixtures import make_track, make_connector_track, make_playlist, make_mock_uow

# Domain entity factories (keyword overrides for any Track field)
track = make_track(id=1, title="Test", isrc="US1234567890")
ct = make_connector_track("sp_123")
playlist = make_playlist(id=1, name="Test Playlist")

# Mock UoW with pre-wired repos (configure specific repos as needed)
uow = make_mock_uow()
uow.get_track_repository().save_track.side_effect = lambda t: t.with_id(100)
```

### Test Commands
```bash
# Fast development feedback (skips slow tests by default)
poetry run pytest                               # Fast tests (<1min)
poetry run pytest -x                            # Stop on first failure
poetry run pytest --lf                          # Run last failed tests only
poetry run pytest tests/unit/domain/            # Pure business logic (fastest)

# Specific test categories
poetry run pytest -m "unit"                     # Unit tests only
poetry run pytest -m "integration and not slow" # Fast integration tests
poetry run pytest -m "slow"                     # Slow tests only
poetry run pytest -m "diagnostic"               # Diagnostic/profiling tests

# Complete test suite (CI/CD)
poetry run pytest -m ""                         # ALL tests including slow/diagnostic
poetry run pytest tests/integration/            # All integration tests

# Coverage and quality
poetry run pytest --cov=src --cov-report=html   # Coverage report
poetry run pytest --durations=20                # Find slowest 20 tests
poetry run pytest -k "test_track_"              # Focus on specific functionality
```

## Adding Features

### Domain-First Development
1. **Domain Entity** - Pure business logic with no dependencies
2. **Repository Protocol** - Abstract interface in domain layer  
3. **Use Case** - Application orchestration with dependency injection
4. **Infrastructure** - Repository implementation and CLI command
5. **Tests** - Domain → Application → Integration

```python
# 1. Domain entity (src/domain/entities/)
@define(frozen=True, slots=True)
class NewEntity:
    name: str
    value: int

# 2. Repository protocol (src/domain/repositories/)  
class NewEntityRepository(Protocol):
    async def save(self, entity: NewEntity) -> NewEntity: ...

# 3. Use case (src/application/use_cases/)
class NewFeatureUseCase:
    def __init__(self, repository: NewEntityRepository):
        self.repository = repository
```

## Common Tasks

### CLI Command
1. Create use case in `src/application/use_cases/`
2. Create CLI command in `src/interface/cli/`
3. Wire with dependency injection

### Workflow Node
```python
# Create transform in domain/transforms/ or application/metadata_transforms/
# Register in application/workflows/node_catalog.py

from src.application.workflows.node_catalog import node

@node("sorter.custom_sort", category="sorter")
async def custom_sort_node(tracklist: TrackList, config: dict) -> TrackList:
    # Your sorting logic
    return sorted_tracklist
```

### External Service Connector

See `.claude/skills/new-connector/` for the full step-by-step guide, or run `/new-connector` to invoke it.

### Database Changes
1. Update `src/infrastructure/persistence/database/db_models.py`
2. Generate: `poetry run alembic revision --autogenerate`
3. Apply: `poetry run alembic upgrade head`

## Code Standards

### Principles
- **Ruthlessly DRY** - No code duplication in single-maintainer codebase
- **Batch-First** - Design for collections, single items are degenerate cases
- **Immutable Domain** - Pure transformations with no side effects
- **Python 3.14+** - Modern syntax, type safety, strict typing

### Conventions
- 88-character lines, double quotes, Google docstrings
- Type everything: `list[str]`, `dict[str, Any]`, generics `[T, R]`
- UTC timestamps: `datetime.now(UTC)`
- Dependency injection in use cases

### Shared Utilities (`src/application/use_cases/_shared/`)

Use these utilities to eliminate duplication in playlist-related use cases:

```python
from src.application.use_cases._shared import (
    # Operation counting for playlist diffs
    count_operation_types,           # Returns OperationCounts(added, removed, moved)

    # Type-safe result objects (replace tuple returns)
    OperationCounts,                 # Instead of tuple[int, int, int]
    ApiExecutionResult,              # For API operation results
    AppendOperationResult,           # For append operations

    # Fluent metadata builders (replace dict construction)
    PlaylistMetadataBuilder,         # .with_timestamp().with_operations().build()
    build_api_execution_metadata,
    build_database_update_metadata,

    # Validation and error classification
    classify_connector_api_error,    # Pattern matching for API errors
    classify_database_error,
    ConnectorPlaylistUpdateValidator,

    # Playlist item factories
    create_connector_playlist_items_from_tracks,
)
```

**Extract to `_shared/` when**: Logic duplicated in 3+ files (not 2)
**Keep local when**: Single use, domain-specific, or context-dependent

### Intentional "Duplication" (Don't Extract)

Some patterns repeat by design in hexagonal architecture:

**Command/Result Classes** - Each use case has its own types
```python
# ✅ Correct: Each use case defines its own Command/Result
@define(frozen=True)
class GetPlayedTracksCommand:  # Specific to this use case
    limit: int
    days_back: int | None
```

**Transaction Boundaries** - Each use case manages its own UoW
```python
# ✅ Correct: Use case controls transaction lifecycle
async with uow:
    result = await self._execute_operation(...)
    await uow.commit()  # Business logic decides when
```

**Context-Specific Error Handling** - Preserves valuable debugging info
```python
# ✅ Correct: Context-specific error messages
except Exception as e:
    logger.error("Playlist sync failed", playlist_id=id, error=str(e))
    # Don't extract - context matters for debugging
```

## Logging

### Basic Usage
```python
from src.config.logging import get_logger

logger = get_logger(__name__)
logger.info("Operation complete", batch_size=100, status="success")
```

### Error Handling
```python
from src.config.logging import resilient_operation

@resilient_operation("spotify_sync")
async def sync_playlist(playlist_id: str):
    # Auto-logs timing, errors with HTTP classification
    return await spotify.get_playlist(playlist_id)

@resilient_operation("batch_import", include_timing=False)
async def import_batch(items: list):
    # Skip timing for bulk operations
    return await process_items(items)
```

### Production Configuration
Set environment variables for production safety:
```bash
# Disable sensitive data logging in production
export LOGGING__DIAGNOSE_IN_PRODUCTION=false
export LOGGING__BACKTRACE_IN_PRODUCTION=false

# Configure log management
export LOGGING__ROTATION="50 MB"
export LOGGING__RETENTION="2 weeks"
export LOGGING__FILE_LEVEL="INFO"
```

## Troubleshooting

### Quick Fixes
```bash
# Type errors
poetry run basedpyright src/

# Test failures  
poetry run pytest -v --tb=short --lf

# Database reset
rm data/narada.db && poetry run alembic upgrade head

# Migration status
poetry run alembic current
```

## Subagent Usage Guide

See `.claude/skills/subagent-guide/` for the full subagent usage guide — agent descriptions, rotation strategy, when-to-use decision matrix, tool scope table, and best practices.

---

## Resources

### Core Documentation
- **[CLAUDE.md](../CLAUDE.md)** - Essential commands and coding standards
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design and patterns
- **[DATABASE.md](DATABASE.md)** - Schema reference
- **[Backlog](backlog/)** - Project roadmap

### External References  
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/) - Database ORM
- [Typer](https://typer.tiangolo.com/) - CLI framework
- [BasedPyright](https://github.com/DetachHead/basedpyright) - Type checker