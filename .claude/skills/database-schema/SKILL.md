---
name: database-schema
description: Mixd's PostgreSQL schema semantics — RLS multi-tenancy, cascade/session/engine patterns, migration-only DDL gotchas, constraint conventions, and where table truth lives. Use when writing repository methods, database migrations, queries, or any persistence-layer code.
user-invocable: false
---

# Mixd Database Schema Reference

> **Table/column truth is the code**: `src/infrastructure/persistence/database/models/`, one module per aggregate (`base.py` holds `DatabaseModel`/`BaseEntity`; the package `__init__` is the only import path). Verify counts with `grep -c "^class DB" src/infrastructure/persistence/database/models/*.py` before citing them. This skill carries the *semantics* the code can't show at a glance — RLS, cascades, migration-only DDL, session mechanics.
> Verified 2026-09-17 · migration head `057` · 38 model classes. (If the counts don't match reality, re-verify everything else here too.) Cross-module relationships are deferred annotations (PEP 649) resolved by class name through the shared registry; `tests/integration/test_schema_gates.py` (`slow`) fails the build if a module drops out of the registry or the ORM drifts from the migrations.

PostgreSQL 17 via psycopg3 — Neon serverless in prod, testcontainers `postgres:17-alpine` in tests. Tables inherit `BaseEntity` → `id` (UUID PK), `created_at`, `updated_at`; exceptions: `workflow_run_nodes` (id only), `oauth_states` (id + created_at only).

## Table inventory (38, by model module)

| Module | Tables |
|--------|--------|
| `track.py` | tracks, connector_tracks, track_metrics, track_likes, track_preferences, track_preference_events, track_tags, track_tag_events |
| `mapping.py` | track_mappings, match_reviews |
| `resolution.py` | resolution_events, resolution_negatives |
| `play.py` | track_plays, connector_plays, play_sources |
| `playlist.py` | playlists, connector_playlists, playlist_mappings, playlist_tracks, playlist_sync_bases, playlist_assignments, playlist_assignment_members |
| `workflow.py` | workflows, workflow_versions, workflow_runs, workflow_run_nodes |
| `schedule.py` / `operation_run.py` | schedules; operation_runs |
| `auth.py` | oauth_tokens, oauth_states, oauth_clients, oauth_authorization_requests, oauth_authorization_codes, oauth_refresh_tokens |
| `user_settings.py` / `sync.py` / `chat.py` | user_settings; sync_checkpoints; chat_feedback, pending_actions |

## Semantic gotchas by table (constraints the code shows but you'll miss)

- **tracks** — per-user uniques on `(user_id, spotify_id)`, `(user_id, isrc)`, `(user_id, mbid)`; normalized/stripped columns are pre-computed for fuzzy matching. pg_trgm GIN + JSONB GIN indexes exist **only via migration `002_pg_opt`** — absent in test DBs (see Environments). Play aggregates `play_count`/`first_played_at`/`last_played_at` (052, v0.10.4) are written ONLY by `recompute_track_play_aggregates` (projection, track merge, rebuild) — never by `save_track`/`to_db`, whose full-column write would clobber them. Every library sort has a `(user_id, key, id)` covering index (053, v0.10.4.1); nullable sort columns carry **two** — `(col IS NULL) ASC` and `(col IS NOT NULL) DESC` lead the key, because NULLS LAST is Postgres's ASC default and the opposite of its DESC default, and because the leading boolean is what keeps the keyset row-comparison seekable. Changing a sort's ORDER BY without its index is a silent full scan.
- **Writing as `default` on a remote database raises** (v0.10.4.1) — `set_rls_user_on_begin` refuses to open a transaction when the contextvar user is `DEFAULT_USER_ID` and `DATABASE_URL` is not localhost, because that combination is always an accident (prod carried three phantom tenants from it, purged 2026-09-06). Cross-tenant maintenance declares `system_context()`; `execute_use_case(user_id=None)` does so automatically. The guard sees the *declared* user; the other half closed in v0.12.0.2 (migration 055): no `user_id` column carries a default any more, and the domain entities have no `user_id` field default either, so an INSERT that **omits** the column fails with `NotNullViolation` instead of landing in a phantom tenant. `DEFAULT_USER_ID` survives only at the interface edge (CLI helpers, API deps, the `user_context` contextvar) as a local-dev convenience.
- **Tenancy is not "has a `user_id` column"** — `playlist_tracks`, `workflow_runs`, `workflow_versions`, `workflow_run_nodes` derive theirs through a CASCADE parent. Any query, guard, or cleanup that enumerates tenant data by column membership silently skips them.
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
