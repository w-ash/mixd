# Narada Database Design

## Overview

Narada's database design follows a focused schema pattern that prioritizes essential storage needs while maintaining flexibility for future expansion. Each entity maps to a core domain concept while avoiding unnecessary normalization that would increase query complexity.

The database uses SQLite with SQLAlchemy 2.0 async patterns for optimal performance in our single-user, local application context.

## Core Design Principles

### 1. Base Model Pattern
All tables inherit from `BaseEntity(DatabaseModel, TimestampMixin)` which provides:
- `id` (Primary Key)
- `created_at` (Record creation timestamp)
- `updated_at` (Last update timestamp)

This ensures consistent behavior across all entities with audit trails using hard deletes.

### 2. Hard Delete Strategy
All entities use hard deletion for simplicity and performance:
- Simplified queries without soft delete filtering
- Better performance with smaller indexes
- Data recovery relies on external API re-import and database backups
- External APIs serve as source of truth for data restoration

### 3. Connector Architecture
Separation between internal records and connector-specific entities allows:
- Complete metadata storage for each service
- Advanced cross-service entity resolution
- Independent service updates without affecting core data

### 4. JSON for Complex Data
Artists and raw metadata stored as JSON to:
- Avoid complex joins while supporting nested data structures
- Preserve complete information from external services
- Enable flexible querying without rigid schema constraints

### 5. Temporal Design
- Time-series metrics with explicit collection timestamps
- Event-based play records with chronological indexing
- Sync checkpoints for incremental processing

## Database Schema

### Core Entities

The schema consists of the following tables:
- `tracks` - Central track entities
- `connector_tracks` - Service-specific track representations
- `track_mappings` - Cross-service track relationships
- `track_metrics` - Time-series metrics
- `track_likes` - Like/favorite status per service
- `track_plays` - Immutable play events
- `playlists` - Playlist entities
- `playlist_mappings` - Playlist-to-service mappings
- `playlist_tracks` - Playlist-track relationships with ordering
- `sync_checkpoints` - Synchronization state tracking

## Table Definitions

### tracks
Central storage for music metadata with essential identification information.

```sql
CREATE TABLE tracks (
    id INTEGER PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    artists JSON NOT NULL,          -- List of artist names/IDs
    album VARCHAR(255),
    duration_ms INTEGER,
    release_date DATETIME,
    spotify_id VARCHAR(64),         -- Indexed for fast lookup
    isrc VARCHAR(32),               -- Indexed for entity resolution  
    mbid VARCHAR(36),               -- Indexed for MusicBrainz lookup
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE INDEX ix_tracks_spotify_id ON tracks(spotify_id);
CREATE INDEX ix_tracks_isrc ON tracks(isrc);  
CREATE INDEX ix_tracks_mbid ON tracks(mbid);
CREATE INDEX ix_tracks_title ON tracks(title);
```

**Key Points:**
- Primary source of truth for track information
- JSON artist storage for flexible multi-artist handling
- Direct storage for common identifiers (spotify_id, isrc, mbid)
- Rich relationships to mappings, metrics, likes, plays, and playlists

### connector_tracks
External service-specific track representations with rich metadata.

```sql
CREATE TABLE connector_tracks (
    id INTEGER PRIMARY KEY,
    connector_name VARCHAR(32) NOT NULL,           -- Service name (spotify, lastfm, etc)
    connector_track_identifier VARCHAR(64) NOT NULL, -- External service track ID
    title VARCHAR(255) NOT NULL,
    artists JSON NOT NULL,                         -- Artists as represented in service
    album VARCHAR(255),
    duration_ms INTEGER,
    isrc VARCHAR(32),                              -- ISRC code if available
    release_date DATETIME,
    raw_metadata JSON NOT NULL,                    -- Complete service-specific metadata
    last_updated DATETIME NOT NULL,                -- Timestamp of last metadata refresh
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE INDEX ix_connector_tracks_connector_name ON connector_tracks(connector_name);
CREATE INDEX ix_connector_tracks_lookup ON connector_tracks(connector_name, isrc);
CREATE UNIQUE INDEX ix_connector_tracks_unique ON connector_tracks(connector_name, connector_track_identifier);
```

**Key Points:**
- Preserves exactly how tracks appear in each service
- Captures all available metadata from each service
- Can exist before being mapped to internal tracks
- Minimizes API calls by storing complete external track data

### track_mappings
Connects internal tracks to external service tracks with match quality metadata.

