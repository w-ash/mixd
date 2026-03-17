# Database Session Management

How Narada manages database sessions to handle SQLite's concurrency limitations while maintaining Clean Architecture principles and proper transaction boundary control through the UnitOfWork pattern.

## Transaction Management Philosophy

**Application Layer Controls Transaction Boundaries**: Use cases decide when transactions begin, commit, or rollback based on business logic, not just technical success/failure.

**Infrastructure Layer Handles Technical Implementation**: Database connections, session lifecycle, and transaction mechanics are managed by infrastructure components.

**Clean Separation**: Business logic remains decoupled from session management complexity.

## UnitOfWork Pattern Implementation

```python
# Domain interface
class UnitOfWork(Protocol):
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    def get_track_repository(self) -> TrackRepository: ...


# Infrastructure implementation
class DatabaseUnitOfWork:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._committed = False
        self._connector_cache: dict[str, Any] = {}  # Per-UoW instance caching

    async def commit(self):
        await self._session.commit()
        self._committed = True

    async def rollback(self):
        await self._session.rollback()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # ... commit/rollback logic ...
        # Close cached connector instances (httpx pools, etc.)
        for connector in self._connector_cache.values():
            if hasattr(connector, "aclose"):
                await connector.aclose()
        self._connector_cache.clear()

    def get_track_repository(self) -> TrackRepository:
        return TrackRepository(self._session)
```

## Session Management Patterns

### 1. Workflow-Scoped Sessions
**Pattern**: Single shared session per workflow execution
**Implementation**: `SharedSessionProvider` in `prefect.py`
**Usage**: All Prefect workflow tasks share one session to prevent concurrent write conflicts

```python
# Create a single shared session for the entire workflow execution
async with get_session() as shared_session:
    # Create shared session provider that wraps the session
    shared_session_provider = SharedSessionProvider(shared_session)

    # All workflow tasks use the same session
    context = {
        "session_provider": shared_session_provider,
        "shared_session": shared_session,
    }
```

**Benefits**: Eliminates SQLite "database is locked" errors, ensures ACID properties across workflow operations, simplifies transaction management.

### 2. Session-Per-Operation Pattern
**Pattern**: Fresh session for each discrete operation
**Implementation**: `DatabaseProgressContext.run_with_repositories()`
**Usage**: CLI operations and use cases that don't run within workflows

```python
async with DatabaseProgressContext(...) as progress:
    # Each operation gets its own short-lived session
    async def _import_operation(repositories: TrackRepositories) -> OperationResult:
        # Session created and closed automatically
        return await service.import_play_history(...)

    return await progress.run_with_repositories(_import_operation)
```

**Benefits**: Prevents long-held sessions, follows SQLAlchemy best practices, maintains Clean Architecture boundaries.

### 3. Isolated Sessions for Metrics
**Pattern**: Specialized sessions for operations needing isolation
**Implementation**: `get_isolated_session()`
**Usage**: Metrics operations that may conflict with main operations

```python
async with get_isolated_session() as session:
    # Optimized session settings for metrics operations
    # - autoflush=False to avoid implicit I/O
    # - isolated transaction boundaries
```

**Benefits**: Prevents metrics operations from interfering with main workflows, optimized for specific use cases.

## SQLite Configuration

**Connection Pooling**: Uses `NullPool` for SQLite to create/close connections on demand, eliminating pooling-related locks.

**Pragmas Applied**:
- `journal_mode=WAL`: Write-ahead logging for concurrent read access
- `busy_timeout=30000`: 30-second timeout for lock conflicts
- `synchronous=NORMAL`: Balanced safety/performance
- `foreign_keys=ON`: Enforce referential integrity

**Event Listeners**: Automatically apply pragmas on each connection creation to ensure consistent database behavior.

## Anti-Patterns to Avoid

❌ **Multiple Concurrent Sessions in Workflows**: Creates SQLite lock conflicts
❌ **Long-Held Sessions**: Blocks other operations unnecessarily
❌ **Direct Session Creation**: Bypasses configured pragmas and pooling strategy
❌ **Session Sharing Across Components**: Violates Clean Architecture boundaries

✅ **Use Workflow-Scoped Sessions**: For Prefect workflows
✅ **Use Session-Per-Operation**: For CLI and use case operations
✅ **Use Context Managers**: Ensure proper session lifecycle management
✅ **Follow Injection Patterns**: Maintain Clean Architecture compliance
