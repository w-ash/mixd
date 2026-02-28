# Narada System Architecture

## System Overview

Narada is a personal music metadata hub that integrates Spotify, Last.fm, and MusicBrainz to give users ownership of their listening data while enabling powerful cross-service operations that no single platform provides.

### Core Problem

Music streaming services operate in silos. Users cannot:
- Sort a Spotify playlist by personal Last.fm play counts
- Sync likes between services through intelligent track matching
- Build sophisticated playlists using cross-service data
- Maintain ownership of their listening history and metadata

### Solution Architecture

By maintaining local representations of music entities with cross-service mappings, Narada enables features that transcend individual platform limitations. The system acts as an intelligent bridge between services, providing a unified view of personal music data.

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
  - `transforms/` - Application-level transforms (metrics, shuffle, play_history, _helpers)
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
# ✅ Python 3.14+ pattern with asyncio.Runner
with asyncio.Runner() as runner:
    tracks = runner.run(fetch_tracks())
    process_tracks(tracks)
    result = runner.run(save_tracks(tracks))
```

## Technology Stack

### Core Technology Decisions

#### Python 3.14+
**Why**: Enhanced typing, performance improvements, modern async patterns
**Usage**: Modern language features throughout codebase, PEP 749 support
**Benefits**: Better type safety, cleaner code, future-proofing, improved asyncio

#### SQLite + SQLAlchemy 2.0
**Why**: Zero configuration, atomic transactions, rich relationships
**Usage**: Local database with async ORM patterns and specialized session management
**Benefits**: No server setup, data integrity, complex queries, concurrent operation support

#### Prefect 3.0 (Workflow Engine)
**Why**: Modern async workflow orchestration with improved dependency management
**Usage**: Workflow orchestration with embedded mode and built-in dependency injection
**Benefits**: Native async support, retry logic, error handling, real-time feedback, transactional semantics

#### Typer + Rich (CLI)
**Why**: Type-safe CLI with beautiful output
**Usage**: Command-line interface with rich formatting
**Benefits**: Auto-completion, validation, professional UX

#### attrs (Domain Models)
**Why**: Immutable objects with minimal boilerplate
**Usage**: Domain entities and value objects
**Benefits**: Immutability, type safety, clean constructors

### Supporting Technologies

| Technology | Purpose | Rationale |
|------------|---------|-----------|
| **httpx** | HTTP client for all APIs | Async-first, native OAuth/rate limiting, replaces spotipy/pylast/musicbrainzngs |
| **tenacity** | Retry logic | Declarative retry patterns, exponential backoff, async-native |
| **aiolimiter** | Rate limiting | Async rate limiting for API compliance, leaky bucket algorithm |
| **rapidfuzz** | String matching | High-performance fuzzy matching for track resolution |
| **toolz** | Functional utilities | Functional composition, efficient data processing |
| **loguru** | Logging | Context-aware logging, minimal configuration |

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

## Data Architecture

### Entity Resolution Model

```
tracks (canonical) ↔ track_mappings ↔ connector_tracks (service-specific)
```

**Benefits**:
- Complete service metadata preservation
- Many-to-many track relationships
- Confidence scoring for match quality
- Independent service updates

### Temporal Data Design

- **Immutable Events**: Play history, sync operations
- **Time-Series Metrics**: Popularity, play counts
- **Checkpoint System**: Incremental sync state

**Benefits**: 
- Complete audit trail
- Efficient incremental operations
- Historical analysis capability

### Hard Delete Pattern

All entities use hard deletion for simplicity and performance. Data recovery relies on external API re-import and database backups.

**Benefits**:
- Simplified queries (no is_deleted filters)
- Better performance (smaller indexes)
- Cleaner data model
- External APIs serve as source of truth for recovery

## Database-First Workflow Architecture

### Critical Design Principle: Database-Centric Operations

**All workflow operations work exclusively on database tracks (`tracks` table), never directly on external connector data.**

This architectural constraint ensures system consistency and enables sophisticated cross-service operations that would be impossible with external-only data.

### Database Schema Relationships

```
External Playlists → Database Persistence → Workflow Operations

┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Spotify         │    │ Database        │    │ Workflows       │
│ Playlist        │───▶│ Persistence     │───▶│ (Enrichment,    │
│                 │    │                 │    │  Sorting, etc.) │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Core Database Tables

#### Primary Track Storage
- **`tracks`** - Internal canonical track representations (what workflows operate on)
- **`track_metrics`** - Metrics storage linked to internal tracks by `track_id`
- **`playlists`** - Internal playlist representations

#### Connector Integration
- **`connector_tracks`** - External service track representations
- **`track_mappings`** - Links internal tracks to external tracks (many-to-one)
- **`connector_playlists`** - External service playlist representations

### Mandatory Database Persistence Flow

#### 1. Source Node Responsibility
Source nodes (e.g., `spotify_playlist_source`) must:
- Fetch external playlist data
- Convert to domain entities (without database IDs)
- **Call `SavePlaylistUseCase` to persist to database**
- Return tracks with populated database IDs

#### 2. Track Upsert Strategy
The `TrackUpsertEnrichmentStrategy` ensures database consistency:
```python
# Repository handles upsert automatically via connector ID
saved_track = await self.track_repos.core.save_track(track)
# Returns track with database ID populated
```

#### 3. Workflow Operations
All downstream operations (enrichment, sorting, filtering) work with database tracks:
- **Input**: Tracks with `track.id != None`
- **Metrics**: Stored in `track_metrics` table by `track_id`
- **Enrichment**: Uses database track IDs for identity resolution

### Critical Developer Safeguards

#### Database ID Requirements
```python
# ✅ Correct: Tracks have database IDs
for track in tracklist.tracks:
    assert track.id is not None, "Track must have database ID"
```

#### Error Detection
Common failure pattern - tracks without database IDs:
```python
# ❌ Broken: Enrichment fails silently
if not tracks_with_ids:
    logger.warning("No tracks with database IDs - enrichment skipped")
    return {}
```

#### Source Node Pattern
```python
# ✅ Correct source node implementation
async def external_playlist_source(context, config):
    # 1. Fetch external playlist
    external_tracks = await connector.get_playlist(playlist_id)
    
    # 2. Convert to domain entities
    domain_tracks = [convert_to_domain(track) for track in external_tracks]
    
    # 3. MANDATORY: Persist to database
    save_command = SavePlaylistCommand(
        tracklist=TrackList(tracks=domain_tracks),
        enrichment_config=EnrichmentConfig(enabled=True),
        persistence_options=PersistenceOptions(operation_type="create_internal")
    )
    result = await SavePlaylistUseCase().execute(save_command)
    
    # 4. Return tracks with database IDs
    return {"tracklist": TrackList(tracks=result.enriched_tracks)}
```

### Data Consistency Benefits

#### 1. Cross-Service Operations
- Sort Spotify playlist by Last.fm play counts
- Sync likes between services through track matching
- Build sophisticated filters using cross-service data

#### 2. Reliable Enrichment
- Enrichment services require database track IDs
- Metrics stored with consistent track references
- Caching and freshness work with stable track identities

#### 3. Audit and History
- Complete operation history linked to database tracks
- Temporal data analysis across services
- Reliable backup and restoration

### Common Anti-Patterns to Avoid

#### ❌ Operating on External Data Directly
```python
# Wrong: Working with connector tracks directly
for spotify_track in spotify_playlist.tracks:
    # This breaks cross-service operations
    metric = lastfm.get_playcount(spotify_track.id)
```

#### ❌ Skipping Database Persistence
```python
# Wrong: Bypassing database persistence
return {"tracklist": TrackList(tracks=external_tracks)}
# These tracks have no database IDs!
```

#### ❌ Missing ID Validation
```python
# Wrong: No validation of database IDs
async def enrichment_step(tracklist):
    # Silently fails if tracks lack database IDs
    return await enrich_tracks(tracklist.tracks)
```

### Architecture Validation

To ensure architectural compliance:

