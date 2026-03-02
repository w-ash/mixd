# API Contracts

> Skeletal endpoint reference. Schemas are stubs -- filled during implementation.
> Each endpoint notes which existing use case backs it and whether it needs to be built.

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
    "code": "PLAYLIST_NOT_FOUND",
    "message": "Playlist with ID 42 not found",
    "details": { "playlist_id": 42 }
  }
}
```

| HTTP Status | Meaning |
|-------------|---------|
| `400` | Invalid request (bad input, validation failure) |
| `404` | Resource not found |
| `409` | Conflict (operation already running, duplicate resource) |
| `422` | Validation error (invalid workflow definition, bad JSON) |
| `429` | Rate limited (forwarded from connector APIs) |
| `500` | Internal server error |
| `503` | Service unavailable (connector not connected) |

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
- **Status**: Exists

```
POST   /playlists
       body: { name: str, description?: str }
       → Playlist
```
- **Use case**: `CreateCanonicalPlaylistUseCase`
- **Status**: Exists

```
GET    /playlists/{id}
       → Playlist (with entries)
```
- **Use case**: `ReadCanonicalPlaylistUseCase`
- **Status**: Exists

```
PATCH  /playlists/{id}
       body: { name?: str, description?: str }
       → Playlist
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Exists

```
DELETE /playlists/{id}
       → 204
```
- **Use case**: `DeleteCanonicalPlaylistUseCase`
- **Status**: Exists

```
GET    /playlists/{id}/tracks
       ?limit=&offset=
       → { data: PlaylistEntry[], total, limit, offset }
```
- **Use case**: `ReadCanonicalPlaylistUseCase`
- **Status**: Exists

```
POST   /playlists/{id}/tracks
       body: { track_ids: int[], position?: int }
       → { data: PlaylistEntry[] }
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Exists (batch variant needs implementation)

```
DELETE /playlists/{id}/tracks/{entry_id}
       → 204
```
- **Use case**: `UpdateCanonicalPlaylistUseCase`
- **Status**: Exists

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
- **Status**: Exists

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
- **Status**: Exists

```
PATCH  /playlists/{id}/links/{link_id}
       body: { sync_direction?: str }
       → PlaylistMapping
```
- **Use case**: `UpdateConnectorPlaylistUseCase`
- **Status**: Exists

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
- **Status**: Exists

### Playlist Object Schemas (stub)

```json
// PlaylistSummary (list view)
{
  "id": 1,
  "name": "string",
  "description": "string | null",
  "track_count": 42,
  "connector_links": ["spotify", "apple_music"],
  "updated_at": "ISO8601"
}

// PlaylistEntry
{
  "id": 1,
  "position": 0,
  "track": { "id": 42, "title": "...", "artists": [{"name": "..."}] },
  "added_at": "ISO8601 | null",
  "added_by": "string | null"
}

// PlaylistMapping
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
       body: { from_date?: str, to_date?: str }
       → { operation_id: str }
```
- **Use case**: `ImportPlayHistoryUseCase`
- **Status**: Exists

```
POST   /imports/spotify/history
       body: multipart/form-data (one or more JSON files)
       → { operation_id: str }
```
- **Use case**: `ImportPlayHistoryUseCase` (file variant)
- **Status**: Exists

```
POST   /imports/spotify/likes
       → { operation_id: str }
```
- **Use case**: `SyncLikesUseCase` (Spotify import direction)
- **Status**: Exists

```
POST   /imports/lastfm/export-likes
       → { operation_id: str }
```
- **Use case**: `SyncLikesUseCase` (Last.fm export direction)
- **Status**: Exists

```
GET    /imports/lastfm/export-likes/preview
       → { count: int, tracks: TrackSummary[] }
```
- **Use case**: Preview export count
- **Status**: Needs implementation

```
GET    /imports/checkpoints
       → { data: SyncCheckpoint[] }
