---
paths:
  - "src/application/**"
---
# Application Layer Rules
- Access infrastructure through `UnitOfWorkProtocol` and repository protocols only — no direct infrastructure imports
- **Approved infrastructure bridges**: `runner.py`, `prefect.py`, `workflows/context.py` — DI wiring entry points that intentionally import infrastructure for session/UoW creation
- All database operations go through UnitOfWork — `async with uow:` for every transaction
- Use case owns transaction boundaries: `await uow.commit()` explicitly
- All use cases run through `application/runner.py` → `execute_use_case()`
- **Typed connector resolvers**: Use `resolve_playlist_connector()`, `resolve_liked_track_connector()`, `resolve_love_track_connector()` from `_shared/connector_resolver.py` — these return typed capability protocols (`PlaylistConnector`, `LikedTrackConnector`, etc. from `connector_protocols.py`). Raw `resolve_connector()` returns `Any` and must not be used in typed code.
- Command/Result objects: `@define(frozen=True)`
- **Prefect workflows**: each task creates its own session from the PostgreSQL pool; level-based `asyncio.gather()` for parallel DAG execution (see `compute_parallel_levels` in `validation.py`). Pipelines are declarative: Source → Enricher → Filter → Sorter → Selector → Destination (see docs/guides/workflows.md)
- **Database-first workflows**: all workflow operations work on database tracks (`track.id is not None`), never on raw connector data. Source nodes MUST persist via `SavePlaylistUseCase` before returning.
- **Intentional pattern repetition**: each use case owns its own Command/Result types, transaction boundaries, and error handling — keep them co-located even when similar.
- **All use cases follow Command/Result**: every `execute()` takes `(command, uow) -> Result`. Even parameterless queries use an empty Command for API uniformity — the signature never changes when params are added later.
- **Preview use cases are read-only**: use cases that preview side effects (e.g., `PreviewPlaylistSyncUseCase`) fetch external state and compute diffs but never call `uow.commit()`. They reuse existing connector methods (e.g., `sync_connector_playlist()`) in a read-only context.
