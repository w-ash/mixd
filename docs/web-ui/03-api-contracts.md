# API Contracts

> Endpoint reference for the Narada REST API.
> Each endpoint notes which existing use case backs it and its implementation status.
> Implemented endpoints have concrete schemas; future endpoints have stub schemas.

---

## Conventions

### Base URL

All endpoints prefixed with `/api/v1`.

### Response Envelope

All list endpoints return a consistent envelope:

```json
{
  "data": [...],
  "total": 1234,
  "limit": 50,
  "offset": 0
}
```

Single-resource endpoints return the object directly (no `data` wrapper).

### Error Response

All errors follow a consistent format:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Playlist 42 not found",
    "details": null
  }
}
```

Exception-to-HTTP mapping is handled by `src/interface/api/middleware.py`:

| Exception | HTTP Status | Error Code | Notes |
|-----------|-------------|------------|-------|
| `NotFoundError` | `404` | `NOT_FOUND` | Domain exception from `src/domain/exceptions.py` |
| `ValueError` | `400` | `VALIDATION_ERROR` | Input validation failures |
| `RequestValidationError` | `422` | (FastAPI default) | Pydantic schema violations (automatic) |
| Unhandled `Exception` | `500` | `INTERNAL_ERROR` | Generic message, details logged server-side |

Implemented status codes:

| HTTP Status | Meaning | Since |
|-------------|---------|-------|
| `413` | Payload too large (file upload exceeds 100 MB limit) | v0.3.1 |
| `429` | Too many concurrent operations (max 3 simultaneous imports) | v0.3.1 |

Future status codes (not yet implemented):

| HTTP Status | Meaning | Milestone |
|-------------|---------|-----------|
| `409` | Conflict (operation already running, duplicate resource) | v0.3.2+ |
| `503` | Service unavailable (connector not connected) | v0.4.0 |

### Pagination

All list endpoints support `limit` (default 50, max 200) and `offset` pagination.

### IDs

All entity IDs are **integers** (matching the codebase -- `Track.id: int`, `Playlist.id: int`). Not UUIDs.

Operation IDs are **UUID strings** (from `ProgressOperation.operation_id`).

---

## Shared Architecture

> CLI and Web UI call the **same use cases** through the **same runner**. No business logic lives in either interface layer.

### Code Path Convergence

Every API endpoint is a thin FastAPI route handler that calls `execute_use_case()` from `application/runner.py` — the same function the CLI invokes via `run_async()`. FastAPI calls it directly (already async); the CLI bridges sync Typer to async through `run_async()`. Route handlers contain zero business logic: parse request → build Command → `execute_use_case()` → serialize Result.

```
CLI (Typer)                          Web UI (FastAPI)
    │                                      │
    ▼                                      ▼
run_async()                          async route handler
    │                                      │
    ▼                                      ▼
execute_use_case(λ uow →             execute_use_case(λ uow →
  UseCase(uow).execute(cmd))           UseCase(uow).execute(cmd))
    │                                      │
    └──────────────┬───────────────────────┘
                   ▼
         Application Layer
         (Use Cases, Services)
                   │
                   ▼
         Domain Layer (pure logic)
                   │
                   ▼
         Infrastructure Layer
         (repos, connectors)
```

### Progress Tracking

Both interfaces share the same progress infrastructure:

- **Domain protocols** (`domain/entities/progress.py`): `ProgressEmitter` and `ProgressSubscriber` define the interface
- **Application hub** (`application/services/progress_manager.py`): `AsyncProgressManager` implements `ProgressEmitter` and fans out to N subscribers
- **CLI subscriber**: `RichProgressProvider` → Rich progress bars in terminal
- **Web subscriber**: `SSEProgressProvider` (new, v0.3.1) → Server-Sent Events to browser

Same use case code emits the same `ProgressEvent` objects — each interface just renders them differently.

### What This Means for Implementation

- Route handlers are ~5-10 lines each
- No new "web-specific" business logic — if the CLI can do it, the API can do it
- Each endpoint table below shows its backing use case — that's the actual code that runs

---

## 0. Health

```
GET    /health
       → { status: "ok", version: "0.3.0" }