1. **All tracks entering workflows must have database IDs**
2. **Source nodes must call `SavePlaylistUseCase`**
3. **Enrichment and metrics operations require database tracks**
4. **Cross-service operations work through database mappings**

This database-first approach is fundamental to Narada's ability to provide unified operations across music services while maintaining data consistency and enabling sophisticated cross-service workflows.

## Database Session Management Architecture

Narada implements a sophisticated session management strategy to handle SQLite's concurrency limitations while maintaining Clean Architecture principles and proper transaction boundary control through the UnitOfWork pattern.

### Transaction Management Philosophy

**Application Layer Controls Transaction Boundaries**: Use cases decide when transactions begin, commit, or rollback based on business logic, not just technical success/failure.

**Infrastructure Layer Handles Technical Implementation**: Database connections, session lifecycle, and transaction mechanics are managed by infrastructure components.

**Clean Separation**: Business logic remains decoupled from session management complexity.

### UnitOfWork Pattern Implementation

```python
# Domain interface
class UnitOfWork(Protocol):
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    def get_track_repository(self) -> TrackRepository: ...

# Infrastructure implementation
class DatabaseUnitOfWork:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._committed = False
        self._connector_cache: dict[str, Any] = {}  # Per-UoW instance caching

    async def commit(self):
        await self._session.commit()
        self._committed = True

    async def rollback(self):
        await self._session.rollback()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # ... commit/rollback logic ...
        # Close cached connector instances (httpx pools, etc.)
        for connector in self._connector_cache.values():
            if hasattr(connector, "aclose"):
                await connector.aclose()
        self._connector_cache.clear()

    def get_track_repository(self) -> TrackRepository:
        return TrackRepository(self._session)
```

### Session Management Patterns

#### 1. Workflow-Scoped Sessions
**Pattern**: Single shared session per workflow execution
**Implementation**: `SharedSessionProvider` in `prefect.py`
**Usage**: All Prefect workflow tasks share one session to prevent concurrent write conflicts

```python
# Create a single shared session for the entire workflow execution
async with get_session() as shared_session:
    # Create shared session provider that wraps the session
    shared_session_provider = SharedSessionProvider(shared_session)
    
    # All workflow tasks use the same session
    context = {
        "session_provider": shared_session_provider,
        "shared_session": shared_session,  
    }
```

**Benefits**: Eliminates SQLite "database is locked" errors, ensures ACID properties across workflow operations, simplifies transaction management.

#### 2. Session-Per-Operation Pattern
**Pattern**: Fresh session for each discrete operation
**Implementation**: `DatabaseProgressContext.run_with_repositories()`
**Usage**: CLI operations and use cases that don't run within workflows

```python
async with DatabaseProgressContext(...) as progress:
    # Each operation gets its own short-lived session
    async def _import_operation(repositories: TrackRepositories) -> OperationResult:
        # Session created and closed automatically
        return await service.import_play_history(...)
    
    return await progress.run_with_repositories(_import_operation)
```

**Benefits**: Prevents long-held sessions, follows SQLAlchemy best practices, maintains Clean Architecture boundaries.

#### 3. Isolated Sessions for Metrics
**Pattern**: Specialized sessions for operations needing isolation
**Implementation**: `get_isolated_session()` 
**Usage**: Metrics operations that may conflict with main operations

```python
async with get_isolated_session() as session:
    # Optimized session settings for metrics operations
    # - autoflush=False to avoid implicit I/O
    # - isolated transaction boundaries
```

**Benefits**: Prevents metrics operations from interfering with main workflows, optimized for specific use cases.

### SQLite Configuration

**Connection Pooling**: Uses `NullPool` for SQLite to create/close connections on demand, eliminating pooling-related locks.

**Pragmas Applied**:
- `journal_mode=WAL`: Write-ahead logging for concurrent read access
- `busy_timeout=30000`: 30-second timeout for lock conflicts
- `synchronous=NORMAL`: Balanced safety/performance
- `foreign_keys=ON`: Enforce referential integrity

**Event Listeners**: Automatically apply pragmas on each connection creation to ensure consistent database behavior.

### Anti-Patterns to Avoid

