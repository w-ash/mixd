---
name: api-contracts
description: Mixd REST API conventions — response envelope, pagination, error codes, SSE event format, long-operation and confirm-token patterns, plus where the authoritative endpoint list lives. Use when implementing FastAPI route handlers or frontend Tanstack Query hooks.
user-invocable: false
---

# Mixd API Conventions

> **Endpoint truth is generated, not listed here.** For what exists right now: `web/openapi.json` (regenerated every `pnpm --prefix web sync-api`) or the routers in `src/interface/api/routes/`. For planned/designed endpoints and per-flow status: `docs/web-ui/03-api-contracts.md`. Route → use case mapping: read the route handler — they're 5–10 lines and name the Command directly. (This skill previously carried hand-synced endpoint tables; they drifted twice — v0.8.6 found 7 missing routers — so they were removed 2026-07-03.)

Base URL: `/api/v1`. All IDs — entities and operations — are UUID strings.

## Response conventions

- **List**: `{"data": [...], "total": int, "limit": int, "offset": int}` — pagination via `limit` (default 50, max 200) + `offset`. `/operation-runs` uses cursor pagination (`limit` + `cursor`).
- **Single**: object directly (no `data` wrapper).
- **Error**: `{"error": {"code": "UPPER_SNAKE", "message": "Human readable", "details": {...}}}`
- **Long operations**: return `{"operation_id": "uuid"}` immediately → progress via SSE.

| HTTP | Meaning |
|------|---------|
| 400 | Bad input / validation failure |
| 404 | Not found |
| 409 | Conflict (op already running, duplicate, or confirmation required) |
| 422 | Invalid definition / bad JSON |
| 429 | Rate limited (forwarded from connector) |
| 500 | Internal error |
| 503 | Connector unavailable |

## Semantic patterns (not visible in openapi.json)

- **Destructive sync confirm-token**: an unconfirmed destructive sync returns **409 `CONFIRMATION_REQUIRED`** with a fresh `confirm_token` in `details`; the client re-POSTs with `{confirm_token}`. Preview endpoints (`.../sync/preview`) return the diff + safety assessment + token up front.
- **Direction vocabulary**: link/preview responses carry `direction_label` — one user-facing vocabulary that leads with what gets overwritten (e.g. "Spotify → Mixd (replaces Mixd)"). Default `sync_direction` is **pull**.
- **Operation awareness / re-attach**: `GET /operation-runs?status=running` lists in-flight runs; rows carry `operation_id` (the SSE handle) so the UI can re-attach after reload. `GET /operations/{id}/snapshot` serves stalled-SSE recovery.

## SSE event format

`GET /operations/{id}/progress` (`text/event-stream` — stubbed to an empty schema in `web/openapi.json` by `scripts/export_openapi.py`, so this section is the reference):

```
id: 42
event: progress
data: {"operation_id":"uuid","status":"RUNNING","current":150,"total":5000,"message":"Importing plays...","metadata":{}}

id: 43
event: complete
data: {"operation_id":"uuid","status":"COMPLETED","current":5000,"total":5000,"message":"Import complete","result":{...}}
```

Event types: `progress`, `complete`, `error`, `cancelled`. Supports `Last-Event-ID` reconnection. On stream end, the frontend reconciles via REST (v0.8.8 pattern).

## Object shapes

Generated TypeScript models are the reference: `web/src/api/generated/model/`. Backend Pydantic schemas: `src/interface/api/schemas/`. Don't restate shapes from memory — read the generated type.

## Architecture reminder

Route handlers are 5–10 lines: parse request → build frozen Command → `execute_use_case()` → serialize Result. CLI and API call the same use cases through the same runner. Zero business logic in either interface layer.
