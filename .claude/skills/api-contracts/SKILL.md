---
name: api-contracts
description: Narada REST API endpoint reference — all routes, request/response schemas, SSE event format, pagination, error codes, and use case mapping. Use when implementing FastAPI route handlers or frontend Tanstack Query hooks.
user-invocable: false
---

# Narada API Contracts Reference

> Condensed from `docs/web-ui/03-api-contracts.md`. Base URL: `/api/v1`. All entity IDs are integers; operation IDs are UUID strings.

## Response Conventions

**List**: `{"data": [...], "total": int, "limit": int, "offset": int}` — pagination via `limit` (default 50, max 200) + `offset`
**Single**: object directly (no `data` wrapper)
**Error**: `{"error": {"code": "UPPER_SNAKE", "message": "Human readable", "details": {...}}}`
**Long ops**: return `{"operation_id": "uuid"}` immediately → SSE stream for progress

| HTTP | Meaning |
|------|---------|
| 400 | Bad input / validation failure |
| 404 | Not found |
| 409 | Conflict (op already running, duplicate) |
| 422 | Invalid definition / bad JSON |
| 429 | Rate limited (forwarded from connector) |
| 500 | Internal error |
| 503 | Connector unavailable |

## Endpoints

### Tracks (`/tracks`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/tracks?q=&connector=&liked=&sort=&limit=&offset=` | `ListTracksUseCase` | Needs impl |
| GET | `/tracks/{id}` | `GetTrackDetailsUseCase` | Needs impl |
| GET | `/tracks/{id}/mappings` | `GetTrackConnectorMappingsUseCase` | Needs impl |
| PATCH | `/tracks/{id}/mappings/{mapping_id}` | Manual mapping correction | Needs impl |
| DELETE | `/tracks/{id}/mappings/{mapping_id}` | Remove mapping | Needs impl |
| POST | `/tracks/{id}/like` | `SyncLikesUseCase` (single-track) | Needs impl |
| DELETE | `/tracks/{id}/like` | `SyncLikesUseCase` (single-track) | Needs impl |
| GET | `/tracks/{id}/playlists` | Playlists containing track | Needs impl |
| POST | `/tracks/rematch` | `MatchAndIdentifyTracksUseCase` | Exists |

### Playlists (`/playlists`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/playlists?limit=&offset=` | `ListPlaylistsUseCase` | Exists |
| POST | `/playlists` | `CreateCanonicalPlaylistUseCase` | Exists |
| GET | `/playlists/{id}` | `ReadCanonicalPlaylistUseCase` | Exists |
| PATCH | `/playlists/{id}` | `UpdateCanonicalPlaylistUseCase` | Exists |
| DELETE | `/playlists/{id}` | `DeleteCanonicalPlaylistUseCase` | Exists |
| GET | `/playlists/{id}/tracks?limit=&offset=` | `ReadCanonicalPlaylistUseCase` | Exists |
| POST | `/playlists/{id}/tracks` | `UpdateCanonicalPlaylistUseCase` | Exists |
| DELETE | `/playlists/{id}/tracks/{entry_id}` | `UpdateCanonicalPlaylistUseCase` | Exists |
| DELETE | `/playlists/{id}/tracks` (batch) | `UpdateCanonicalPlaylistUseCase` | Needs impl |
| PATCH | `/playlists/{id}/tracks/reorder` | `UpdateCanonicalPlaylistUseCase` | Exists |
| PATCH | `/playlists/{id}/tracks/move` | `UpdateCanonicalPlaylistUseCase` | Needs impl |