```
- **Use case**: Checkpoint query
- **Status**: Needs implementation

### Import Object Schemas (stub)

```json
// SyncCheckpoint
{
  "service": "lastfm | spotify",
  "entity_type": "play_history | likes",
  "last_timestamp": "ISO8601",
  "cursor": "string | null"
}
```

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
- **Use case**: `SSEProgressProvider` (new, implements existing `ProgressSubscriber` protocol)
- **Status**: Needs implementation
- **SSE reconnection**: Supports `Last-Event-ID` header for reconnection

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

```
GET    /operations
       ?limit=20
       → { data: OperationSummary[], total, limit, offset }
```
- **Use case**: Recent operations activity feed
- **Status**: Needs implementation

### SSE Event Format

```
id: 42
event: progress
data: {"operation_id":"uuid","status":"RUNNING","current":150,"total":5000,"message":"Importing plays...","metadata":{}}

id: 43
event: complete
data: {"operation_id":"uuid","status":"COMPLETED","current":5000,"total":5000,"message":"Import complete","result":{...}}
```

- `id` field enables `Last-Event-ID` reconnection
- `event` types: `progress`, `complete`, `error`, `cancelled`
- `data` matches the domain `ProgressEvent` shape

---

## 7. Connectors (Auth & Search)

```
GET    /connectors
       → { data: [{ name: str, connected: bool, last_used: ISO8601 | null }] }
```
- **Use case**: Connector status query
- **Status**: Needs implementation

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

## Use Case Mapping Summary

### Existing Use Cases (need API wrappers only)

| Use Case | API Endpoints |
|----------|--------------|
| `ListPlaylistsUseCase` | `GET /playlists` |
| `CreateCanonicalPlaylistUseCase` | `POST /playlists` |
| `ReadCanonicalPlaylistUseCase` | `GET /playlists/{id}`, `GET /playlists/{id}/tracks` |
| `UpdateCanonicalPlaylistUseCase` | `PATCH /playlists/{id}`, playlist track operations, reorder |
| `DeleteCanonicalPlaylistUseCase` | `DELETE /playlists/{id}` |
| `CreateConnectorPlaylistUseCase` | `POST /playlists/{id}/links` |
| `UpdateConnectorPlaylistUseCase` | `PATCH /playlists/{id}/links/{id}`, sync |
| `SyncLikesUseCase` | `POST /imports/spotify/likes`, `POST /imports/lastfm/export-likes` |
| `ImportPlayHistoryUseCase` | `POST /imports/lastfm/history`, `POST /imports/spotify/history` |
| `MatchAndIdentifyTracksUseCase` | `POST /tracks/rematch` |
| `EnrichTracksUseCase` | Internal (used by workflows) |

### Use Cases Needing Implementation

| Use Case | Milestone | API Endpoints |
|----------|-----------|--------------|
| SSE progress streaming | v0.3.1 | `GET /operations/{id}/progress` |
| Operation cancellation | v0.3.1 | `POST /operations/{id}/cancel` |
| `ListTracksUseCase` | v0.3.2 | `GET /tracks` |
| `GetTrackDetailsUseCase` | v0.3.2 | `GET /tracks/{id}` |
| `SearchTracksUseCase` | v0.3.2 | `GET /tracks?q=...` |
| `GetTrackConnectorMappingsUseCase` | v0.3.2 | `GET /tracks/{id}/mappings` |
| `GetTrackStatsUseCase` | v0.3.3 | `GET /stats/dashboard` |
| `GetConnectorMappingStatsUseCase` | v0.3.3 | `GET /stats/dashboard` (partial) |
| `GetSyncStatusUseCase` | v0.3.3 | `GET /connectors`, `GET /imports/checkpoints` |
| `GetMetadataFreshnessUseCase` | v0.3.3 | `GET /stats/dashboard` (partial) |
| Workflow CRUD | v0.4.0 | `GET/POST/PATCH/DELETE /workflows` |
| Workflow execution | v0.4.0 | `POST /workflows/{id}/run` |
| Connector playlist browse | v0.4.0 | `GET /connectors/{connector}/playlists` |
| Connector OAuth flows | v0.5.0 | `/auth/*`, `/connectors/*/auth-url` |
| `GetUnmappedTracksUseCase` | v0.6.0 | `GET /tracks?unmapped_for=...` |