```sql
CREATE TABLE track_mappings (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL,           -- FK to tracks table
    connector_track_id INTEGER NOT NULL, -- FK to connector_tracks table  
    connector_name VARCHAR(32) NOT NULL, -- Service name for indexing
    match_method VARCHAR(32) NOT NULL,   -- Resolution method used
    confidence INTEGER NOT NULL,         -- Match confidence (0-100)
    confidence_evidence JSON,            -- Evidence supporting confidence score
    is_primary BOOLEAN DEFAULT FALSE,    -- Primary mapping per track-connector pair
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
    FOREIGN KEY (connector_track_id) REFERENCES connector_tracks(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_connector_track_canonical_mapping ON track_mappings(connector_track_id, connector_name);
CREATE UNIQUE INDEX uq_primary_mapping ON track_mappings(track_id, connector_name) WHERE is_primary = TRUE;
CREATE INDEX ix_track_mappings_track_lookup ON track_mappings(track_id);
CREATE INDEX ix_track_mappings_connector_lookup ON track_mappings(connector_track_id);
```

**Key Points:**
- True many-to-many relationship between internal and external tracks
- Tracks match method and confidence for quality assessment
- Points to complete connector track records
- Supports future alternative match algorithms

### track_metrics
Time-series metrics for tracks from various services.

```sql
CREATE TABLE track_metrics (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL,           -- FK to tracks table
    connector_name VARCHAR(32) NOT NULL, -- Source service name
    metric_type VARCHAR(32) NOT NULL,    -- Metric type (play_count, explicit_flag, etc)
    value FLOAT NOT NULL,               -- Numeric metric value
    collected_at DATETIME NOT NULL,      -- When the metric was collected
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
    UNIQUE(track_id, connector_name, metric_type)
);

CREATE INDEX ix_track_metrics_lookup ON track_metrics(track_id, connector_name, metric_type);
```

**Key Points:**
- Time-series design for historical metrics with timestamps
- Supports various metric types (plays, explicit flag, etc.)
- Clearly identifies the source of each metric
- Floating-point values for wide range of metrics

### track_likes
Track preference state across music services with synchronization support.

```sql
CREATE TABLE track_likes (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL,           -- FK to tracks table
    service VARCHAR(32) NOT NULL,        -- Service name (spotify, lastfm, etc)
    is_liked BOOLEAN DEFAULT TRUE,       -- Current like status
    liked_at DATETIME,                  -- When the track was liked
    last_synced DATETIME,               -- When sync was last performed
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
    UNIQUE(track_id, service)
);

CREATE INDEX ix_track_likes_lookup ON track_likes(service, is_liked);
```

**Key Points:**
- Records like/favorite status per service
- Timestamps for tracking synchronization
- Captures when tracks were liked
- One like entry per track/service combination

### track_plays
Immutable record of track play events from service imports.

```sql
CREATE TABLE track_plays (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL,           -- FK to tracks table
    service VARCHAR(32) NOT NULL,        -- Service name (spotify, lastfm, etc)
    played_at DATETIME NOT NULL,         -- When the track was played
    ms_played INTEGER,                  -- Milliseconds played (optional)
    context JSON,                       -- Additional play context
    import_timestamp DATETIME,          -- When this record was imported
    import_source VARCHAR(32),          -- Source of import (e.g., "spotify_export", "lastfm_api")
    import_batch_id VARCHAR(64),        -- Batch identifier for import group
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
    UNIQUE(track_id, service, played_at, ms_played)  -- Prevent duplicate plays
);

CREATE INDEX ix_track_plays_service ON track_plays(service);
CREATE INDEX ix_track_plays_played_at ON track_plays(played_at);
CREATE INDEX ix_track_plays_track_id ON track_plays(track_id);
CREATE INDEX ix_track_plays_track_played ON track_plays(track_id, played_at);
CREATE INDEX ix_track_plays_import_source ON track_plays(import_source);
CREATE INDEX ix_track_plays_import_batch ON track_plays(import_batch_id);
```

**Key Points:**
- Records individual play events from service exports
- Optimized for chronological queries with played_at index
- Full provenance tracking for data imports
- Support for efficient bulk imports with batch IDs
- JSON field for platform-specific metadata

### playlists
Source of truth for playlists with essential metadata.

```sql
CREATE TABLE playlists (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description VARCHAR(1000),
    track_count INTEGER DEFAULT 0,       -- Cached count for efficiency
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
```

