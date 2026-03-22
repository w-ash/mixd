# Mixd Database Design

## Overview

Mixd's database design follows a focused schema pattern that prioritizes essential storage needs while maintaining flexibility for future expansion. Each entity maps to a core domain concept while avoiding unnecessary normalization that would increase query complexity.

The database uses PostgreSQL with SQLAlchemy 2.0 async patterns via psycopg3 (psycopg[binary]). Local development uses Docker Compose (`docker compose up -d`); deployment targets Neon's managed PostgreSQL.

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

### 4. JSONB for Complex Data
Artists and raw metadata stored as JSONB to:
- Avoid complex joins while supporting nested data structures
- Preserve complete information from external services
- Enable GIN-indexed containment queries
- 25% smaller on disk than JSON, with faster reads

### 5. Temporal Design
- Time-series metrics with explicit collection timestamps
- Event-based play records with chronological indexing (BRIN)
- Sync checkpoints for incremental processing

## PostgreSQL-Native Features

### JSONB Everywhere
All JSON columns use PostgreSQL JSONB (not JSON). JSONB is pre-parsed binary — it's smaller, faster for reads, and supports GIN indexing. The `artists` column on `tracks` has a `jsonb_path_ops` GIN index for containment queries.

### ARRAY Columns
`track_plays.source_services` uses native `ARRAY(VARCHAR)` instead of JSON for lists of service names. This enables `@>` containment operators and native array functions.

### pg_trgm Trigram Search
The `pg_trgm` extension provides GIN-accelerated `ILIKE` searches. Indexes on `tracks.title`, `tracks.album`, and `tracks.artists_text` enable substring matching without full table scans. Users can type "deadm" to find "deadmau5" — trigrams support partial matching that `tsvector` cannot.

### BRIN Indexes
`track_plays.played_at` uses a BRIN (Block Range Index) — 99% smaller than B-tree for naturally-ordered time-series data. Ideal for append-only play history.

### Database-Side Aggregations
Play statistics use PostgreSQL `GROUP BY` with `COUNT`, `MIN`, `MAX` — not in-memory Python aggregation. For 100k plays across 1k tracks, the database returns 1k rows instead of loading 100k ORM objects.

### Tuple IN Queries
Multi-column deduplication lookups use `tuple_(col1, col2, ...).in_(keys)` — a single query replaces the SQLite-era batched OR conditions that were limited by expression tree depth.

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
    id SERIAL PRIMARY KEY,
    title VARCHAR NOT NULL,
    artists JSONB NOT NULL,            -- {"names": ["Artist1", "Artist2"]}
    album VARCHAR,
    duration_ms INTEGER,
    release_date TIMESTAMPTZ,
    spotify_id VARCHAR,                -- Indexed for fast lookup
    isrc VARCHAR(32),                  -- Indexed for entity resolution
    mbid VARCHAR(36),                  -- Indexed for MusicBrainz lookup
    title_normalized VARCHAR,          -- Pre-computed for fuzzy matching
    artist_normalized VARCHAR,         -- Pre-computed for fuzzy matching
    title_stripped VARCHAR,            -- Parentheticals removed for matching
    artists_text VARCHAR,              -- Denormalized "Artist1, Artist2" for search/sort
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- B-tree indexes
CREATE INDEX ix_tracks_spotify_id ON tracks(spotify_id);
CREATE INDEX ix_tracks_isrc ON tracks(isrc);
CREATE INDEX ix_tracks_mbid ON tracks(mbid);
CREATE INDEX ix_tracks_title ON tracks(title);
CREATE INDEX ix_tracks_normalized_lookup ON tracks(title_normalized, artist_normalized);
CREATE INDEX ix_tracks_stripped_lookup ON tracks(title_stripped, artist_normalized);

-- GIN trigram indexes (pg_trgm)
CREATE INDEX ix_tracks_title_trgm ON tracks USING gin (title gin_trgm_ops);
CREATE INDEX ix_tracks_album_trgm ON tracks USING gin (album gin_trgm_ops);
CREATE INDEX ix_tracks_artists_text_trgm ON tracks USING gin (artists_text gin_trgm_ops);

