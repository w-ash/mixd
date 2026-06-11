---
name: database-schema
description: Mixd's 28-table PostgreSQL schema — tables, columns, relationships, indexes, cascade behavior, RLS multi-tenancy, and session management patterns. Use when writing repository methods, database migrations, queries, or any persistence-layer code.
user-invocable: false
---

# Mixd Database Schema Reference

> Source of truth: `src/infrastructure/persistence/database/db_models.py` (28 model classes / tables). PostgreSQL 17 via psycopg3 — Neon serverless in prod, testcontainers `postgres:17-alpine` in tests. Tables inherit `BaseEntity` → `id` (UUID PK), `created_at`, `updated_at`. Exceptions: `workflow_run_nodes` (id only), `oauth_states` (id + created_at only).

## Table Inventory (28, grouped)

| Group | Tables |
|-------|--------|
| Track core | tracks, connector_tracks, track_mappings, match_reviews, track_metrics, track_likes, track_plays, connector_plays |
| Playlists | playlists, connector_playlists, playlist_mappings, playlist_tracks, playlist_assignments, playlist_assignment_members |
| Preferences & tags | track_preferences, track_preference_events, track_tags, track_tag_events |
| Workflows & ops | workflows, workflow_versions, workflow_runs, workflow_run_nodes, schedules, operation_runs |
| Auth & state | oauth_tokens, oauth_states, user_settings, sync_checkpoints |

## Key Tables

### tracks
Central track entities; user-scoped (`user_id` VARCHAR, default `'default'`).

| Column | Type | Notes |
|--------|------|-------|
| title, artists | VARCHAR / JSONB NOT NULL | artists = JSONB dict |
| album, duration_ms, release_date, version | — | version INTEGER default 1 |
| spotify_id, isrc, mbid | VARCHAR | each indexed; UNIQUE per user: `(user_id, spotify_id)`, `(user_id, isrc)`, `(user_id, mbid)` |
| title_normalized, artist_normalized, title_stripped, artists_text | VARCHAR | pre-computed for fuzzy matching / search |

**Indexes**: `title`; `(title_normalized, artist_normalized)`; `(title_stripped, artist_normalized)`. pg_trgm GIN (title/album/artists_text) + JSONB GIN (artists) exist only via migration `002_pg_opt` — not in `metadata.create_all()` (so absent in test DBs).

### connector_tracks
Service-specific track cache (no `user_id` — shared across users, no RLS). `raw_metadata` JSONB. **Unique**: `(connector_name, connector_track_identifier)`. **Index**: `(connector_name, isrc)`.

### track_mappings
Cross-service identity: `track_id` FK→tracks, `connector_track_id` FK→connector_tracks (both CASCADE). `match_method`, `confidence` (int), `confidence_evidence` JSONB, `origin` (automatic default), `is_primary`.
**Unique**: `(user_id, connector_track_id, connector_name)`. **Partial unique**: `(user_id, track_id, connector_name) WHERE is_primary = TRUE`. Indexes on track_id, connector_track_id, connector_name.

### match_reviews
Medium-confidence matches staged for human review (`status` default `pending`, `match_weight`, `reviewed_at`). **Unique**: `(user_id, track_id, connector_name, connector_track_id)`.

### track_metrics / track_likes
Metrics: **Unique** `(track_id, connector_name, metric_type)`; value FLOAT, collected_at. Likes: **Unique** `(user_id, track_id, service)`; is_liked, liked_at, last_synced; index `(service, is_liked)`.

### track_plays
Immutable play events. `source_services` is native `ARRAY(VARCHAR)` (cross-source dedup). **Unique**: `(user_id, track_id, service, played_at, ms_played)`. Indexes: service, played_at, import_source, import_batch_id, track_id, `(track_id, played_at)`, `(track_id, service)`. BRIN on played_at via migration `002_pg_opt` only.

### connector_plays
Raw plays pre-resolution; `resolved_track_id` nullable FK→tracks (CASCADE), `resolved_at`, `raw_metadata` JSONB. **Unique**: `(user_id, connector_name, connector_track_identifier, played_at, ms_played)`. Index `(connector_name, resolved_track_id)` finds unresolved plays.