**Key Points:**
- Connector-agnostic playlist representation
- Minimal schema with only essential fields
- Pre-calculated track count for efficient display

### playlist_mappings
Maps internal playlists to external connector playlists.

```sql
CREATE TABLE playlist_mappings (
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL,        -- FK to playlists table
    connector_name VARCHAR(32) NOT NULL, -- Connector name (spotify, apple, etc)
    connector_playlist_id INTEGER NOT NULL, -- FK to connector_playlists table
    last_synced DATETIME,               -- Last successful sync
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
    FOREIGN KEY (connector_playlist_id) REFERENCES connector_playlists(id) ON DELETE CASCADE,
    UNIQUE(playlist_id, connector_name),
    UNIQUE(connector_playlist_id)
);
```

**Key Points:**
- Timestamps for synchronization history
- Each playlist maps to exactly one external playlist per connector
- Follows same mapping pattern as track resolution

### playlist_tracks
Maps the many-to-many relationship between playlists and tracks with ordering.

```sql
CREATE TABLE playlist_tracks (
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL,        -- FK to playlists table
    track_id INTEGER NOT NULL,           -- FK to tracks table
    sort_key VARCHAR(32) NOT NULL,       -- Lexicographical ordering key
    added_at DATETIME,                  -- When track was added to playlist
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
);

CREATE INDEX ix_playlist_tracks_order ON playlist_tracks(playlist_id, sort_key);
```

**Key Points:**
- Efficient ordering using lexicographical sort keys (e.g., "a0000000")
- Implicit history through track position changes preserved via timestamps
- Composite index optimizes ordered track fetching

### sync_checkpoints
Tracks synchronization state for incremental operations.

```sql
CREATE TABLE sync_checkpoints (
    id INTEGER PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,        -- User identifier
    service VARCHAR(32) NOT NULL,        -- Service name (spotify, lastfm, etc)
    entity_type VARCHAR(32) NOT NULL,    -- Entity type (likes, plays, etc)
    last_timestamp DATETIME,             -- Last successful sync timestamp
    cursor VARCHAR(1024),                -- Continuation token if applicable
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE(user_id, service, entity_type)
);
```

**Key Points:**
- Enables efficient incremental operations
- Cursor support for paginated operations
- Supports different entity types per service
- Tracks separate sync state per user

## Repository Pattern Implementation

**Complete separation between business logic and database implementation:**

```
Domain:        TrackRepositoryProtocol (interface - never changes)
                         ↑
Infrastructure: SQLAlchemyTrackRepository (current implementation)
Future:        PostgreSQLTrackRepository, MongoTrackRepository, RedisTrackRepository
```

**How it works:**
- **Domain defines contracts** - `TrackRepositoryProtocol` in `src/domain/repositories/interfaces.py`
- **Infrastructure implements** - `SQLAlchemyTrackRepository` in `src/infrastructure/persistence/repositories/`
- **Application uses contracts** - `def __init__(self, track_repo: TrackRepositoryProtocol)`
- **UnitOfWork coordinates** - `async with uow:` manages transactions, `await uow.commit()` saves changes

**Key files for database swaps:**
- `src/domain/repositories/interfaces.py` - Abstract protocols (never change)
- `src/infrastructure/persistence/repositories/base_repo.py` - Generic base (replace for new DB)
- `src/infrastructure/persistence/repositories/track/core.py` - Track operations (reimplement for new DB)
- **Critical** - Always use `selectinload()` for relationships, UnitOfWork for transactions

## Relationship Architecture

The database uses a rich relationship model with SQLAlchemy's relationship features:

### Core Track Relationships
- `Track` → `TrackMappings` → `ConnectorTracks` (many-to-many)
- `Track` → `TrackMetrics` (one-to-many)
- `Track` → `TrackLikes` (one-to-many)
- `Track` → `TrackPlays` (one-to-many)
- `Track` → `PlaylistTracks` → `Playlists` (many-to-many)

### Playlist Relationships
- `Playlist` → `PlaylistTracks` → `Tracks` (many-to-many)
- `Playlist` → `PlaylistMappings` (one-to-many)

### Cascade Behavior
- **Hard cascade deletes**: `ON DELETE CASCADE` for foreign key relationships
- **Orphan removal**: SQLAlchemy `cascade="all, delete-orphan"` for owned relationships
- **Passive deletes**: `passive_deletes=True` for performance optimization
- **Clean relationship management**: Proper cleanup without soft delete complexity

## Indexing Strategy

