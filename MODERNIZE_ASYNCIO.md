# 🎯 Active Work Tracker - Modernize Asyncio for Python 3.14

> [!info] Purpose
> This file tracks the work to modernize asyncio code patterns for Python 3.14 compatibility, removing deprecated APIs that will be removed in Python 3.16.

**Current Initiative**: Python 3.14 Asyncio Modernization
**Status**: `#in-progress` `#infrastructure` `#v0.2.8`
**Last Updated**: 2025-12-21

## Progress Overview
- [x] **Upgrade to Python 3.14.2** ✅ (Completed)
- [x] **Update all dependencies to latest versions** ✅ (Completed)
- [ ] **Modernize asyncio patterns** 🔜 (Current focus)

---

## 🔜 Epic: Modernize Asyncio Event Loop Configuration `#in-progress`

**Goal**: Replace deprecated `asyncio.DefaultEventLoopPolicy` and `asyncio.iscoroutinefunction()` with Python 3.14+ recommended patterns while maintaining critical 200-thread concurrency for LastFM batch operations.

**Why**: Python 3.14 introduced deprecation warnings for several asyncio APIs that will be removed in Python 3.16. We need to modernize our code now to:
- Eliminate 239 deprecation warnings in test suite
- Ensure forward compatibility with Python 3.16
- Use modern, recommended patterns for event loop management

**Effort**: M - Affects 9 CLI command files + core infrastructure, but pattern is straightforward

### 🤔 Key Architectural Decision
> [!important] Explicit Executor Configuration Over Global Policy
> **Key Insight**: After analyzing asyncio usage across the codebase, discovered that:
> - All event loops are created via `asyncio.run()` in CLI command handlers (9 locations)
> - No central application initialization point exists (Typer architecture)
> - Current global policy works via import-time side effect
> - 200-thread executor is CRITICAL for LastFM batch operations (prevents 8x slowdown)
>
> **Chosen Approach**: Replace global event loop policy with explicit helper function:
> - Create `run_async_with_connector_executor(coro)` helper in `connectors/__init__.py`
> - Replace all `asyncio.run()` calls in CLI commands with new helper
> - Use `inspect.iscoroutinefunction()` instead of `asyncio.iscoroutinefunction()`
>
> **Rationale**:
> - **Explicit over implicit**: No import-time side effects, clearer code flow
> - **Better testability**: Executor configuration is a function call, not global state
> - **Forward compatible**: Uses Python 3.14+ recommended patterns
> - **Maintains performance**: Still provides 200-thread concurrency (vs default 32-36)

### 📝 Implementation Plan
> [!note]
> Two-part implementation: simple fix for repo_decorator.py, then systematic replacement of event loop policy.

**Phase 1: Simple Fix - Replace asyncio.iscoroutinefunction**
- [x] Update `src/infrastructure/persistence/repositories/repo_decorator.py`
  - Add `import inspect`
  - Replace `asyncio.iscoroutinefunction()` with `inspect.iscoroutinefunction()`

**Phase 2: Create Modern Executor Helper**
- [ ] **Task 2.1**: Update `src/infrastructure/connectors/__init__.py`
  - Remove `_NaradaEventLoopPolicy` class (lines 31-52)
  - Remove `asyncio.set_event_loop_policy()` call (line 57)
  - Add `create_executor_for_connectors()` function
  - Add `run_async_with_connector_executor(coro)` helper function
  - Update `__all__` exports to include new helper

**Phase 3: Update CLI Commands**
- [ ] **Task 3.1**: Update `src/interface/cli/workflow_commands.py` (1 location)
- [ ] **Task 3.2**: Update `src/interface/cli/playlist_commands.py` (3 locations)
- [ ] **Task 3.3**: Update `src/interface/cli/likes_commands.py` (5 locations)
- [ ] **Task 3.4**: Update `src/interface/cli/track_commands.py` (2 locations)
- [ ] **Task 3.5**: Update `src/interface/shared/cli_helpers.py` (1 location)

**Phase 4: Testing & Validation**
- [ ] **Task 4.1**: Run unit tests - verify all 618 tests pass
- [ ] **Task 4.2**: Run integration tests - verify connector operations work
- [ ] **Task 4.3**: Test high-concurrency operation manually (LastFM import)
- [ ] **Task 4.4**: Verify zero deprecation warnings with `pytest -W error::DeprecationWarning`

### ✨ User-Facing Changes & Examples

**No user-facing changes** - This is an internal infrastructure modernization. Users will see:
- Same CLI commands work identically
- Same performance characteristics
- No deprecation warnings in output

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Infrastructure**:
  - `connectors/__init__.py` - Remove deprecated policy, add modern helper
  - `repositories/repo_decorator.py` - Use inspect.iscoroutinefunction
- **Interface**:
  - All CLI command modules - Replace `asyncio.run()` with new helper
  - CLI helpers - Update shared utilities

**Testing Strategy**:
- **Unit**: All existing 618 unit tests should pass unchanged
- **Integration**: Connector operations (LastFM, Spotify, MusicBrainz) work correctly
- **Performance**: Validate 200-thread concurrency is maintained (existing test in `test_thread_pool_configuration.py`)
- **Warnings**: Run `pytest -W error::DeprecationWarning` to ensure zero asyncio warnings

**Key Files to Modify**:
- `src/infrastructure/connectors/__init__.py` (remove policy, add helper)
- `src/infrastructure/persistence/repositories/repo_decorator.py` (simple fix)
- `src/interface/cli/workflow_commands.py` (1 asyncio.run call)
- `src/interface/cli/playlist_commands.py` (3 asyncio.run calls)
- `src/interface/cli/likes_commands.py` (5 asyncio.run calls)
- `src/interface/cli/track_commands.py` (2 asyncio.run calls)
- `src/interface/shared/cli_helpers.py` (1 asyncio.run call)
- `tests/unit/infrastructure/connectors/shared/test_thread_pool_configuration.py` (update if needed)

**Critical Constraint**: Must maintain 200-thread executor concurrency. Default Python executor caps at ~32-36 threads, which causes LastFM batch operations to slow from ~0.5s to 4+ seconds.

### 📊 Current Deprecation Warnings
From test run with Python 3.14.2:
- **239 total warnings** (618 tests passed)
- **172 warnings** from third-party `backoff` library (external, can't fix)
- **67 warnings** from our code:
  - 47 from `repo_decorator.py:65` - `asyncio.iscoroutinefunction()`
  - 2 from `connectors/__init__.py:31,57` - `DefaultEventLoopPolicy`, `set_event_loop_policy()`
  - 18 from other asyncio usages

**Target**: Reduce to ~172 warnings (only external backoff library)
