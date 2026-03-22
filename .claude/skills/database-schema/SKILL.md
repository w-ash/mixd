---
name: database-schema
description: Mixd's 10-table SQLite database schema — tables, columns, relationships, indexes, cascade behavior, and session management patterns. Use when writing repository methods, database migrations, queries, or any persistence-layer code.
user-invocable: false
---

# Mixd Database Schema Reference

> Condensed from `docs/architecture/database.md`. All tables inherit `BaseEntity` → `id`, `created_at`, `updated_at`.

## Tables

### tracks
Central track entities. Source of truth for metadata.

| Column | Type | Notes |
|--------|------|-------|
| title | VARCHAR(255) NOT NULL | Indexed |
| artists | JSON NOT NULL | List of artist names/IDs |
| album | VARCHAR(255) | |
| duration_ms | INTEGER | |
| release_date | DATETIME | |
| spotify_id | VARCHAR(64) | Indexed — fast Spotify lookup |
| isrc | VARCHAR(32) | Indexed — entity resolution |
| mbid | VARCHAR(36) | Indexed — MusicBrainz lookup |

### connector_tracks
Service-specific track representations with full external metadata.

| Column | Type | Notes |
|--------|------|-------|
| connector_name | VARCHAR(32) NOT NULL | Service name (spotify, lastfm) |
| connector_track_identifier | VARCHAR(64) NOT NULL | External service track ID |
| title, artists, album, duration_ms, isrc, release_date | — | Mirrors track fields |
| raw_metadata | JSON NOT NULL | Complete service-specific data |
| last_updated | DATETIME NOT NULL | Last metadata refresh |

**Unique**: `(connector_name, connector_track_identifier)`
**Index**: `(connector_name, isrc)` for ISRC lookups

### track_mappings
Cross-service identity: connects internal tracks to connector tracks.

| Column | Type | Notes |
|--------|------|-------|
| track_id | FK → tracks | CASCADE delete |
| connector_track_id | FK → connector_tracks | CASCADE delete |
| connector_name | VARCHAR(32) NOT NULL | For indexing |
| match_method | VARCHAR(32) NOT NULL | direct, isrc, mbid, artist_title |
| confidence | INTEGER NOT NULL | 0-100 score |
| confidence_evidence | JSON | Evidence for score |
| is_primary | BOOLEAN DEFAULT FALSE | Primary per track-connector pair |

**Unique**: `(connector_track_id, connector_name)` — one canonical mapping per connector track
**Partial unique**: `(track_id, connector_name) WHERE is_primary = TRUE`

### track_metrics
Time-series metrics from connectors (play_count, popularity, etc.).

| Column | Type | Notes |
|--------|------|-------|
| track_id | FK → tracks | CASCADE delete |
| connector_name | VARCHAR(32) NOT NULL | Source service |
| metric_type | VARCHAR(32) NOT NULL | play_count, popularity, etc. |
| value | FLOAT NOT NULL | Numeric value |
| collected_at | DATETIME NOT NULL | Collection timestamp |

**Unique**: `(track_id, connector_name, metric_type)`

### track_likes
Like/favorite status per service.

| Column | Type | Notes |
|--------|------|-------|
| track_id | FK → tracks | CASCADE delete |
| service | VARCHAR(32) NOT NULL | spotify, lastfm, etc. |
| is_liked | BOOLEAN DEFAULT TRUE | Current status |
| liked_at | DATETIME | When liked |
| last_synced | DATETIME | Last sync time |

**Unique**: `(track_id, service)`

### track_plays
Immutable play event records from imports.

| Column | Type | Notes |
|--------|------|-------|
| track_id | FK → tracks | CASCADE delete |
| service | VARCHAR(32) NOT NULL | Source service |
| played_at | DATETIME NOT NULL | Indexed for chronological queries |
| ms_played | INTEGER | Optional duration |
| context | JSON | Play context metadata |
| import_timestamp | DATETIME | When imported |
| import_source | VARCHAR(32) | spotify_export, lastfm_api |
| import_batch_id | VARCHAR(64) | Batch group identifier |

**Unique**: `(track_id, service, played_at, ms_played)` — dedup

### playlists
Connector-agnostic playlist entities.

| Column | Type | Notes |
|--------|------|-------|
| name | VARCHAR(255) NOT NULL | |
| description | VARCHAR(1000) | |
| track_count | INTEGER DEFAULT 0 | Cached count |

### playlist_mappings
Maps playlists to connector playlists.

| Column | Type | Notes |
|--------|------|-------|
| playlist_id | FK → playlists | CASCADE delete |
| connector_name | VARCHAR(32) NOT NULL | |
| connector_playlist_id | FK → connector_playlists | CASCADE delete |
| last_synced | DATETIME | |

**Unique**: `(playlist_id, connector_name)`, `(connector_playlist_id)`

### playlist_tracks
Many-to-many with lexicographic ordering.

| Column | Type | Notes |
|--------|------|-------|
| playlist_id | FK → playlists | CASCADE delete |
| track_id | FK → tracks | CASCADE delete |
| sort_key | VARCHAR(32) NOT NULL | Lexicographic e.g. "a0000000" |
| added_at | DATETIME | |

**Index**: `(playlist_id, sort_key)` for ordered retrieval

### sync_checkpoints
Incremental sync state per user/service/entity.

| Column | Type | Notes |
|--------|------|-------|
| user_id | VARCHAR(64) NOT NULL | |
| service | VARCHAR(32) NOT NULL | |
| entity_type | VARCHAR(32) NOT NULL | likes, plays, etc. |
| last_timestamp | DATETIME | Last successful sync |
| cursor | VARCHAR(1024) | Continuation token |

**Unique**: `(user_id, service, entity_type)`

## Relationship Map

```
Track ──→ TrackMappings ──→ ConnectorTracks   (many-to-many)
Track ──→ TrackMetrics                        (one-to-many)
Track ──→ TrackLikes                          (one-to-many)
Track ──→ TrackPlays                          (one-to-many)
Track ──→ PlaylistTracks ──→ Playlists        (many-to-many)
Playlist ──→ PlaylistMappings                 (one-to-many)
```

## Cascade & Session Patterns

- **All FKs**: `ON DELETE CASCADE` + SQLAlchemy `cascade="all, delete-orphan"`, `passive_deletes=True`
- **Hard deletes only** — no soft delete filtering
- **Always `selectinload()`** for relationships (lazy load on 1000 tracks = 1001 queries)
- **`expire_on_commit=False`** in session config
- **SQLite config**: WAL mode, NullPool, busy_timeout=30s, `synchronous=NORMAL`, `foreign_keys=ON`, `temp_store=MEMORY`

## Repository Pattern

```
Domain:          TrackRepositoryProtocol  (src/domain/repositories/interfaces.py)
                         ↑
Infrastructure:  SQLAlchemyTrackRepository  (src/infrastructure/persistence/repositories/)
```

- Domain defines Protocol interfaces, Infrastructure implements
- Application injects via constructor, uses UnitOfWork for transactions
- Batch-first: `save_batch()`, `get_by_ids()`, `delete_batch()`