❌ **Multiple Concurrent Sessions in Workflows**: Creates SQLite lock conflicts
❌ **Long-Held Sessions**: Blocks other operations unnecessarily  
❌ **Direct Session Creation**: Bypasses configured pragmas and pooling strategy
❌ **Session Sharing Across Components**: Violates Clean Architecture boundaries

✅ **Use Workflow-Scoped Sessions**: For Prefect workflows
✅ **Use Session-Per-Operation**: For CLI and use case operations
✅ **Use Context Managers**: Ensure proper session lifecycle management
✅ **Follow Injection Patterns**: Maintain Clean Architecture compliance

## Clean Architecture Modernization (2026)

Narada follows modern clean architecture principles with strict adherence to dependency inversion and separation of concerns. The architecture has been continuously refined to eliminate redundancy while maintaining clean boundaries.

### Architecture Principles Applied

#### Ruthlessly DRY (With Intentional Exceptions)
**Principle**: Single-maintainer codebase demands zero redundancy. One implementation per concept, reused across contexts.

**Implementation**:
- ✅ **Eliminated 75+ lines of duplicate code** in workflow node factories
- ✅ **Removed temporary adapter classes** that violated single responsibility
- ✅ **Unified creation patterns** across all workflow components
- ✅ **Single shared implementation** for transform node creation
- ✅ **Shared utilities** in `_shared/` for playlist operations (extract at 3+ uses)

**Intentional Pattern Repetition** (Not Duplication):
- Each use case has its own `Command`/`Result` types (domain separation)
- Each use case manages its own transaction boundaries (business control)
- Error handling preserves context-specific information (debugging value)
- Validation logic reflects domain-specific business rules (not technical)

#### Clean Breaks, No Legacy Code
**Principle**: When modernizing architecture, make clean breaks rather than maintaining compatibility layers.

**Implementation**:
- ✅ **Eliminated adapter pattern completely** - direct connector and repository injection
- ✅ **Direct dependency injection** without wrapper objects
- ✅ **No temporary compatibility code** that would accumulate technical debt
- ✅ **Immediate cleanup** of all references to removed patterns

#### Dependency Injection Without Over-Engineering
**Principle**: Use dependency injection selectively where it provides clear benefits, avoid complex frameworks.

**Implementation**:
- ✅ **Constructor injection** for use cases and services
- ✅ **Existing repository patterns** used directly without additional abstractions
- ✅ **Prefect 3.0 integration** leverages built-in dependency management
- ✅ **Type hints and protocols** for interface clarity without runtime overhead

### Layer Compliance Rules

- Application layer never imports from `src.infrastructure` directly — uses `UnitOfWorkProtocol` and repository protocols
- Interface layer never accesses repositories — calls `execute_use_case()` which handles session/UoW
- Domain layer never imports from infrastructure (except `TYPE_CHECKING` for circular import cases)
- Infrastructure implements domain protocols, never exposes SQLAlchemy models to application

### Workflow System Architecture

#### Database-First Design
All workflow operations work exclusively on database tracks, ensuring:
- Cross-service data consistency
- Reliable enrichment and metrics
- Complete audit trails
- Sophisticated filtering capabilities

#### Prefect 3.0 Integration
Leverages modern workflow orchestration with:
- Transactional semantics for atomic operations
- Shared session management for SQLite compatibility
- Built-in retry logic and error handling
- Real-time progress tracking and artifacts

#### Node System Design
Declarative, composable workflow nodes with:
- Unified factory functions for transform, enrichment, and destination nodes
- Direct connector and repository injection through protocols
- Clean separation of transformation logic from infrastructure concerns
- Type-safe configuration and validation with comprehensive error handling

## Development Philosophy

### Ruthlessly DRY
Single-maintainer codebase demands zero redundancy. One implementation per concept, reused across contexts.

### Batch-First Design
Design for N items, single operations are degenerate cases. This scales naturally and reduces API overhead.

### Immutable Domain
Pure transformations without side effects. Easier to reason about, test, and debug.

### Framework-First
Leverage existing tools (Typer, Rich, Prefect) rather than building custom solutions. Focus development effort on unique business logic.