```
- **Use case**: None (static response)
- **Status**: ✅ Implemented (v0.3.0)

---

## 1. Tracks

The Track API object is an **assembled view** composed from:
- `Track` entity (title, artists, album, duration_ms, release_date, isrc)
- `track_mappings` (connector linkage with confidence)
- `track_likes` (per-service like status)
- `track_metrics` (collected metrics like play counts, popularity)

```
GET    /tracks
       ?q=<search>                    free-text search on title + artist + album
       ?connector=<name>              filter by connector mapping
       ?liked=<true|false>            filter by like status
       ?liked_on=<connector>          filter likes by specific service
       ?unmapped_for=<connector>      tracks missing mapping to this connector
       ?sort=<field>                  title, artist, album, release_date, duration
       ?order=<asc|desc>              sort direction (default: asc)
       ?limit=&offset=
       → { data: Track[], total, limit, offset }
```
- **Use case**: `ListTracksUseCase` (v0.3.2), `SearchTracksUseCase` (v0.3.2)
- **Status**: Needs implementation

```
GET    /tracks/{id}
       → Track (assembled view with mappings, likes, metrics, play summary)
```
- **Use case**: `GetTrackDetailsUseCase` (v0.3.2)
- **Status**: Needs implementation

```
GET    /tracks/{id}/mappings
       → { data: ConnectorTrackMapping[] }
```
- **Use case**: `GetTrackConnectorMappingsUseCase` (v0.3.2)
- **Status**: Needs implementation

```
PATCH  /tracks/{id}/mappings/{mapping_id}
       body: { connector_track_id: str }
       → ConnectorTrackMapping
```
- **Use case**: Manual mapping correction (new)
- **Status**: Needs implementation

```
DELETE /tracks/{id}/mappings/{mapping_id}
       → 204
```
- **Use case**: Remove mapping (new)
- **Status**: Needs implementation

```
POST   /tracks/{id}/like
       body: { connector: str }
       → TrackLikeStatus
```
- **Use case**: `SyncLikesUseCase` (single-track variant)
- **Status**: Needs implementation

```
DELETE /tracks/{id}/like
       body: { connector: str }
       → 204
```
- **Use case**: `SyncLikesUseCase` (single-track variant)
- **Status**: Needs implementation

```
GET    /tracks/{id}/playlists
       → { data: PlaylistSummary[] }
```
- **Use case**: New query (which playlists contain this track)
- **Status**: Needs implementation

```
POST   /tracks/rematch
       body: { track_ids: int[], connector: str }
       → { operation_id: str }
```
- **Use case**: `MatchAndIdentifyTracksUseCase`
- **Status**: Exists (needs API wrapper)

### Track Object Schema (stub)

```json
{
  "id": 42,
  "title": "string",
  "artists": [{ "name": "string" }],
  "album": "string | null",
  "duration_ms": 0,
  "release_date": "YYYY-MM-DD | null",
  "isrc": "string | null",
  "connector_mappings": [
    {
      "id": 1,
      "connector": "spotify",
      "connector_track_id": "string",
      "confidence": 95,
      "match_method": "direct | isrc | mbid | artist_title | manual",
      "is_primary": true
    }
  ],
  "like_status": {
    "spotify": { "is_liked": true, "liked_at": "ISO8601" },
    "lastfm": { "is_liked": false, "liked_at": null }
  },
  "metrics": [
    { "connector": "lastfm", "metric_type": "play_count", "value": 42, "collected_at": "ISO8601" }
  ],
  "play_summary": {
    "total_plays": 42,
    "last_played": "ISO8601 | null",
    "first_played": "ISO8601 | null"
  }
}
```

---

## 2. Playlists

```
GET    /playlists
       ?limit=&offset=
       → { data: PlaylistSummary[], total, limit, offset }
```
- **Use case**: `ListPlaylistsUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
POST   /playlists
       body: { name: str, description?: str }
       → PlaylistDetail (status 201)
```
- **Use case**: `CreateCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
GET    /playlists/{id}
       → PlaylistDetail (with entries)
```
- **Use case**: `ReadCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
PATCH  /playlists/{id}
       body: { name?: str, description?: str }
       → PlaylistDetail
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)
- **Note**: `null` fields are ignored (preserve existing); empty string `""` clears the field

```
DELETE /playlists/{id}
       → 204
```
- **Use case**: `DeleteCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
GET    /playlists/{id}/tracks
       ?limit=&offset=
       → { data: PlaylistEntry[], total, limit, offset }
```
- **Use case**: `ReadCanonicalPlaylistUseCase`
- **Status**: ✅ Implemented (v0.3.0)