-- GIN JSONB index
CREATE INDEX ix_tracks_artists_gin ON tracks USING gin (artists jsonb_path_ops);
```

**Key Points:**
- Primary source of truth for track information
- JSONB artist storage with GIN index for containment queries
- Trigram indexes on text columns for fast substring search
- Direct storage for common identifiers (spotify_id, isrc, mbid)

### track_plays
Immutable record of track play events from service imports.

```sql
CREATE TABLE track_plays (
    id SERIAL PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    service VARCHAR(32) NOT NULL,
    played_at TIMESTAMPTZ NOT NULL,
    ms_played INTEGER,
    context JSONB,
    source_services VARCHAR[],          -- Native PostgreSQL ARRAY
    import_timestamp TIMESTAMPTZ,
    import_source VARCHAR(32),
    import_batch_id VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(track_id, service, played_at, ms_played)
);

-- B-tree indexes
CREATE INDEX ix_track_plays_service ON track_plays(service);
CREATE INDEX ix_track_plays_played_at ON track_plays(played_at);
CREATE INDEX ix_track_plays_track_id ON track_plays(track_id);
CREATE INDEX ix_track_plays_track_played ON track_plays(track_id, played_at);
CREATE INDEX ix_track_plays_track_service ON track_plays(track_id, service);

-- BRIN index for time-series data
CREATE INDEX ix_track_plays_played_at_brin ON track_plays USING brin (played_at);
```

**Key Points:**
- BRIN index for efficient time-range queries on append-only data
- Native ARRAY for source_services (cross-source deduplication)
- JSONB context for platform-specific metadata

## Repository Pattern Implementation

**Complete separation between business logic and database implementation:**

```
Domain:        TrackRepositoryProtocol (interface - never changes)
                         ↑
Infrastructure: SQLAlchemyTrackRepository (current implementation)
```

**How it works:**
- **Domain defines contracts** - `TrackRepositoryProtocol` in `src/domain/repositories/interfaces.py`
- **Infrastructure implements** - `SQLAlchemyTrackRepository` in `src/infrastructure/persistence/repositories/`
- **Application uses contracts** - `def __init__(self, track_repo: TrackRepositoryProtocol)`
- **UnitOfWork coordinates** - `async with uow:` manages transactions, `await uow.commit()` saves changes

**Key files for database work:**
- `src/domain/repositories/interfaces.py` - Abstract protocols (never change)
- `src/infrastructure/persistence/repositories/base_repo.py` - Generic base
- `src/infrastructure/persistence/repositories/track/core.py` - Track operations
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

| Table | Index | Type | Purpose |
|-------|-------|------|---------|
| `tracks` | `spotify_id`, `isrc`, `mbid` | B-tree | Fast identifier lookup |
| `tracks` | `title`, `album`, `artists_text` | GIN (trgm) | Substring search |
| `tracks` | `artists` | GIN (jsonb) | JSONB containment queries |
| `tracks` | `(title_normalized, artist_normalized)` | B-tree | Fuzzy matching |
| `track_plays` | `played_at` | BRIN | Time-range queries |
| `track_plays` | `(track_id, played_at)` | B-tree | Per-track time filtering |
| `track_plays` | `track_id` | B-tree | Per-track aggregation |
| `workflow_runs` | `status` | B-tree | Status filtering |
| `playlist_mappings` | `sync_status` | B-tree | Sync operations |

## Database Session Management

### PostgreSQL Connection Configuration
- **Driver**: `psycopg[binary]` (psycopg3) via `postgresql+psycopg://`
- **Pool**: `AsyncAdaptedQueuePool` (default) for connection reuse
- **Session**: `expire_on_commit=False`, `autoflush=False`
- **Local dev**: `docker compose up -d` starts PostgreSQL on port 5432

### Connection URL Format
```
postgresql+psycopg://mixd:mixd@localhost:5432/mixd
```

### Session Factory Configuration
- Async sessions with SQLAlchemy 2.0 patterns
- Proper async context management with automatic cleanup
- `selectinload()` for all relationship loading (never lazy load)

### Hosted Database (Neon)

