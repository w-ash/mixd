# 🎯 Active Work Tracker - Spotify Playlist Performance Optimization

> [!info] Purpose
> This file tracks active development work on optimizing Spotify playlist operations for large-scale transformations. For strategic roadmap and completed milestones, see [[BACKLOG.md]].

**Current Initiative**: Spotify Playlist Performance Optimization + Infrastructure Architecture Cleanup
**Status**: `#in-progress` `#infrastructure` `#architecture` `#v2.1`
**Last Updated**: 2025-08-08

## Progress Overview
- [x] **Refactor UpdateConnectorPlaylistUseCase** 🎉 (Completed - 775→663 lines, -14%)
- [x] **Optimize Spotify Operations** 🎉 (Completed - Sequencing + Batching implemented) 
- [ ] **Clean Architecture Refactor** 🔜 (Current focus - Separate API wrapper from business logic)

---

## 🔜 Epic: Spotify Playlist Performance Optimization `#in-progress`

**Goal**: Fix performance bottleneck in Spotify playlist operations where large transformations (700+ operations) timeout due to individual API calls with rate limiting delays.

**Why**: The test workflow (`test_playlist_update.json`) revealed that 746 move operations take 76+ seconds due to individual `playlist_reorder_items` calls. This blocks real-world usage of playlist workflows with meaningful track counts. Users need fast, reliable playlist synchronization that preserves context.

**Effort**: M - Infrastructure optimization with careful analysis of Spotify API constraints and user context preservation.

### 🤔 Key Architectural Decision
> [!important] Context Preservation Over Speed
> **Key Insight**: After analyzing the performance bottleneck, we discovered that the timeout is caused by 746 individual move operations, each making separate `playlist_reorder_items` API calls with delays. The naive solution would be complete playlist replacement, but this destroys valuable user context (`added_at` timestamps, `added_by` user info) that Spotify maintains.
>
> **Chosen Approach**: Optimize differential operations through intelligent move batching and operation reduction, while strictly preserving all user context. Use Spotify's `range_length` parameter to move blocks of tracks and add smart move detection to skip unnecessary operations.
>
> **Rationale**:
> - **Context Preservation**: Maintains `added_at` timestamps and `added_by` user attribution
> - **Performance**: Reduces 746 individual moves to ~50-100 block operations  
> - **API Efficiency**: Respects Spotify's differential operation model while optimizing throughput

### 📝 Implementation Plan
> [!note]
> Break down the work into logical, sequential tasks.

**Phase 1: Move Operation Analysis**
- [ ] **Task 1.1**: Analyze current move operation patterns in test workflow to understand redundancy
- [ ] **Task 1.2**: Research Spotify `playlist_reorder_items` API `range_length` parameter capabilities
- [ ] **Task 1.3**: Add logging to track move operation efficiency in SpotifyConnector

**Phase 2: Optimization Implementation**
- [ ] **Task 2.1**: Implement move operation grouping in `_execute_move_operations()` using `range_length > 1`
- [ ] **Task 2.2**: Add unnecessary move detection in diff engine to reduce operation count
- [ ] **Task 2.3**: Implement adaptive rate limiting based on operation batch sizes

**Phase 3: Testing & Validation**
- [ ] **Task 3.1**: Run test workflow to validate performance improvement
- [ ] **Task 3.2**: Verify context preservation (added_at timestamps remain intact)
- [ ] **Task 3.3**: Add performance benchmarks for large playlist operations

### ✨ User-Facing Changes & Examples
**Performance Improvements**:
- Large playlist workflows (700+ operations) complete in 10-20 seconds instead of timing out
- Maintains all existing functionality with no API changes
- Preserves Spotify playlist context (timestamps, user attribution)

**Example Workflow Impact**:
```bash
# Before: Times out after 2+ minutes
poetry run narada playlist run test_playlist_update

# After: Completes in ~15 seconds
poetry run narada playlist run test_playlist_update
✅ Completed in 14.2s - 746 moves optimized to 67 block operations
```

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**: Potentially optimize move detection in `playlist/diff_engine.py`
- **Application**: No changes needed (already optimized in previous epic)
- **Infrastructure**: Primary changes in `connectors/spotify.py` move operation handling
- **Interface**: No changes (performance is transparent to CLI)

**Testing Strategy**:
- **Unit**: Test move grouping logic and operation counting 
- **Integration**: Test Spotify API batch move operations with real playlists
- **E2E/Workflow**: Validate `test_playlist_update.json` completes under 30 seconds

**Key Files to Modify**:
- `src/infrastructure/connectors/spotify.py` - `_execute_move_operations()` optimization
- `src/domain/playlist/diff_engine.py` - Optional move reduction logic
- `src/config/settings.py` - Rate limiting configuration
- `tests/infrastructure/connectors/test_spotify.py` - Performance benchmarks

### 🔍 Current Context & Discovery

**Performance Problem Identified**: 
The `test_playlist_update.json` workflow timeout revealed the root cause:
- 746 individual move operations via `playlist_reorder_items`
- Each operation includes `await asyncio.sleep(settings.api.spotify_request_delay)`
- 746 × 0.1s delay = 74.6s minimum, plus actual API time
- Total execution time exceeds 2-minute timeout