| Table | Index | Purpose |
|-------|-------|---------|
| `tracks` | `spotify_id` | Fast lookup by Spotify ID |
| `tracks` | `isrc` | Fast lookup for entity resolution |
| `tracks` | `mbid` | Fast lookup by MusicBrainz ID |
| `connector_tracks` | `connector_name` | Fast filtering by service |
| `connector_tracks` | `(connector_name, connector_track_id)` | Prevent duplicates |
| `connector_tracks` | `(connector_name, isrc)` | Lookup by service and ISRC |
| `track_mappings` | `(track_id, connector_track_id)` | Fast relationship lookup |
| `track_metrics` | `(track_id, connector_name, metric_type)` | Fast metric lookup |
| `track_likes` | `(track_id, service)` | Enforce single like entry |
| `track_likes` | `(service, is_liked)` | Fast filtering by service |
| `track_plays` | `service` | Filter by service |
| `track_plays` | `played_at` | Chronological queries |
| `playlist_tracks` | `(playlist_id, sort_key)` | Ordered track retrieval |
| `playlist_mappings` | `(playlist_id, connector_name)` | Enforce single mapping |
| `sync_checkpoints` | `(user_id, service, entity_type)` | Enforce single checkpoint |

## Database Session Management

The database implementation uses SQLite-specific optimizations:

### SQLite Connection Configuration
- **NullPool**: Creates connections on-demand and closes immediately for SQLite
- **WAL Mode**: Write-ahead logging for concurrent read access
- **Busy Timeout**: 30-second timeout for handling database locks
- **Pragmas Applied**: `synchronous=NORMAL`, `foreign_keys=ON`, `temp_store=MEMORY`

### Session Management
- **`get_session()`**: Standard session with automatic transaction handling
- **`get_isolated_session()`**: Optimized session for operations needing better isolation
- **`transaction()`**: Nested transaction context using savepoints
- **Auto-configuration**: `expire_on_commit=False`, `autoflush=False` for SQLite compatibility

### Connection URL Format
```
sqlite+aiosqlite:///data/db/narada.db?journal_mode=WAL&synchronous=NORMAL&foreign_keys=ON&busy_timeout=30000
```

### Session Factory Configuration
- Async sessions with SQLAlchemy 2.0 patterns
- Proper async context management with automatic cleanup
- Engine event listeners ensure SQLite pragmas are applied consistently

## Migration Strategy

Database migrations are handled through Alembic with the following approach:

1. **Schema Definition**: SQLAlchemy models define the target schema
2. **Migration Generation**: Alembic auto-generates migration scripts
3. **Review Process**: All migrations reviewed before application
4. **Version Control**: Migration files tracked in git
5. **Rollback Support**: All migrations support rollback operations

## Performance Considerations

### Query Optimization
- Composite indexes for common query patterns
- Selective loading with `selectinload()` for relationships
- Proper join strategies for related data
- No soft delete filtering overhead

### SQLite-Specific Optimizations
- WAL mode for concurrent read access
- NullPool for proper connection lifecycle
- Busy timeout handling for lock conflicts
- Pragma optimization via engine event listeners

### Bulk Operations
- Bulk insert patterns for large datasets
- Batch processing for API imports with `import_batch_id` tracking
- Efficient upsert strategies for sync operations
- SQLAlchemy 2.0 async patterns throughout

### Caching Strategy
- Application-level caching for frequently accessed data
- `expire_on_commit=False` for session efficiency
- Query result caching where appropriate

## Development Workflow

### Adding New Tables
1. Create SQLAlchemy model inheriting from `NaradaDBBase`
2. Add relationships to existing models
3. Generate migration with `alembic revision --autogenerate`
4. Review and test migration
5. Update repository interfaces as needed

### Modifying Existing Tables
1. Update SQLAlchemy model
2. Generate migration with `alembic revision --autogenerate`
3. Test migration and rollback
4. Update affected repository methods
5. Update tests to reflect changes

### Data Integrity
- Use database transactions for multi-table operations
- Implement proper foreign key constraints
- Regular data validation and cleanup procedures

## Related Documentation

- **[Architecture](README.md)** - System architecture and design decisions
- **[Development](../development.md)** - Developer onboarding and contribution guide
- **[CLI Reference](../guides/cli.md)** - CLI commands that interact with the database
- **[Workflow Guide](../guides/workflows.md)** - Workflow system that operates on database entities
- **[Likes Sync Guide](../guides/likes-sync.md)** - Likes synchronization using database entities