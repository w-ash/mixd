# Layers & Architectural Patterns

How Narada's Clean Architecture is structured, the patterns each layer uses, and the core system components that implement cross-service operations.

## Clean Architecture Foundation

Narada implements Clean Architecture principles with strict dependency boundaries to ensure maintainability, testability, and adaptability.

### Dependency Flow

```
Interface → Application → Domain ← Infrastructure ← External Services
```

Dependencies only flow inward, creating a stable core surrounded by adaptable interfaces.

### Layer Responsibilities

#### Config Layer (`src/config/`)
- **Purpose**: Application configuration and cross-cutting concerns
- **Contents**: Settings management, logging setup, constants
- **Structure**:
  - `settings.py` - Pydantic Settings with environment variable loading
  - `logging.py` - Loguru configuration with structured logging
  - `constants.py` - Application-wide constants
- **Key Principle**: Zero business logic, only configuration and cross-cutting utilities

#### Interface Layer (`src/interface/`)
- **Purpose**: Primary adapters that provide entry points to the application
- **Contents**: CLI commands, UI components, future FastAPI web controllers
- **Structure**:
  - `cli/` - 5 Typer command groups (playlist, workflow, history, likes, track) plus:
    - `async_runner.py` - sync-to-async bridge for CLI (FastAPI won't need this)
    - `interactive_menu.py` - reusable Rich menu pattern
    - `ui.py`, `cli_helpers.py` - Rich/Typer utilities (CLI-specific, not shared)
    - `progress_provider.py` - Rich progress display with Live coordination
- **Key Principle**: Only calls application use cases via `execute_use_case()` runner, never accesses domain or infrastructure directly. All CLI-specific code stays in `cli/` — no `shared/` directory to avoid coupling future web interface to Rich/Typer.

#### Domain Layer (`src/domain/`)
- **Purpose**: Pure business logic with zero external dependencies
- **Contents**: Core entities, business rules, algorithms, repository interfaces, pure transformations
- **Structure**:
  - `entities/` - attrs classes (Track, Playlist, PlaylistEntry, Progress, Operations, Shared types)
  - `repositories/` - Abstract protocols (TrackRepositoryProtocol, PlaylistRepositoryProtocol, PlayRepositoryProtocol)
  - `matching/` - Track matching algorithms, evaluation service, protocols, confidence scoring types
  - `playlist/` - Diff engine for minimal playlist updates, execution strategies
  - `transforms/` - Pure transformation modules (filtering, sorting, selecting, combining, core operations)
  - `workflows/` - Playlist operation business rules and domain workflow logic
  - `services/` - Domain services (progress_coordinator)
- **Examples**: Track matching with confidence scoring, playlist diff algorithm for minimal API calls, pure functional transforms
- **Responsibilities**: Enforces business rules and data integrity (e.g., playlists can't have duplicate tracks, tracks must have valid structure)
- **Benefits**: Fast tests, pure functions, technology-agnostic, organized by domain concept
- **Key Principle**: Never touches databases, APIs, or external systems directly
- **Playlist Identity Preservation**: The `Playlist` entity uses `PlaylistEntry` (not bare tracks) to preserve track membership metadata (`added_at`, `added_by`) through playlist operations. This follows industry best practices (Spotify, MusicBrainz) where playlist-track relationships are "membership instances" with stable identity, not "position slots". Each `PlaylistEntry` preserves its record identity through reordering, enabling duplicate tracks and timestamp preservation.

#### Application Layer (`src/application/`)
- **Purpose**: Orchestration and transaction boundary management
- **Contents**: Use case implementations, workflow definitions, application services, utilities
- **Structure**:
  - `use_cases/` - High-level business operations (13 use cases)
    - Core playlist operations: create, read, update, delete canonical playlists
    - Connector operations: create/update connector playlists
    - Data operations: import play history, enrich tracks, sync likes
    - Query operations: list playlists, get liked/played tracks, match and identify tracks
  - `services/` - Application-level coordination services (7 services)
    - `batch_file_import_service.py` - Batch file import orchestration
    - `connector_playlist_processing_service.py` - Playlist processing coordination
    - `connector_playlist_sync_service.py` - Cross-service playlist synchronization
    - `metrics_application_service.py` - Metrics collection and caching coordination
    - `play_import_orchestrator.py` - Play history import orchestration
    - `playlist_backup_service.py` - Playlist backup and restoration
    - `progress_manager.py` - Progress tracking and UI coordination
  - `metadata_transforms/` - Metadata-aware transforms (metrics, shuffle, play_history, _helpers)
  - `utilities/` - Batch processing utilities (batch_results, enhanced_database_batch_processor, results)
  - `workflows/` - Prefect workflow definitions and node implementations (14 modules + workflow definitions/)
  - `runner.py` - Generic `execute_use_case[TResult]()` — session/UoW lifecycle for both CLI and FastAPI
- **Key Principle**: Uses repositories like contract-based tools, owns transaction control logic

#### Infrastructure Layer (`src/infrastructure/`)
- **Purpose**: External integrations and technical implementation
- **Contents**: Database repositories, API connectors, UnitOfWork implementations, persistence layer, infrastructure services
- **Examples**: SpotifyConnector, SQLAlchemyRepository implementations, DatabaseUnitOfWork
- **Structure**:
  - `connectors/` - External service API clients (Spotify, Last.fm, MusicBrainz, Apple Music)
  - `persistence/repositories/` - Concrete repository implementations organized by domain aggregate
    - `track/` - Core track operations, connector mappings, likes, metrics, plays
    - `playlist/` - Core playlist operations, connector mappings
    - `play/` - Play history and connector play operations
  - `persistence/database/` - SQLAlchemy models and database configuration
  - `services/` - Infrastructure-level services (4 services)
    - `base_play_importer.py` - Base class for play import implementations
    - `play_import_registry.py` - Registry for play import strategies
    - `track_identity_service_impl.py` - Track identity resolution implementation
    - `track_merge_service.py` - Canonical track merging operations
- **Responsibilities**:
  - **Implements repository contracts**: Provides concrete implementations that handle database queries and external service calls
  - **Handles technical transaction details**: Manages actual database connections, commits, rollbacks
  - **External service integration**: Communicates with Spotify, Last.fm, MusicBrainz APIs
- **Benefits**: Swappable implementations, isolated side effects, organized by domain
- **Key Principle**: Entirely behind interfaces, decoupled from application layer

### Why Clean Architecture?

1. **Testability**: Business logic isolated from external dependencies
2. **Maintainability**: Clear separation of concerns prevents tangled code
3. **Adaptability**: Can add new interfaces (web, mobile) without changing core logic
4. **Development Speed**: New features built on stable foundations
5. **Technology Independence**: Core logic works with any database or API

### Application Layer Orchestration

The application layer coordinates business processes without performing low-level work. It owns transaction boundaries (commit/rollback based on business rules), uses repositories as contract-based tools, and remains decoupled from database details, API specifics, and framework dependencies.

#### Use Case Runner Pattern

The `execute_use_case[TResult]()` function in `application/runner.py` is the single entry point for all use case execution. It handles session creation, UoW wiring, and cleanup:

```python
# Both CLI and FastAPI use the same runner
result = await execute_use_case(
    lambda uow: ImportTracksUseCase(uow).execute(command)
)

# CLI wraps this in run_async() for sync Typer commands
# FastAPI calls execute_use_case() directly (natively async)
```

This eliminates session/UoW boilerplate duplication across interfaces.

---

## Key Architectural Patterns

### Unit of Work Pattern
Centralizes transaction boundary management in the application layer.

```python
# Domain interface
class UnitOfWorkProtocol(Protocol):
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    def get_track_repository(self) -> TrackRepository: ...
    def get_playlist_repository(self) -> PlaylistRepository: ...

# Application layer usage
class SyncPlaylistUseCase:
    async def execute(self, command: SyncPlaylistCommand, uow: UnitOfWorkProtocol):
        async with uow:
            track_repo = uow.get_track_repository()
            playlist_repo = uow.get_playlist_repository()

            # Orchestrate business process
            current_playlist = await playlist_repo.get_by_id(command.playlist_id)
            updated_tracks = await self._apply_sync_changes(current_playlist, command.changes)
            validated_tracks = self._validate_sync_rules(updated_tracks)

            # Application decides transaction outcome
            if self._sync_validation_passes(validated_tracks):
                await playlist_repo.update_tracks(command.playlist_id, validated_tracks)
                await uow.commit()  # Application controls when to commit
            else:
                await uow.rollback()  # Application decides to rollback
```

**Benefits**: Application controls transaction lifecycle, clean separation of orchestration vs implementation, easy testing with mock UnitOfWork

### Repository Pattern
Centralizes data access with consistent async interfaces.

```python
# Domain interface
class TrackRepository(Protocol):
    async def get_by_spotify_ids(self, spotify_ids: list[str]) -> list[Track]:
        ...

    async def save_batch(self, tracks: list[Track]) -> list[Track]:
        ...

# Infrastructure implementation
class SQLAlchemyTrackRepository:
    async def get_by_spotify_ids(self, spotify_ids: list[str]) -> list[Track]:
        # Database-specific implementation
```

**Benefits**: Consistent data access, easy testing, swappable storage backends

### Command Pattern
Rich operation contexts with built-in validation.

```python
@dataclass
class UpdatePlaylistCommand:
    playlist_id: str
    tracks: list[Track]
    operation_type: OperationType
    conflict_resolution: ConflictStrategy

    def validate(self) -> None:
        # Validation logic
```

**Benefits**: Clear operation boundaries, validation encapsulation, audit trails

### Strategy Pattern
Pluggable algorithms for flexible behavior.

```python
class TrackMatchingStrategy(Protocol):
    async def match_tracks(self, tracks: list[Track]) -> list[MatchResult]:
        ...

class SpotifyTrackMatcher:
    async def match_tracks(self, tracks: list[Track]) -> list[MatchResult]:
        # Spotify-specific matching logic
```

**Benefits**: Algorithmic flexibility, easy testing, service extensibility

### Connector Capability Protocols
Typed narrow interfaces for specific connector operations. Instead of passing `Any` from the connector registry, call sites use capability protocols that describe what they need.

```python
# application/workflows/protocols.py — capability protocols
class LikedTrackConnector(Protocol):
    """Connector that can read liked/saved tracks."""
    async def get_liked_tracks(self, limit: int = 50, cursor: str | None = None) -> ...: ...

class LoveTrackConnector(Protocol):
    """Connector that can love/like tracks."""
    async def love_track(self, artist: str, title: str) -> bool: ...

class PlaylistConnector(Protocol):
    """Connector that supports playlist CRUD."""
    async def get_playlist_details(self, playlist_id: str) -> ...: ...
    async def execute_playlist_operations(self, ...) -> str | None: ...
    async def create_playlist(self, name: str, tracks: list[Track], ...) -> str: ...

# application/use_cases/_shared/connector_resolver.py — typed resolvers
def resolve_liked_track_connector(uow) -> LikedTrackConnector: ...
def resolve_love_track_connector(uow) -> LoveTrackConnector: ...
def resolve_playlist_connector(service, uow) -> PlaylistConnector: ...
```

**Benefits**: Call sites get type-checked method access instead of `Any`, each use case depends only on the capability it needs (Interface Segregation), and per-UoW connector caching ensures one httpx pool per transaction scope with deterministic `aclose()` cleanup.

### Workflow Pattern
Declarative transformation pipelines.

```python
# JSON workflow definition
{
  "tasks": [
    {"type": "source.spotify_playlist", "config": {"playlist_id": "..."}},
    {"type": "enricher.lastfm", "upstream": ["source"]},
    {"type": "sorter.by_metric", "config": {"metric": "play_count"}, "upstream": ["enricher"]}
  ]
}
```

**Benefits**: Composable operations, visual workflow building, non-technical configuration

### Async Patterns (Python 3.14+)

#### Use Case Runner (CLI + FastAPI shared entry point)
```python
# application/runner.py — both interfaces use this
from src.application.runner import execute_use_case

result = await execute_use_case(
    lambda uow: SyncLikesUseCase(uow).execute(command)
)
```

#### CLI Sync-to-Async Bridge
```python
# interface/cli/async_runner.py — CLI-only, FastAPI is natively async
from src.interface.cli.async_runner import run_async

run_async(execute_use_case(...))  # Uses asyncio.run() + ThreadPoolExecutor
```

#### Multiple Sequential Async Operations
```python
# Python 3.14+ pattern with asyncio.Runner
with asyncio.Runner() as runner:
    tracks = runner.run(fetch_tracks())
    process_tracks(tracks)
    result = runner.run(save_tracks(tracks))
```

---

## Core System Components

### Track Resolution Engine

**Challenge**: Music services use inconsistent track identifiers
**Solution**: Multi-stage resolution with pluggable matching providers

```
Stage 1: Provider-Based Matching (Service-specific matching via MatchProvider protocol)
Stage 2: Metadata Similarity Matching (Artist/Title fuzzy matching with rapidfuzz)
Stage 3: Confidence Scoring (Domain layer evaluates match quality)
Stage 4: Graceful Degradation (Preserve all data, even unmatched)
```

#### Matching Provider Architecture

The track resolution system uses a pluggable provider architecture:

```python
# Shared provider protocol
class MatchProvider(Protocol):
    async def fetch_raw_matches_for_tracks(
        self, tracks: list[Any], **options
    ) -> ProviderMatchResult: ...

    @property
    def service_name(self) -> str: ...

# Service-specific implementations
# - src/infrastructure/connectors/spotify/matching_provider.py
# - src/infrastructure/connectors/lastfm/matching_provider.py
# - src/infrastructure/connectors/musicbrainz/matching_provider.py
```

**Benefits**:
- 90% exact match rate with deterministic IDs
- Pluggable service-specific matching strategies
- Handles real-world data inconsistencies
- Preserves complete data for manual review

### Playlist Transformation System

**Challenge**: Static playlist management lacks sophisticated operations
**Solution**: Declarative workflow system with composable nodes

```
Source → Enricher → Filter → Sorter → Selector → Destination
```

**Node Categories**:
- **Sources**: Spotify playlists, albums, user libraries
- **Enrichers**: Last.fm play counts, MusicBrainz metadata
- **Filters**: Release date, play count, artist exclusions
- **Sorters**: Any metric, multiple criteria
- **Selectors**: First/last N, random sampling
- **Destinations**: Spotify playlist creation/updates

### Differential Playlist Updates

**Challenge**: Naive playlist replacement loses metadata and is inefficient
**Solution**: Intelligent differential algorithm

```
Calculate: Minimal add/remove/reorder operations
Preserve: Existing track order where possible
Handle: Concurrent external changes through conflict resolution
Optimize: API usage through batching and sequencing
```

**Benefits**:
- Preserves Spotify track addition timestamps
- Reduces API calls by 60-80%
- Handles external playlist changes gracefully
- Provides dry-run capability for preview

### Cross-Service Synchronization

**Challenge**: Services don't communicate with each other
**Solution**: Narada as intelligent intermediary

```
Service A → Narada (Resolution) → Service B
```

**Synchronization Types**:
- **Bidirectional Likes**: Spotify ↔ Last.fm through intelligent matching
- **Play History Import**: Spotify GDPR exports with enhanced resolution
- **Playlist Backup**: Local storage with restoration capability

---

## Cross-Cutting Concerns

- **Logging**: Loguru with JSON structured logging, context propagation via `get_logger(__name__).bind()`
- **Error Handling**: Tenacity retry with exponential backoff, `ErrorClassifier` protocol per connector
- **Progress**: Rich Live display with coordinated console logging via `RichProgressProvider`
- **Testing**: Comprehensive test suite (<1min fast suite), `db_session` fixture with isolated temp databases
- **Database Migrations**: Alembic with SQLAlchemy 2.0 auto-generation
- **Security**: OAuth 2.0 for service APIs, local-first data storage, env vars for secrets