```
POST   /playlists/{id}/tracks
       body: { track_ids: int[], position?: int }
       → { data: PlaylistEntry[] }
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Needs API route (use case exists)

```
DELETE /playlists/{id}/tracks/{entry_id}
       → 204
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Needs API route (use case exists)

```
DELETE /playlists/{id}/tracks
       body: { entry_ids: int[] }
       → 204
```
- **Use case**: `UpdateCanonicalPlaylistUseCase` (batch variant)
- **Status**: Needs implementation

```
PATCH  /playlists/{id}/tracks/reorder
       body: { entry_ids: int[] }
       → 204
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Needs API route (use case exists)

```
PATCH  /playlists/{id}/tracks/move
       body: { entry_id: int, new_position: int }
       → 204
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Needs implementation

### Connector Links

```
GET    /playlists/{id}/links
       → { data: PlaylistMapping[] }
```
- **Use case**: Query playlist connector mappings
- **Status**: Needs implementation

```
POST   /playlists/{id}/links
       body: { connector: str, connector_playlist_id: str, sync_direction: "narada" | "connector" | "manual" }
       → PlaylistMapping
```
- **Use case**: `CreateConnectorPlaylistUseCase`
- **Status**: Needs API route (use case exists)

```
PATCH  /playlists/{id}/links/{link_id}
       body: { sync_direction?: str }
       → PlaylistMapping
```
- **Use case**: `UpdateConnectorPlaylistUseCase`
- **Status**: Needs API route (use case exists)

```
DELETE /playlists/{id}/links/{link_id}
       → 204
```
- **Use case**: Unlink connector playlist
- **Status**: Needs implementation

```
POST   /playlists/{id}/links/{link_id}/sync
       body: { direction: "push" | "pull" }
       → { operation_id: str }
```
- **Use case**: `UpdateConnectorPlaylistUseCase`
- **Status**: Needs API route (use case exists)

### Playlist Object Schemas

Defined in `src/interface/api/schemas/playlists.py`.

```json
// PlaylistSummary (list view)
{
  "id": 1,
  "name": "string",
  "description": "string | null",
  "track_count": 42,
  "connector_links": ["spotify"],
  "updated_at": "2026-03-01T12:00:00"
}

// PlaylistDetail (extends PlaylistSummary)
{
  "id": 1,
  "name": "string",
  "description": "string | null",
  "track_count": 42,
  "connector_links": ["spotify"],
  "updated_at": "2026-03-01T12:00:00",
  "entries": [PlaylistEntry]
}

// PlaylistEntry
{
  "position": 0,
  "track": {
    "id": 42,
    "title": "string",
    "artists": [{ "name": "string" }],
    "album": "string | null",
    "duration_ms": 180000
  },
  "added_at": "2026-03-01T12:00:00 | null"
}

// PlaylistMapping (stub -- not yet implemented)
{
  "id": 1,
  "connector": "spotify",
  "connector_playlist_id": "string",
  "connector_playlist_name": "string",
  "sync_direction": "narada | connector | manual",
  "last_synced": "ISO8601 | null"
}
```

---

## 3. Workflows

```
GET    /workflows
       → { data: WorkflowSummary[], total, limit, offset }
```
- **Use case**: List workflows
- **Status**: Needs implementation

```
POST   /workflows
       body: { name: str, definition: object }
       → Workflow
```
- **Use case**: Create workflow
- **Status**: Needs implementation

```
GET    /workflows/{id}
       → Workflow (with full definition)
```
- **Use case**: Get workflow
- **Status**: Needs implementation

```
PATCH  /workflows/{id}
       body: { name?: str, definition?: object }
       → Workflow
```
- **Use case**: Update workflow
- **Status**: Needs implementation

```
DELETE /workflows/{id}
       → 204
```
- **Use case**: Delete workflow
- **Status**: Needs implementation

```
POST   /workflows/{id}/run
       → { operation_id: str }
```
- **Use case**: Execute workflow via Prefect
- **Status**: Needs implementation

```
POST   /workflows/{id}/preview
       → { tracks: TrackSummary[], node_results: NodeExecutionSummary[] }
```
- **Use case**: Dry-run workflow (skip destination writes)
- **Status**: Needs implementation

