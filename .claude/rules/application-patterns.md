---
paths:
  - "src/application/**"
---
# Application Layer Rules

## Boundaries
- Access infrastructure through `UnitOfWorkProtocol` and repository protocols only.
- All database operations go through UnitOfWork: `async with uow:` per transaction.
- The use case owns transaction boundaries: explicit `await uow.commit()`.
- All use cases run through `application/runner.py` → `execute_use_case()`.
- **Approved infrastructure bridges**: `runner.py`, `workflows/engine/executor.py`, `workflows/context.py` are DI wiring entry points that intentionally import infrastructure for session/UoW creation. The function-scoped `MetricConfigProviderImpl` import (the `metric_config` protocol's concrete provider; precedent: `sync_playlist_link.py`) is also sanctioned — imported inside the function that needs it, not at module top, to keep the layer edge narrow.

## Connector resolution
Use typed resolvers from `_shared/connector_resolver.py` — `resolve_playlist_connector()`, `resolve_liked_track_connector()`, `resolve_love_track_connector()` — returning typed capability protocols (`PlaylistConnector`, `LikedTrackConnector`, etc. from `connector_protocols.py`). Raw `resolve_connector()` returns `Any`; reach for the typed resolver in typed code.

## Command/Result shape
- Every `execute()` takes `(command, uow) -> Result`. Even parameterless queries use an empty Command for API uniformity — the signature stays stable when params are added later.
- Command/Result objects: `@define(frozen=True)`.
- Each use case owns its own Command/Result types, transaction boundaries, and error handling. Pattern repetition across use cases is intentional — keep them co-located even when similar.
- Envelope helpers in `use_cases/_shared/` (`persist_entry_change`, `mutate_owned_link`, `apply_with_event_log`, `require_owned_mapping`, `timed_query`) are the sanctioned way to share the transaction/guard skeleton — the Command/Result surface stays per use case; only the envelope plumbing is shared.
- **Streaming-agentic-loop exemption**: `ChatUseCase` breaks the `(command, uow) -> Result` shape by design — it takes `(command) -> AsyncGenerator[ChatEvent]`, is invoked directly by its route (not via `execute_use_case()`), and holds no UoW. All persistence inside the loop goes through per-tool-call `execute_use_case()` instead. Threading a single UoW across an LLM round-trip (seconds to minutes of model latency, holding a DB transaction open the whole time) is strictly worse, so the loop owns no transaction and each tool call is its own short transaction.

## Workflows
- Pipelines are declarative: Source → Enricher → Filter → Sorter → Selector → Destination (see `docs/guides/workflows.md`).
- Each node task creates its own session from the PostgreSQL pool; level-based `asyncio.TaskGroup` for parallel DAG execution (see `compute_parallel_levels` in `validation.py`).
- All workflow operations work on database tracks (`track.id is not None`), never on raw connector data. Source nodes persist via `SavePlaylistUseCase` before returning.

## Preview use cases
Use cases that preview side effects (e.g., `PreviewPlaylistSyncUseCase`) fetch external state and compute diffs but skip `uow.commit()`. They reuse existing connector methods (e.g., `sync_connector_playlist()`) in a read-only context.