### Progressive Enhancement
Start with simple implementations, add sophistication incrementally. Avoid over-engineering for hypothetical future needs.

## Architectural Benefits

### Current Capabilities
- **Smart Playlist Operations**: Cross-service data transformations
- **Bidirectional Synchronization**: Intelligent track matching between services
- **Comprehensive Data Ownership**: Complete play history and metadata control
- **Sophisticated Updates**: Differential playlist operations with conflict resolution

### Future Extensibility

#### Adding New Music Services

Each music service connector is completely self-contained in its own folder:

```
src/infrastructure/connectors/spotify/
├── models.py              # Pydantic models for API response shapes
├── client.py              # API client (auth, requests, validates → models)
├── connector.py           # Main service interface
├── factory.py             # Creates all Spotify services
├── operations.py          # Core operations (get playlists, etc)
├── matching_provider.py   # Track matching logic
├── conversions.py         # Typed model → Domain model conversion
├── error_classifier.py    # Service-specific error handling
├── play_importer.py       # Play history import
├── play_resolver.py       # Play record resolution
├── personal_data.py       # GDPR export parsing
├── playlist_sync_operations.py # Playlist sync logic
└── utilities.py           # Spotify-specific helpers

src/infrastructure/connectors/_shared/
├── error_classification.py  # ErrorClassifier protocol + HTTPErrorClassifier base
├── failure_handling.py      # Match failure logging and utilities
├── isrc.py                  # Shared ISRC normalization/validation
├── matching_provider.py     # BaseMatchingProvider ABC (template method)
├── metric_registry.py       # Metric resolver registry
├── rate_limited_batch_processor.py
└── retry_policies.py        # Tenacity retry configuration
```

**To add YouTube Music:**
1. Copy existing connector: `cp -r src/infrastructure/connectors/spotify src/infrastructure/connectors/youtube_music`
2. Rename classes: `SpotifyConnector` → `YouTubeMusicConnector`
3. Implement interfaces: `ConnectorProtocol`, `MatchProvider`, etc.
4. Register in connector factory - **done!**

**Benefits**: Self-contained design means zero changes to other services when adding new ones.

#### Other Extensions
- **Web Interface** (v0.5.0): FastAPI backend using `execute_use_case()` runner + React frontend. Interface layer already restructured — CLI-specific code isolated in `interface/cli/`, `application/runner.py` ready for `Depends()` injection.
- **Advanced Analytics**: Machine learning on comprehensive listening data
- **Collaborative Features**: Multi-user support with existing architecture

### Technical Scalability
- **Database**: SQLite handles millions of tracks efficiently
- **API Efficiency**: Batch operations and caching minimize external calls
- **Memory Usage**: Streaming operations and lazy loading for large datasets
- **Performance**: Async-first design enables concurrent operations

## Cross-Cutting Concerns

- **Logging**: Loguru with JSON structured logging, context propagation via `get_logger(__name__).bind()`
- **Error Handling**: Tenacity retry with exponential backoff, `ErrorClassifier` protocol per connector
- **Progress**: Rich Live display with coordinated console logging via `RichProgressProvider`
- **Testing**: 1159 tests (<1min fast suite), `db_session` fixture with isolated temp databases
- **Database Migrations**: Alembic with SQLAlchemy 2.0 auto-generation
- **Security**: OAuth 2.0 for service APIs, local-first data storage, env vars for secrets

## Related Documentation

- **[DEVELOPMENT.md](DEVELOPMENT.md)** - Developer onboarding and contribution guide
- **[DATABASE.md](DATABASE.md)** - Database schema and design reference
- **[API.md](API.md)** - Complete CLI command reference
- **[workflow_guide.md](workflow_guide.md)** - Workflow system documentation
- **[likes_sync_guide.md](likes_sync_guide.md)** - Likes synchronization between Spotify and Last.fm
- **[ROADMAP.md](../ROADMAP.md)** - Project roadmap and planned features
- **[CLAUDE.md](../CLAUDE.md)** - Development commands and style guide