### playlists / playlist_tracks / connector_playlists / playlist_mappings
- **playlists**: name, description, track_count (cached).
- **playlist_tracks**: one row = one membership *instance* (duplicates allowed; reorders update `sort_key`, preserving row id/added_at). `sort_key` VARCHAR(32) lexicographic; index `(playlist_id, sort_key)`. No user_id (scoped via parent).
- **connector_playlists**: shared cache, no user_id/RLS; `items` JSONB list, `raw_metadata` JSONB, `snapshot_id`. **Unique**: `(connector_name, connector_playlist_identifier)`.
- **playlist_mappings**: FKs CASCADE to both sides; sync columns (`sync_direction` push default, `sync_status` never_synced default + index, last_sync_* error/timestamps/counts). **Unique**: `(playlist_id, connector_name)`, `(user_id, connector_playlist_id)`.

### playlist_assignments / playlist_assignment_members
Assignment = one metadata action (`action_type` set_preference|add_tag, `action_value`) bound to a **connector_playlist** (FK CASCADE, not canonical playlist). **Unique**: `(connector_playlist_id, action_type, action_value)`. Members snapshot matched tracks per apply (DELETE+INSERT); `user_id` denormalized for RLS. **Unique**: `(assignment_id, track_id)`.

### track_preferences / track_preference_events, track_tags / track_tag_events
- preferences: one per `(user_id, track_id)` UNIQUE; `state` (hmm/nah/yah/star), `source`, `preferred_at`.
- track_tags: **Unique** `(user_id, track_id, tag)`; `namespace`/`value` derived from tag and indexed. GIN trigram index on tag via migration `c602c5a08631` only.
- `*_events` tables: append-only logs, never updated/deleted; indexed `(user_id, track_id)`.

### workflows / workflow_versions / workflow_runs / workflow_run_nodes
- **workflows**: `user_id` nullable — NULL = shared with all users (migration 013); `definition` JSONB, `definition_version`.
- **workflow_versions**: previous definition snapshot per `(workflow_id, version)` UNIQUE; FK CASCADE.
- **workflow_runs**: `run_number` (per-workflow sequential), `operation_id` (unique, SSE registry key), `status`, `definition_snapshot`/`output_tracks` JSONB, heartbeat_at, `triggered_by_schedule_id` FK→schedules **ON DELETE SET NULL**. **Partial unique**: `(workflow_id) WHERE status IN ('pending','running')` — DB-level concurrency guard, mapped to 409.
- **workflow_run_nodes**: per-node lifecycle (status, durations, track counts, `node_details` JSONB); FK run_id CASCADE.

### schedules
Fires a workflow run XOR background sync: exactly one of `workflow_id` (FK CASCADE) / `sync_target` set — CHECK lives in migration 025 (convention: CHECKs in migrations, never `__table_args__`). Cadence = hour/minute/day_of_week + timezone; `next_run_at` precomputed UTC poll column. Partial uniques: `(user_id, workflow_id)` and `(user_id, sync_target)`. Indexes: user_id, `(status, next_run_at)`, partial started_at. **No RLS** — repository `WHERE user_id` + cross-tenant scheduler poll.

### operation_runs / oauth_tokens / oauth_states / user_settings / sync_checkpoints
- **operation_runs**: audit row per SSE operation; `counts`/`issues` JSONB; `triggered_by_schedule_id` SET NULL; index `(user_id, started_at)`.
- **oauth_tokens**: **Unique** `(user_id, service)`; OAuth2 (access/refresh/expires_at) or session key (Last.fm); `extra_data` JSONB.
- **oauth_states**: transient CSRF state + PKCE verifier, 5-min TTL, consumed atomically; no RLS (unguessable token is the access control).
- **user_settings**: JSONB store, **Unique** `(user_id, key)`.
- **sync_checkpoints**: **Unique** `(user_id, service, entity_type)`; last_timestamp, cursor, remote_total.

## Relationship Map

```
Track ──→ TrackMappings ──→ ConnectorTracks            (many-to-many)
Track ──→ Metrics / Likes / Plays / Preferences / Tags (one-to-many)
Track ──→ PlaylistTracks ──→ Playlists                 (many-to-many)
ConnectorPlay ──→ Track (resolved_track_id, nullable)
Playlist ──→ PlaylistMappings ──→ ConnectorPlaylists
ConnectorPlaylist ──→ Assignments ──→ AssignmentMembers ──→ Track
Workflow ──→ Versions / Runs ──→ RunNodes
Schedule ──→ Workflow (CASCADE); Run tables ──→ Schedule (SET NULL)
```

## UUID Primary Keys

