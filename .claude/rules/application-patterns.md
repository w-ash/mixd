---
paths:
  - "src/application/**"
---
# Application Layer Rules
- NEVER import from infrastructure directly — use `UnitOfWorkProtocol` and repository protocols
- **Approved infrastructure bridges**: `runner.py`, `prefect.py`, `workflows/context.py` — DI wiring entry points that intentionally import infrastructure for session/UoW creation
- NEVER bypass UnitOfWork for database operations
- Use case owns transaction boundaries: `async with uow:` ... `await uow.commit()`
- All use cases run through `application/runner.py` → `execute_use_case()`
- **Typed connector resolvers**: Use `resolve_playlist_connector()`, `resolve_liked_track_connector()`, `resolve_love_track_connector()` from `_shared/connector_resolver.py` — NOT raw `resolve_connector()` which returns `Any`. Capability protocols (`PlaylistConnector`, `LikedTrackConnector`, `LoveTrackConnector`, `TrackMetadataConnector`) live in `connector_protocols.py` (NOT `workflows/protocols.py` — that would create a circular import).
- Command/Result objects: `@define(frozen=True)`
- Constructor injection for all dependencies