```
POST   /workflows/validate
       body: { definition: object }
       → { valid: bool, errors: ValidationError[] }
```
- **Use case**: Validate workflow definition
- **Status**: Needs implementation

```
GET    /workflows/{id}/runs
       ?limit=&offset=
       → { data: WorkflowRun[], total, limit, offset }
```
- **Use case**: List workflow runs
- **Status**: Needs implementation

```
GET    /workflows/{id}/runs/{run_id}
       → WorkflowRun (with per-node details)
```
- **Use case**: Get run detail
- **Status**: Needs implementation

### Workflow Object Schemas (stub)

```json
// WorkflowSummary
{
  "id": 1,
  "name": "string",
  "description": "string",
  "last_run": {
    "status": "COMPLETED | FAILED | RUNNING | null",
    "completed_at": "ISO8601 | null",
    "output_track_count": 42
  }
}

// WorkflowRun
{
  "id": "uuid",
  "workflow_id": 1,
  "started_at": "ISO8601",
  "completed_at": "ISO8601 | null",
  "status": "PENDING | RUNNING | COMPLETED | FAILED | CANCELLED",
  "result_summary": {
    "tracks_output": 42,
    "destination_playlist_id": 5
  },
  "node_results": [
    { "node_id": "source", "status": "COMPLETED", "track_count": 120, "duration_ms": 3400 }
  ],
  "error": "string | null"
}
```

---

## 4. Imports

```
POST   /imports/lastfm/history
       body: { mode: "recent" | "incremental" | "full", limit?: int, from_date?: str, to_date?: str }
       → { operation_id: str }
```
- **Use case**: `ImportPlayHistoryUseCase` (via `run_import()`)
- **Status**: ✅ Implemented (v0.3.1)
- **Background**: Launches as background task, returns operation_id immediately for SSE progress subscription

```
POST   /imports/spotify/history
       body: multipart/form-data (single JSON file, max 100 MB)
       → { operation_id: str }
```
- **Use case**: `ImportPlayHistoryUseCase` (file variant via `run_import()`)
- **Status**: ✅ Implemented (v0.3.1)
- **Note**: Returns `413` if file exceeds 100 MB. File is saved to temp location and cleaned up after processing.

```
POST   /imports/spotify/likes
       body: { limit?: int, max_imports?: int }
       → { operation_id: str }
```
- **Use case**: `SyncLikesUseCase` (Spotify import direction via `run_spotify_likes_import()`)
- **Status**: ✅ Implemented (v0.3.1)

```
POST   /imports/lastfm/likes
       body: { batch_size?: int, max_exports?: int }
       → { operation_id: str }
```
- **Use case**: `SyncLikesUseCase` (Last.fm export direction via `run_lastfm_likes_export()`)
- **Status**: ✅ Implemented (v0.3.1)

```
GET    /imports/lastfm/likes/preview
       → { count: int, tracks: TrackSummary[] }
```
- **Use case**: Preview export count
- **Status**: Needs implementation

```
GET    /imports/checkpoints
       → CheckpointStatus[] (flat array, no envelope)
```
- **Use case**: Checkpoint query (via `get_sync_checkpoint_status()`)
- **Status**: ✅ Implemented (v0.3.1)

### Import Object Schemas

Defined in `src/interface/api/schemas/imports.py`.

```json
// OperationStartedResponse
{
  "operation_id": "uuid-string"
}

// CheckpointStatus
{
  "service": "spotify | lastfm",
  "entity_type": "likes | plays",
  "last_sync_timestamp": "ISO8601 | null",
  "has_previous_sync": true
}
```

### Concurrency Limit

A maximum of 3 concurrent import operations are allowed (configurable via `SSEConstants.MAX_CONCURRENT_OPERATIONS`). The limit is checked against *logically active* operations — operations that have finished their use-case work but are still in the 30-second SSE grace period do not count against the limit. Exceeding the limit returns `429` with `Retry-After: 30`.

---

## 5. Stats (Dashboard)

```
GET    /stats/dashboard
       → {
           total_tracks: int,
           total_plays: int,
           total_playlists: int,
           tracks_by_connector: { "spotify": int, "lastfm": int, ... },
           total_liked: int,
           liked_by_connector: { "spotify": int, "lastfm": int, ... }
         }
```
- **Use case**: `GetTrackStatsUseCase` (v0.3.3)
- **Status**: Needs implementation