- `DatabaseModel.id` = `postgresql.UUID(as_uuid=True)`, Python-side default `uuid.uuid7()` (time-ordered).
- Migration 008 converted 19 tables from SERIAL integers; pre-existing rows backfilled with `gen_random_uuid()` (v4). Later tables were UUID from birth.

## Row-Level Security (multi-tenancy)

- Policy on each protected table: `CREATE POLICY user_isolation ... FOR ALL USING (user_id = current_setting('app.user_id', TRUE))`, with `ENABLE` + `FORCE ROW LEVEL SECURITY` (owner role does not bypass — migration 011). Defense-in-depth alongside repository `WHERE user_id` filters.
- **20 RLS tables**: tracks, track_mappings, match_reviews, track_likes, track_plays, connector_plays, playlists, workflows, oauth_tokens, user_settings, sync_checkpoints (007/011); track_metrics, playlist_mappings (015); track_preferences, track_preference_events; track_tags, track_tag_events; playlist_assignments, playlist_assignment_members (016, renamed 017); operation_runs (018).
- **workflows** policy additionally allows `user_id IS NULL` (shared rows, migration 013).
- **No RLS (8)**: connector_tracks, connector_playlists (shared cache), playlist_tracks (parent-scoped), oauth_states, workflow_versions, workflow_runs, workflow_run_nodes, schedules.
- Mechanics (`user_context.py` + `db_connection.py`): `user_context(user_id)` sets a ContextVar (async-safe; default `DEFAULT_USER_ID`); an `after_begin` session event runs `SELECT set_config('app.user_id', :uid, true)` on the **connection** per top-level transaction (transaction-scoped — safe with Neon PgBouncer; savepoints inherit).

## Cascade & Session Patterns

- **FKs**: `ON DELETE CASCADE` + `passive_deletes=True`; owned collections add `cascade="all, delete-orphan"`. Exception: `triggered_by_schedule_id` (workflow_runs, operation_runs) is `ON DELETE SET NULL`.
- **Hard deletes only** — no soft delete filtering.
- **Always `selectinload()`**; several relationships are `lazy="raise_on_sql"` (lazy access raises instead of N+1). `model.loaded_list(Attr, Type)` / `loaded_one()` read eager-loaded relationships with zero I/O, returning `[]`/`None` if not loaded.
- **Sessions**: `async_sessionmaker(expire_on_commit=False, autoflush=False, autocommit=False)`; per-task sessions from the pool, never shared — MVCC handles concurrent tasks.
- **Engine**: `create_async_engine` (psycopg3), `pool_size=5, max_overflow=10, pool_timeout=60, pool_recycle=3600, pool_pre_ping=True`. `statement_timeout=30s` / `lock_timeout=10s` via connect-event `SET` (Neon's PgBouncer rejects startup parameters).
- **JSONB serializer**: orjson registered via `set_json_dumps` — raw UUID/datetime values serialize natively at flush; naive datetimes raise.

## Environments

- **Prod**: Neon serverless PostgreSQL (PgBouncer pooler endpoint; scale-to-zero — `pool_pre_ping` handles wake). Local dev: Docker Compose.
- **Tests**: testcontainers `postgres:17-alpine`, one container per pytest-xdist worker; schema via `metadata.create_all()` (`init_db()`), bypassing the Alembic chain — migration-only DDL (pg_trgm GIN, BRIN, CHECK constraints) is absent in test DBs. Per-test isolation via savepoint rollback (`db_session` fixture).

## Conventions

- `Mapped[JsonDict]` auto-maps to `postgresql.JSONB` via `type_annotation_map` — no explicit column type needed.
- No native PG ENUM types: status/state columns are short VARCHARs (`status`, `state`, `sync_direction`, …) with allowed values documented in comments; renaming a value requires a row `UPDATE` in the migration.
- Constraint names from `MetaData(naming_convention=...)`: `ix_`/`uq_`/`ck_`/`fk_`/`pk_` templates.
- Timestamps are `DateTime(timezone=True)` with `datetime.now(UTC)` defaults.

## Repository Pattern

```
Domain:          TrackRepositoryProtocol  (src/domain/repositories/interfaces.py)
                         ↑
Infrastructure:  SQLAlchemyTrackRepository  (src/infrastructure/persistence/repositories/)
```

- Domain defines Protocol interfaces, Infrastructure implements
- Application injects via constructor, uses UnitOfWork for transactions
- Batch-first: `save_batch()`, `get_by_ids()`, `delete_batch()`
