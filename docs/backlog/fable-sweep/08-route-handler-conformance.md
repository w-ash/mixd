# 08 — Route handlers: move the strays back behind execute_use_case

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** interface · **Suggested executor:** Opus · **Effort:** M · **ROI:** med · **Risk:** med · **Status:** Not Started

## Problem

The convention (CLAUDE.md layer invariants) is 5–10-line handlers: parse → Command → `execute_use_case` → serialize. Most of the 18 route modules comply; six handlers have drifted (audit 2026-07-01):

1. `interface/api/routes/tracks.py:119-127` (`list_tracks`) — tag normalization with try/except→422 **business logic in the handler**; belongs in the Command validator.
2. `interface/api/routes/playlists.py:246` (`get_playlist_tracks`) — in-handler pagination: slices `entries[offset : offset + limit]` and builds the page itself.
3. `interface/api/routes/tracks.py:197-199` (`merge_track`) — composes two use cases (merge + `GetTrackDetailsUseCase`) in the handler.
4. `interface/api/routes/playlist_assignments.py:61-68` (`create_and_apply_assignment`) — composes two use cases via an inner `_create_and_apply(uow)` function.
5. `interface/api/routes/workflows.py:368-422` (`run_workflow_endpoint`, ~55 lines) — SSE orchestration: `prepare_sse_operation`, hand-built `sse_queue.put({...})`, `launch_background`, teardown.
6. `interface/api/routes/playlists.py:447-488` (`sync_playlist_link`) — direction parsing + `_ensure_sync_confirmed` + inner SSE coroutine.

## Why it matters

Maintainer: logic in handlers is untestable without HTTP plumbing and invisible to the CLI (which shares use cases, not routes) — e.g. the CLI's `list_tracks` path today gets *different* tag-normalization behavior than the web's. User: indirect — CLI/web parity (the Curator uses both).

## Proposed change

1. (1)+(2): move normalization into `ListTracksCommand` validation and pagination into `ReadCanonicalPlaylist`/a query use case; handlers shrink to the standard shape. 422 semantics preserved via the standard command-validation error mapping.
2. (3)+(4): create composing use cases (`MergeTrackAndFetchDetails` may be overkill — alternatively have `MergeTracksUseCase` return the details the route needs; pick the smaller diff) and `CreateAndApplyAssignmentUseCase` (the inner function is already the use-case body).
3. (5)+(6): extract the SSE boilerplate into the existing SSE helper layer (`prepare_sse_operation` siblings in `interface/api/` — enumerate with `git grep -l prepare_sse_operation`); handlers keep only route wiring. SSE event shapes must stay byte-identical (frontend `useOperationSSE` parses them).

## Blast radius & behavior-preservation

Response schemas, status codes, and SSE event payloads unchanged — assert via existing API tests. The 422 error body for bad tags must keep its shape. Frontend Orval client untouched (no OpenAPI change).

## Test plan

Existing: `uv run pytest tests/ -k "routes or api"` + the affected use-case suites. Add: one unit test per new/expanded use case (the composition cases). Frontend: no changes expected; run `pnpm --prefix web test` to confirm no SSE contract drift.

## Guardrails (do not skip)

- **Clean break:** inner helper functions in handlers deleted, not exported.
- **Grep gate:** `git grep '_create_and_apply' src/interface` returns nothing when done.
- **Layer flow:** this spoke *restores* the invariant (interface → application only). The OAuth-shared-helpers exception is untouched.
- **Green:** `uv run pytest` + `pnpm --prefix web test` stay green; no test weakened.
- **Ratchet:** `tracks.py`'s 2 PLR violations should clear; verify.
- **Scope discipline:** the many-`Query(...)`-param bulk of `list_tracks` (~60 lines of declarations) is FastAPI-inherent — leave it.

## Notes / counter-proposal

Depends on spoke 07 only if the same `_shared` modules are touched simultaneously — coordinate or sequence 07 → 08.