```
GET    /stats/plays
       ?limit=&offset=
       ?from=<ISO8601>&to=<ISO8601>
       → { data: TrackPlay[], total, limit, offset }
```
- **Use case**: Play history query
- **Status**: Needs implementation

```
GET    /stats/top-tracks
       ?period_days=30
       ?limit=50
       → { data: [{ track: TrackSummary, play_count: int, last_played: ISO8601 }] }
```
- **Use case**: Top tracks aggregation
- **Status**: Needs implementation

---

## 6. Operations (Progress & SSE)

```
GET    /operations/{operation_id}/progress
       Accept: text/event-stream
       → SSE stream of ProgressEvent
```
- **Use case**: `OperationBoundEmitter` + `OperationRegistry` (implements `ProgressEmitter` protocol)
- **Status**: ✅ Implemented (v0.3.1)
- **SSE reconnection**: Supports `Last-Event-ID` header for reconnection
- **Grace period**: SSE queue stays alive 30 seconds after operation completes to allow clients to receive final events

```
GET    /operations
       → { data: OperationSummary[] } (active operations only)
```
- **Use case**: List currently active operations
- **Status**: ✅ Implemented (v0.3.1)

```
GET    /operations/{operation_id}
       → ProgressEvent (snapshot for polling fallback)
```
- **Use case**: Current operation state
- **Status**: Needs implementation

```
POST   /operations/{operation_id}/cancel
       → 204
```
- **Use case**: Cancel running operation
- **Status**: Needs implementation

### SSE Event Format

```
event: started
data: {"total": 5000, "description": "Importing listening history..."}

event: progress
data: {"current": 150, "total": 5000, "message": "Importing plays...", "completion_percentage": 3.0, "items_per_second": 12.5, "eta_seconds": 388}

event: complete
data: {"final_status": "completed"}

event: error
data: {"message": "Import failed: rate limited"}
```

- `event` types: `started`, `progress`, `complete`, `error`
- `started` provides total count and description for initial display
- `progress` events include optional computed metrics (percentage, throughput, ETA)
- Both `complete` and `error` events trigger query invalidation on the frontend

---

## 7. Connectors (Auth & Search)

```
GET    /connectors
       → ConnectorStatus[]
```
- **Use case**: Reads filesystem/environment state directly (no use case — connector-specific logic)
- **Status**: ✅ Implemented (v0.3.0)
- **Note**: Returns a **flat array**, not the standard `{ data: [...] }` envelope. This endpoint reads credential files and environment variables rather than querying the database, so it doesn't go through a use case. Spotify includes silent token refresh — if the cached token is expired but a refresh_token exists, the endpoint refreshes it before responding.

```
GET    /connectors/spotify/auth-url
       → { url: str }
```
- **Use case**: Generate Spotify OAuth URL
- **Status**: Needs implementation

```
GET    /auth/spotify/callback
       ?code=&state=
       → 302 redirect to /settings
```
- **Use case**: Exchange OAuth code for tokens
- **Status**: Needs implementation

```
GET    /connectors/lastfm/auth-url
       → { url: str }
```
- **Use case**: Generate Last.fm auth URL
- **Status**: Needs implementation

```
GET    /auth/lastfm/callback
       ?token=
       → 302 redirect to /settings
```
- **Use case**: Store Last.fm session key
- **Status**: Needs implementation

```
DELETE /connectors/{connector}/auth
       → 204
```
- **Use case**: Disconnect connector
- **Status**: Needs implementation

```
GET    /connectors/{connector}/search
       ?q=<title artist>
       ?limit=10
       → { data: [{ connector_track_id: str, title: str, artists: [], album: str, duration_ms: int, preview_url: str | null }] }
```
- **Use case**: Connector track search (for mapping correction)
- **Status**: Needs implementation

```
GET    /connectors/{connector}/playlists
       ?q=<search>
       ?limit=50&offset=0
       → { data: [{ connector_playlist_id: str, name: str, track_count: int, owner: str }], total, limit, offset }
```
- **Use case**: Browse/search user's playlists on a connector (for linking)
- **Status**: Needs implementation

---

### Connector Status Schema

Defined in `src/interface/api/schemas/connectors.py`.

