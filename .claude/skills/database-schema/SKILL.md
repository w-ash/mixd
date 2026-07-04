---
name: database-schema
description: Mixd's PostgreSQL schema semantics — RLS multi-tenancy, cascade/session/engine patterns, migration-only DDL gotchas, constraint conventions, and where table truth lives. Use when writing repository methods, database migrations, queries, or any persistence-layer code.
user-invocable: false
---

# Mixd Database Schema Reference

> **Table/column truth is the code**: `src/infrastructure/persistence/database/db_models.py`. Verify counts with `grep -c "^class DB" db_models.py` before citing them. This skill carries the *semantics* the code can't show at a glance — RLS, cascades, migration-only DDL, session mechanics.
> Verified 2026-07-03 · migration head `033` · 29 model classes. (The previous revision said 28 — `playlist_sync_bases` had been added without updating this file. If the counts above don't match reality, re-verify everything else here too.)

PostgreSQL 17 via psycopg3 — Neon serverless in prod, testcontainers `postgres:17-alpine` in tests. Tables inherit `BaseEntity` → `id` (UUID PK), `created_at`, `updated_at`; exceptions: `workflow_run_nodes` (id only), `oauth_states` (id + created_at only).

## Table inventory (29, grouped)

| Group | Tables |
|-------|--------|
| Track core | tracks, connector_tracks, track_mappings, match_reviews, track_metrics, track_likes, track_plays, connector_plays |
| Playlists | playlists, connector_playlists, playlist_mappings, playlist_tracks, playlist_sync_bases, playlist_assignments, playlist_assignment_members |
| Preferences & tags | track_preferences, track_preference_events, track_tags, track_tag_events |
| Workflows & ops | workflows, workflow_versions, workflow_runs, workflow_run_nodes, schedules, operation_runs |
| Auth & state | oauth_tokens, oauth_states, user_settings, sync_checkpoints |

## Semantic gotchas by table (constraints the code shows but you'll miss)

- **tracks** — per-user uniques on `(user_id, spotify_id)`, `(user_id, isrc)`, `(user_id, mbid)`; normalized/stripped columns are pre-computed for fuzzy matching. pg_trgm GIN + JSONB GIN indexes exist **only via migration `002_pg_opt`** — absent in test DBs (see Environments).
- **connector_tracks / connector_playlists** — shared cache, **no `user_id`, no RLS**. Uniques on `(connector_name, connector_*_identifier)`.
- **track_mappings** — unique `(user_id, connector_track_id, connector_name)`; **partial unique** `(user_id, track_id, connector_name) WHERE is_primary = TRUE`.
- **track_plays** — immutable events; `source_services` is native `ARRAY(VARCHAR)` (cross-source dedup); unique `(user_id, track_id, service, played_at, ms_played)`; BRIN on `played_at` via `002_pg_opt` only.
- **connector_plays** — raw plays pre-resolution; `resolved_track_id` nullable FK; `(connector_name, resolved_track_id)` index finds unresolved plays.
- **playlist_tracks** — one row = one membership *instance* (duplicates allowed); reorders update the lexicographic `sort_key` VARCHAR(32), preserving row id/`added_at`. No `user_id` (parent-scoped).
- **playlist_sync_bases** — per-link reconciliation base (connector snapshot at last sync) for divergence detection (v0.8.7 engine); **unique `link_id`** FK→playlist_mappings CASCADE; RLS via migration 030.
- **playlist_assignments** — bound to a **connector_playlist**, not the canonical playlist; members are snapshot rows (DELETE+INSERT per apply) with `user_id` denormalized for RLS.
- **`*_events` tables** (preference/tag) — append-only logs, never updated or deleted.
- **workflows** — `user_id` nullable: NULL = shared with all users (migration 013; the RLS policy allows it).
- **workflow_runs** — **partial unique `(workflow_id) WHERE status IN ('pending','running')`** is the DB-level concurrency guard, surfaced as 409; `operation_id` unique = SSE registry key; `triggered_by_schedule_id` is `ON DELETE SET NULL` (also on operation_runs).
- **schedules** — workflow XOR sync target enforced by a CHECK that lives **in migration 025, not `__table_args__`** (house convention: CHECKs go in migrations); `next_run_at` is the precomputed UTC poll column; **no RLS** (cross-tenant scheduler poll + repository `WHERE user_id`).
- **oauth_states** — transient CSRF state + PKCE verifier, 5-min TTL, consumed atomically; no RLS (unguessable token is the access control).

## Relationship map

```
Track ──→ TrackMappings ──→ ConnectorTracks            (many-to-many)
Track ──→ Metrics / Likes / Plays / Preferences / Tags (one-to-many)
Track ──→ PlaylistTracks ──→ Playlists                 (many-to-many)
ConnectorPlay ──→ Track (resolved_track_id, nullable)
Playlist ──→ PlaylistMappings ──→ ConnectorPlaylists   (+ SyncBase per mapping)
ConnectorPlaylist ──→ Assignments ──→ AssignmentMembers ──→ Track
Workflow ──→ Versions / Runs ──→ RunNodes
Schedule ──→ Workflow (CASCADE); Run tables ──→ Schedule (SET NULL)
```

## UUID primary keys

`DatabaseModel.id` = `postgresql.UUID(as_uuid=True)` with Python-side `uuid.uuid7()` (time-ordered). Migration 008 converted 19 tables from SERIAL; pre-existing rows were backfilled with `gen_random_uuid()` (v4) — don't assume all historical ids sort by time.

## Row-Level Security (multi-tenancy)

- Policy per protected table: `CREATE POLICY user_isolation ... FOR ALL USING (user_id = current_setting('app.user_id', TRUE))` with `ENABLE` + `FORCE` (owner role doesn't bypass — migration 011). Defense-in-depth alongside repository `WHERE user_id` filters.
- **21 RLS tables**; **8 without RLS**: connector_tracks, connector_playlists (shared cache), playlist_tracks (parent-scoped), oauth_states, workflow_versions, workflow_runs, workflow_run_nodes, schedules. The `workflows` policy additionally allows `user_id IS NULL` (shared rows). Verify against migrations before relying on the census (`git grep "CREATE POLICY" alembic/`).
- Mechanics (`user_context.py` + `db_connection.py`): `user_context(user_id)` sets an async-safe ContextVar (default `DEFAULT_USER_ID`); an `after_begin` session event runs `SELECT set_config('app.user_id', :uid, true)` on the **connection** per top-level transaction — transaction-scoped, so safe with Neon PgBouncer; savepoints inherit it.

## Cascade & session patterns

- FKs: `ON DELETE CASCADE` + `passive_deletes=True`; owned collections add `cascade="all, delete-orphan"`. Exception: `triggered_by_schedule_id` → SET NULL.
- Hard deletes only — no soft-delete filtering anywhere.
- Always `selectinload()`; several relationships are `lazy="raise_on_sql"` (lazy access raises instead of N+1). `model.loaded_list(Attr, Type)` / `loaded_one()` read eager-loaded relationships with zero I/O, returning `[]`/`None` if not loaded.
- Sessions: `async_sessionmaker(expire_on_commit=False, autoflush=False, autocommit=False)`; per-task sessions from the pool, never shared — MVCC handles concurrency.
- Engine: `create_async_engine` (psycopg3), `pool_size=5, max_overflow=10, pool_timeout=60, pool_recycle=3600, pool_pre_ping=True`. `statement_timeout=30s` / `lock_timeout=10s` via connect-event `SET` — Neon's PgBouncer rejects startup parameters.
- JSONB serializer: orjson via `set_json_dumps` — raw UUID/datetime serialize natively at flush; naive datetimes raise.

## Environments

- **Prod**: Neon serverless (PgBouncer pooler endpoint, scale-to-zero — `pool_pre_ping` handles wake). Local dev: Docker Compose.
- **Tests**: testcontainers `postgres:17-alpine`, one container per pytest-xdist worker; schema via `metadata.create_all()`, **bypassing the Alembic chain** — migration-only DDL (pg_trgm GIN, BRIN, CHECK constraints) is absent in test DBs. Per-test isolation via savepoint rollback (`db_session` fixture).

## Conventions

- `Mapped[JsonDict]` auto-maps to `postgresql.JSONB` via `type_annotation_map`.
- No native PG ENUMs: status/state columns are short VARCHARs with allowed values in comments; renaming a value requires a row `UPDATE` in the migration.
- Constraint names from `MetaData(naming_convention=...)`: `ix_`/`uq_`/`ck_`/`fk_`/`pk_`.
- Timestamps: `DateTime(timezone=True)` with `datetime.now(UTC)` defaults.

## Repository pattern

Domain defines Protocol interfaces (`src/domain/repositories/`), Infrastructure implements (`src/infrastructure/persistence/repositories/`), Application injects via constructor and owns transactions through UnitOfWork. Batch-first: `save_batch()`, `get_by_ids()`, `delete_batch()`.