### Playlist Connector Links

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/playlists/{id}/links` | Query mappings | Needs impl |
| POST | `/playlists/{id}/links` | `CreateConnectorPlaylistUseCase` | Exists |
| PATCH | `/playlists/{id}/links/{link_id}` | `UpdateConnectorPlaylistUseCase` | Exists |
| DELETE | `/playlists/{id}/links/{link_id}` | Unlink connector | Needs impl |
| POST | `/playlists/{id}/links/{link_id}/sync` | `UpdateConnectorPlaylistUseCase` | Exists |

### Workflows (`/workflows`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/workflows` | List workflows | Needs impl |
| POST | `/workflows` | Create workflow | Needs impl |
| GET | `/workflows/{id}` | Get workflow | Needs impl |
| PATCH | `/workflows/{id}` | Update workflow | Needs impl |
| DELETE | `/workflows/{id}` | Delete workflow | Needs impl |
| POST | `/workflows/{id}/run` | Execute via Prefect | Needs impl |
| POST | `/workflows/{id}/preview` | Dry-run (skip writes) | Needs impl |
| POST | `/workflows/validate` | Validate definition | Needs impl |
| GET | `/workflows/{id}/runs?limit=&offset=` | List runs | Needs impl |
| GET | `/workflows/{id}/runs/{run_id}` | Run detail | Needs impl |

### Imports (`/imports`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| POST | `/imports/lastfm/history` | `ImportPlayHistoryUseCase` | Exists |
| POST | `/imports/spotify/history` (multipart) | `ImportPlayHistoryUseCase` | Exists |
| POST | `/imports/spotify/likes` | `SyncLikesUseCase` | Exists |
| POST | `/imports/lastfm/export-likes` | `SyncLikesUseCase` | Exists |
| GET | `/imports/lastfm/export-likes/preview` | Preview count | Needs impl |
| GET | `/imports/checkpoints` | Checkpoint query | Needs impl |

### Stats (`/stats`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/stats/dashboard` | `GetTrackStatsUseCase` | Needs impl |
| GET | `/stats/plays?from=&to=&limit=&offset=` | Play history query | Needs impl |
| GET | `/stats/top-tracks?period_days=30&limit=50` | Top tracks aggregation | Needs impl |

### Operations (`/operations`) — Progress & SSE

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/operations/{id}/progress` (SSE) | `SSEProgressProvider` | Needs impl |
| GET | `/operations/{id}` | Operation snapshot | Needs impl |
| POST | `/operations/{id}/cancel` | Cancel operation | Needs impl |
| GET | `/operations?limit=20` | Recent operations | Needs impl |

### Connectors (`/connectors`)

| Method | Path | Use Case | Status |
|--------|------|----------|--------|
| GET | `/connectors` | Status query | Needs impl |
| GET | `/connectors/spotify/auth-url` | OAuth URL | Needs impl |
| GET | `/auth/spotify/callback` | OAuth exchange → redirect | Needs impl |
| GET | `/connectors/lastfm/auth-url` | Auth URL | Needs impl |
| GET | `/auth/lastfm/callback` | Store session key → redirect | Needs impl |
| DELETE | `/connectors/{connector}/auth` | Disconnect | Needs impl |
| GET | `/connectors/{connector}/search?q=&limit=` | Track search | Needs impl |
| GET | `/connectors/{connector}/playlists?q=&limit=&offset=` | Browse playlists | Needs impl |

## SSE Event Format

```
id: 42
event: progress
data: {"operation_id":"uuid","status":"RUNNING","current":150,"total":5000,"message":"Importing plays...","metadata":{}}

id: 43
event: complete
data: {"operation_id":"uuid","status":"COMPLETED","current":5000,"total":5000,"message":"Import complete","result":{...}}
```

Event types: `progress`, `complete`, `error`, `cancelled`. Supports `Last-Event-ID` reconnection.

## Key Object Shapes

**Track**: `{id, title, artists: [{name}], album, duration_ms, release_date, isrc, connector_mappings: [{id, connector, connector_track_id, confidence, match_method, is_primary}], like_status: {service: {is_liked, liked_at}}, metrics: [{connector, metric_type, value, collected_at}], play_summary: {total_plays, last_played, first_played}}`

**PlaylistSummary**: `{id, name, description, track_count, connector_links: [str], updated_at}`

**PlaylistEntry**: `{id, position, track: {id, title, artists}, added_at}`

**WorkflowRun**: `{id (uuid), workflow_id, started_at, completed_at, status, result_summary, node_results: [{node_id, status, track_count, duration_ms}], error}`

## Architecture Reminder

Route handlers are 5-10 lines: parse request → build frozen Command → `execute_use_case()` → serialize Result. CLI and API call the same use cases through the same runner. Zero business logic in either interface layer.
