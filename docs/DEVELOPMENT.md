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
# Testing
poetry run pytest                               # All tests
poetry run pytest tests/domain/                 # Fast domain tests
poetry run pytest tests/integration/           # Database integration tests
poetry run pytest --cov=narada --cov-report=html # Coverage

# Quality
ruff check . --fix --unsafe-fixes              # Modern Python 3.13+ linting
ruff format .                                   # Format
poetry run basedpyright src/                   # Type check

# Database
poetry run alembic revision --autogenerate     # Generate migration
poetry run alembic upgrade head                # Apply migrations
```

## Architecture

### DDD + Hexagonal Layers
**Dependency Flow**: Interface → Application → Domain ← Infrastructure

```
src/
├── domain/                   # Pure business logic (zero dependencies)
│   ├── entities/            # DDD aggregates (Track, Playlist)
│   └── repositories/        # Abstract repository protocols
│
├── application/             # Use cases and orchestration
│   ├── services/           # Application services  
│   └── use_cases/          # DDD use cases
│
├── infrastructure/         # External adapters
│   ├── connectors/        # External service adapters
│   └── persistence/       # Database adapters
│
└── interface/             # Primary adapters
    └── cli/              # CLI entry points
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

### Test Pyramid (Speed & Coverage) - 2025 Best Practice
- **Unit Tests** (`tests/unit/`) - Fast, isolated, 60%+ of test suite, <100ms each
- **Integration Tests** (`tests/integration/`) - Real database/APIs, 35% of test suite  
- **E2E Tests** - Critical user workflows, 5% of test suite

### Test Structure (Modern pytest Organization)
```
tests/
├── conftest.py                    # Root fixtures: db_session, test_data_tracker
├── unit/                          # Fast, isolated tests (<100ms)
│   ├── domain/                   # Pure business logic, zero dependencies
│   ├── application/              # Use cases with mocked repositories
│   └── infrastructure/           # Connector logic with mocks
├── integration/                  # Real database, external services  
│   ├── repositories/            # Database integration tests
│   └── workflows/               # Multi-component integration
└── fixtures/                     # Shared test data and utilities
```

### Fixture Organization
- **Root** (`tests/conftest.py`) - `db_session`, `test_data_tracker` with automatic cleanup
- **Unit** (`tests/unit/*/conftest.py`) - Mocked repositories and services by layer
- **Integration** (`tests/integration/conftest.py`) - Real database fixtures with cleanup
- **Shared** (`tests/fixtures/`) - Cross-layer reusable test utilities

### Performance Tests
Performance tests are **excluded from regular runs** to keep test execution fast:
```bash
# Run all tests (includes performance tests - may be slow)
poetry run pytest

# Run only performance tests when needed
poetry run pytest -m "performance"

# Run integration tests excluding performance tests (faster)
poetry run pytest -m "integration and not performance"

# Run specific performance test file
poetry run pytest tests/integration/test_large_playlist_performance.py
```

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

### DRY Test Builders
Use `tests/shared/builders.py` for consistent test data:
```python
class TrackBuilder:
    def with_title(self, title: str) -> "TrackBuilder": ...
    def with_spotify_id(self, spotify_id: str) -> "TrackBuilder": ...
    def build(self) -> Track: ...

# Usage
track = TrackBuilder().with_title("Test").with_spotify_id("123").build()
```

### Test Commands
```bash
# Fast development feedback  
poetry run pytest tests/domain/ -x              # Pure business logic (fastest)
poetry run pytest tests/integration/ --maxfail=3  # Database integration
poetry run pytest --lf                          # Run last failed tests only

# Coverage and quality
poetry run pytest --cov=src --cov-report=html   # Coverage report  
poetry run pytest --durations=10                # Find slow tests
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
# src/application/workflows/transforms.py
async def new_transform(tracklist: TrackList) -> TrackList:
    # Transform logic

# src/application/workflows/node_catalog.py
@node("transformer.new_transform")
async def handle_new_transform(tracklist: TrackList, config: dict) -> TrackList:
    return await new_transform(tracklist, **config)
```

### External Service Connector
```python
# src/infrastructure/connectors/new_service/connector.py
class NewServiceConnector(BaseAPIConnector):
    @property
    def connector_name(self) -> str:
        return "new_service"
    
    def convert_track_to_connector(self, track_data: dict) -> ConnectorTrack:
        from .conversions import convert_new_service_track
        return convert_new_service_track(track_data)
    
    # Implement TrackMetadataConnector and/or PlaylistConnectorProtocol

# src/infrastructure/connectors/new_service/conversions.py
def convert_new_service_track(track_data: dict) -> ConnectorTrack:
    # Convert service API data to ConnectorTrack

# src/infrastructure/connectors/new_service/matching_provider.py  
class NewServiceMatchingProvider(BaseMatchingProvider):
    # Implement matching logic for this service
```

### Database Changes
1. Update `src/infrastructure/persistence/database/db_models.py`
2. Generate: `poetry run alembic revision --autogenerate`
3. Apply: `poetry run alembic upgrade head`

## Code Standards

### Principles
- **Ruthlessly DRY** - No code duplication in single-maintainer codebase
- **Batch-First** - Design for collections, single items are degenerate cases
- **Immutable Domain** - Pure transformations with no side effects
- **Python 3.13+** - Modern syntax, type safety, strict typing

### Conventions
- 88-character lines, double quotes, Google docstrings
- Type everything: `list[str]`, `dict[str, Any]`, generics `[T, R]`
- UTC timestamps: `datetime.now(UTC)`
- Dependency injection in use cases

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

## Resources

### Core Documentation
- **[CLAUDE.md](../CLAUDE.md)** - Essential commands and coding standards
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design and patterns
- **[DATABASE.md](DATABASE.md)** - Schema reference
- **[BACKLOG.md](../BACKLOG.md)** - Project roadmap

### External References  
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/) - Database ORM
- [Typer](https://typer.tiangolo.com/) - CLI framework
- [BasedPyright](https://github.com/DetachHead/basedpyright) - Type checker