```json
// ConnectorStatus
{
  "name": "spotify",
  "connected": true,
  "account_name": "display_name | null",
  "token_expires_at": 1709312400
}
```

Fields:
- `name`: connector identifier (`"spotify"`, `"lastfm"`, `"musicbrainz"`, `"apple"`)
- `connected`: whether valid credentials exist
- `account_name`: display name if available (Spotify: fetched via `/me`, Last.fm: from settings, others: null)
- `token_expires_at`: Unix timestamp (Spotify only, null for others)

---

## Use Case Mapping Summary

### Implemented API Routes

| Use Case | API Endpoints | Route File | Since |
|----------|--------------|------------|-------|
| `ListPlaylistsUseCase` | `GET /playlists` | `routes/playlists.py` | v0.3.0 |
| `CreateCanonicalPlaylistUseCase` | `POST /playlists` | `routes/playlists.py` | v0.3.0 |
| `ReadCanonicalPlaylistUseCase` | `GET /playlists/{id}`, `GET /playlists/{id}/tracks` | `routes/playlists.py` | v0.3.0 |
| `UpdateCanonicalPlaylistUseCase` | `PATCH /playlists/{id}` | `routes/playlists.py` | v0.3.0 |
| `DeleteCanonicalPlaylistUseCase` | `DELETE /playlists/{id}` | `routes/playlists.py` | v0.3.0 |
| *(no use case)* | `GET /connectors` | `routes/connectors.py` | v0.3.0 |
| *(no use case)* | `GET /health` | `routes/health.py` | v0.3.0 |
| `ImportPlayHistoryUseCase` | `POST /imports/lastfm/history`, `POST /imports/spotify/history` | `routes/imports.py` | v0.3.1 |
| `SyncLikesUseCase` | `POST /imports/spotify/likes`, `POST /imports/lastfm/likes` | `routes/imports.py` | v0.3.1 |
| Checkpoint query | `GET /imports/checkpoints` | `routes/imports.py` | v0.3.1 |
| SSE progress streaming | `GET /operations/{id}/progress` | `routes/operations.py` | v0.3.1 |
| Active operations list | `GET /operations` | `routes/operations.py` | v0.3.1 |

### Use Cases With Existing Logic (Need API Routes)

| Use Case | API Endpoints | Milestone |
|----------|--------------|-----------|
| `UpdateCanonicalPlaylistUseCase` | `POST /playlists/{id}/tracks`, `DELETE .../tracks`, `PATCH .../reorder` | v0.3.2+ |
| `CreateConnectorPlaylistUseCase` | `POST /playlists/{id}/links` | v0.4.0 |
| `UpdateConnectorPlaylistUseCase` | `PATCH /playlists/{id}/links/{id}`, sync | v0.4.0 |
| `MatchAndIdentifyTracksUseCase` | `POST /tracks/rematch` | v0.3.2 |
| `EnrichTracksUseCase` | Internal (used by workflows) | — |

### Use Cases Needing Implementation

| Use Case | Milestone | API Endpoints |
|----------|-----------|--------------|
| Operation cancellation | v0.3.2+ | `POST /operations/{id}/cancel` |
| `ListTracksUseCase` | v0.3.2 | `GET /tracks` |
| `GetTrackDetailsUseCase` | v0.3.2 | `GET /tracks/{id}` |
| `SearchTracksUseCase` | v0.3.2 | `GET /tracks?q=...` |
| `GetTrackConnectorMappingsUseCase` | v0.3.2 | `GET /tracks/{id}/mappings` |
| `GetTrackStatsUseCase` | v0.3.3 | `GET /stats/dashboard` |
| `GetConnectorMappingStatsUseCase` | v0.3.3 | `GET /stats/dashboard` (partial) |
| `GetMetadataFreshnessUseCase` | v0.3.3 | `GET /stats/dashboard` (partial) |
| Workflow CRUD | v0.4.0 | `GET/POST/PATCH/DELETE /workflows` |
| Workflow execution | v0.4.0 | `POST /workflows/{id}/run` |
| Connector playlist browse | v0.4.0 | `GET /connectors/{connector}/playlists` |
| Connector OAuth flows | v0.5.0 | `/auth/*`, `/connectors/*/auth-url` |
| `GetUnmappedTracksUseCase` | v0.6.0 | `GET /tracks?unmapped_for=...` |