Production deployment uses [Neon](https://neon.tech) managed PostgreSQL:

- **Project**: `us-west-2`, PostgreSQL 17, free tier (0.5GB storage, 100 compute hours/month)
- **Connection**: Pooler endpoint (built-in PgBouncer) with `sslmode=require`
- **Scale-to-zero**: Compute suspends after idle timeout; `pool_pre_ping=True` handles reconnection transparently
- **Timeouts**: `statement_timeout` and `lock_timeout` set via `pool_events` (post-connect `SET` commands), not `connect_args` startup parameters — Neon's PgBouncer rejects startup parameters
- **URL normalization**: `_normalize_database_url()` in `settings.py` converts `postgresql://` (Neon/Fly.io format) to `postgresql+psycopg://` (SQLAlchemy format)
- **Backups**: Automatic point-in-time recovery on all Neon plans
- **Cold start**: ~500ms for Neon compute wake + standard connection time. Combined with Fly.io machine wake (~1s) and Python startup (~5s), total cold start is ~7.5s

## Migration Strategy

Database migrations are handled through Alembic with the following approach:

1. **Schema Definition**: SQLAlchemy models define the target schema
2. **Migration Generation**: Alembic auto-generates migration scripts
3. **Review Process**: All migrations reviewed before application
4. **Version Control**: Migration files tracked in git
5. **Rollback Support**: All migrations support rollback operations

### Current Migrations
- `001_initial` — Fresh PostgreSQL schema (replaces SQLite migration chain)
- `002_pg_opt` — PostgreSQL optimization: JSONB, ARRAY, pg_trgm, BRIN, indexes

## Performance Considerations

### Query Optimization
- Database-side `GROUP BY` for aggregations (not in-memory Python)
- `tuple_.in_()` for multi-column lookups (no batching needed)
- Window functions (`ROW_NUMBER`, `DISTINCT ON`) for per-group queries
- Composite indexes for common query patterns
- Selective loading with `selectinload()` for relationships
- No soft delete filtering overhead

### PostgreSQL-Specific Optimizations
- JSONB with GIN indexes for structured data queries
- pg_trgm trigram indexes for `ILIKE` substring search
- BRIN indexes for time-series columns (99% smaller than B-tree)
- Native ARRAY columns where appropriate
- No VARCHAR(N) length limits on high-traffic text columns (TEXT = VARCHAR performance)

### Bulk Operations
- Bulk insert patterns for large datasets
- Batch processing for API imports with `import_batch_id` tracking
- `ON CONFLICT` upsert via `sqlalchemy.dialects.postgresql.insert`
- SQLAlchemy 2.0 async patterns throughout

## Development Workflow

### Adding New Tables
1. Create SQLAlchemy model inheriting from `BaseEntity`
2. Add relationships to existing models
3. Generate migration with `uv run alembic revision --autogenerate -m "description"`
4. Review and test migration
5. Update repository interfaces as needed

### Modifying Existing Tables
1. Update SQLAlchemy model
2. Generate migration with `uv run alembic revision --autogenerate -m "description"`
3. Test migration and rollback
4. Update affected repository methods
5. Update tests to reflect changes

### Data Integrity
- Use database transactions for multi-table operations
- Implement proper foreign key constraints
- Regular data validation and cleanup procedures

## PostgreSQL-Specific Patterns

### Data-Modifying CTE Chains

Track merge operations (`move_references_to_track`, `merge_mappings_to_track`, `merge_metrics_to_track` in `track/core.py`) use multi-step CTE chains for atomic operations. PostgreSQL CTE snapshot semantics apply: **each CTE sees the table state as of the statement start**, not modifications made by earlier CTEs in the same chain.

This means a DELETE CTE and an UPDATE CTE can both "see" the same row. To prevent double-operations:
```sql
-- Guard pattern: exclude rows already handled by a prior CTE
DELETE FROM track_likes WHERE track_id = :loser_id
  AND id NOT IN (SELECT loser_id FROM conflict_likes)
```

The `AND id NOT IN (SELECT ... FROM read_only_cte)` pattern ensures each row is handled by exactly one CTE branch.

### Keyset Pagination Indexes

Composite indexes from migration `003_keyset_idx` support O(1) keyset page seeks:

| Index | Columns | Used By |
|-------|---------|---------|
| `ix_tracks_title_id` | `(title, id)` | `sort_by=title_asc/desc` |
| `ix_tracks_created_at_id` | `(created_at, id)` | `sort_by=added_asc/desc` |
| `ix_tracks_artists_text_id` | `(artists_text, id)` | `sort_by=artist_asc/desc` |

`duration_ms` sort intentionally lacks a composite index (rare sort option — falls back to sequential scan which is fast enough for 15k rows).

Verify with: `EXPLAIN ANALYZE SELECT * FROM tracks WHERE (title, id) > ('value', 123) ORDER BY title ASC, id ASC LIMIT 50`

## Related Documentation

- **[Architecture](README.md)** - System architecture and design decisions
- **[Development](../development.md)** - Developer onboarding and contribution guide
- **[CLI Reference](../guides/cli.md)** - CLI commands that interact with the database
- **[Workflow Guide](../guides/workflows.md)** - Workflow system that operates on database entities
- **[Likes Sync Guide](../guides/likes-sync.md)** - Likes synchronization using database entities