**Spotify API Capabilities**:
- `playlist_reorder_items` supports `range_length > 1` for block moves
- Differential operations preserve `added_at` timestamps and user attribution
- Rate limiting is primarily about request frequency, not payload size

**Architecture Analysis**:
- Recent refactoring of `UpdateConnectorPlaylistUseCase` (775→663 lines) is working correctly
- Application layer properly delegates to infrastructure
- SpotifyConnector correctly implements differential operations but not optimally batched

---

## 🔄 NEW Epic: Infrastructure Architecture Cleanup `#in-progress`

**Goal**: Separate thin API wrappers from connector-specific business logic across all connectors to improve maintainability and testability.

**Why**: During performance optimization, we added ~80 lines of business logic (operation grouping, batching) directly to `SpotifyConnector`, bloating it from ~1100 to 1178 lines. This violates single responsibility principle and mixes API client concerns with optimization logic.

**Effort**: S - Architectural refactoring to extract services, improving separation of concerns.

### 🤔 Key Architectural Decision
> [!important] Clean Separation: API Client vs Business Logic  
> **Key Insight**: Connectors should be thin API wrappers, not complex orchestrators. Business logic like operation optimization, batching strategies, and complex coordination should be extracted into dedicated infrastructure services.
>
> **Chosen Approach**: Extract connector-specific business logic into `*OperationService` classes in `src/infrastructure/services/`. Keep connectors focused purely on API calls, authentication, and response processing.
>
> **Rationale**:
> - **Single Responsibility**: Each class has one clear purpose
> - **Testability**: Business logic can be unit tested independently from API calls  
> - **Reusability**: Optimization logic could be shared or adapted for other services
> - **Maintainability**: Easier to modify business logic without touching API integration

### 🔍 Infrastructure Audit Results

**Current Connector Sizes**:
- ✅ `lastfm.py` (798 lines) - **Good separation**, only has `batch_get_track_info` business logic
- ✅ `musicbrainz.py` (241 lines) - **Clean API wrapper**
- ✅ `spotify.py` (1053 lines) - **Clean API wrapper** (business logic extracted to services)
- ✅ `base_connector.py` (365 lines) - **Appropriate infrastructure abstractions**

**Business Logic Found in Connectors**:
- `SpotifyConnector`: ✅ **Extracted** - All optimization logic moved to `PlaylistOperationService`
- `LastFMConnector`: Basic batch processing (appropriate for that connector)
- Others: Clean API wrappers ✅

### 📝 Refactoring Implementation Plan

**Phase 1: Extract Spotify Business Logic**
- [ ] **Task 1.1**: Create `PlaylistOperationService` in `src/infrastructure/services/`
- [ ] **Task 1.2**: Move operation grouping methods (`_group_consecutive_*`) to service
- [ ] **Task 1.3**: Move complex sequencing logic to service
- [ ] **Task 1.4**: Update use case to orchestrate service + connector

**Phase 2: Simplify SpotifyConnector**
- [x] **Task 2.1**: Remove business logic methods from connector 🎉 (Completed)
- [x] **Task 2.2**: Keep only thin API wrapper methods 🎉 (Completed)
- [x] **Task 2.3**: Simplify `execute_playlist_operations()` to basic API delegation 🎉 (Completed)

**Phase 3: Test & Validate Clean Architecture** 
- [ ] **Task 3.1**: Run test workflow to ensure functionality preserved
- [ ] **Task 3.2**: Unit test business logic service separately from API connector
- [x] **Task 3.3**: Verify line count reduction (1178 → 1053 lines for spotify.py) 🎉 (Completed - 125 lines removed, 10.6% reduction)

### 🛠️ Target Architecture

**After Refactoring**:
```
UpdateConnectorPlaylistUseCase
├── PlaylistOperationService (business logic)
│   ├── optimize_operations() 
│   ├── group_consecutive_moves()
│   └── sequence_for_efficiency()  
└── SpotifyConnector (thin API wrapper)
    ├── playlist_add_items()
    ├── playlist_reorder_items()
    └── playlist_remove_items()
```

**Benefits**:
- **SpotifyConnector**: 1053 lines (down from 1178) - focused API client
- **PlaylistOperationService**: 246 lines - testable business logic  
- **Clean separation**: API concerns vs optimization concerns
- **Better testability**: Mock API calls independently from business logic tests

### 📁 Files to Create/Modify
- **New**: `src/infrastructure/services/playlist_operation_service.py`
- **Modified**: `src/infrastructure/connectors/spotify.py` (extract business logic)
- **Modified**: `src/application/use_cases/update_connector_playlist.py` (orchestrate service)
- **Tests**: `tests/unit/infrastructure/services/test_playlist_operation_service.py`

**Next Developer Notes**:
- Focus on extracting optimization logic from `SpotifyConnector` 
- Keep API methods thin and focused
- Test against playlist `14GT9ahKyAR9SObC7GdwtO` to ensure no regressions
- Key insight: Clean architecture improves both maintainability and performance