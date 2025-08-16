# 🎯 Active Work Tracker - Connector to Canonical Playlist Backup Hardening

> [!info] Purpose
> This file tracks active development work on the current epic. For strategic roadmap and completed milestones, see [[BACKLOG.md]].

## 🎯 Playlist Backup Hardening

> [!abstract] **Initiative Overview**
> **Current Initiative**: Connector to Canonical Playlist Backup Hardening with Metadata Preservation  
> **Status**: `#architecture-redesign` `#list-reordering` `#v0.2.7`  
> **Last Updated**: 2025-08-14

### Initiative Goal
Create a bulletproof playlist synchronization system that transfers connector playlists (Spotify, Last.fm, etc.) to canonical format with 100% first-pass accuracy.

**What We're Building:**
- **New Playlist Creation**: Convert connector playlists to canonical format
- **Existing Playlist Updates**: Synchronize changes (add, remove, reorder) without data loss
- **Universal API Logic**: Same algorithm works for internal canonical and external connector updates

**Non-Negotiable Requirements:**
- **Single-Pass Success**: Achieve exact target state on first execution (no retry loops)
- **Metadata Preservation**: Maintain playlist_track history (timestamps, user attribution, likes)
- **Idempotent Operations**: Repeated runs on unchanged playlists = 0 operations
- **Minimal Moves**: Only reorder tracks not already in correct positions (LIS optimization)

**Root Problem**: Current position-based diff engine fails due to cascading position shifts during sequential execution.

### Success Criteria
**Must Achieve:**
1. **100% First-Pass Accuracy**: Every backup run achieves exact target state without retries
2. **Zero-Op Idempotence**: Unchanged playlists generate 0 operations on subsequent runs
3. **Cross-Platform Compatibility**: Same logic works for canonical and connector playlist updates
4. **Optimal Performance**: Only move tracks that aren't already in correct order

**Validation Tests:**
- Unchanged playlist → 0 operations
- Simple add/remove → Direct operations only  
- Complex reordering → Minimal LIS-optimized moves
- Large playlists (1000+ tracks) → Sub-second performance
- API integration → No position conflicts in sequential execution

---

## 🏗️ Three-Layer Architecture Solution

Replace position-dependent operations with mathematically correct reordering using proven algorithms (LIS) and dependency-aware execution. Same logic works for both atomic canonical updates and sequential API operations.

### Layer 1: Semantic Diff Analysis 
- **Purpose**: Understand what actually changed (tracks added/removed/reordered)
- **Output**: Semantic change description (not position-dependent operations)
- **Algorithm**: Longest Increasing Subsequence (LIS) to find tracks already in correct order
- **Same logic for**: Both canonical and connector updates (DRY principle)

### Layer 2: Operation Planning
- **Purpose**: Calculate optimal execution strategy for changes
- **For Simple Changes**: Direct add/remove operations
- **For Complex Reordering**: LIS-based minimal move set with dependency-safe execution order
- **Output**: Execution plan that avoids position conflicts

### Layer 3: Execution Strategy
- **API Strategy**: Sequential execution with error handling and rate limiting
  - Execute moves in dependency order to avoid position conflicts
  - Handle failures gracefully per operation
- **Canonical Strategy**: Atomic state transformation
  - Direct reordering for performance and correctness
  - Same operation counts for metrics/logging consistency

> [!tip] **Key Benefits of This Approach**
> 1. **🔄 DRY Compliance**: Same diff logic works for canonical and connector updates
> 2. **🏗️ DDD Preservation**: Maintains operation-based architecture for API compatibility
> 3. **⚡ Single-Pass Execution**: Batch processing eliminates cascading position shifts
> 4. **🛡️ Idempotent Operations**: Running backup twice on unchanged playlist = 0 operations
> 5. **📊 Preserves Track Instances**: No "blowing away" of track metadata/history
> 6. **🧪 Internal Testing**: Fix canonical playlists before testing against external APIs
> 7. **🎯 Minimal Operations**: LIS optimization reduces unnecessary moves
> 8. **💯 100% Correct First-Time**: Mathematical guarantees via proven algorithms

> [!note] **Files to Consider**
> 
> **Domain**
> - `src/domain/transforms/core.py`
> - `src/domain/repositories/interfaces.py`
> - `src/domain/entities/playlist.py`
> - `src/domain/playlist/diff_engine.py`
> 
> **Application**
> - `src/application/use_cases/update_canonical_playlist.py`
> - `src/application/use_cases/update_connector_playlist.py`
> - `src/application/services/playlist_backup_service.py`
> - `src/application/services/connector_playlist_sync_service.py`
> - `src/application/services/connector_playlist_processing_service.py`
> 
> **Infrastructure**
> - `src/infrastructure/persistence/unit_of_work.py`
> - `src/infrastructure/persistence/repositories/base_repo.py`
> - `src/infrastructure/persistence/repositories/playlist/core.py`

> [!question] **Context for Future Developers**
> 
> **Why We Don't Skip Diff Engine:**
> - Diff engine generates correct operations for add/remove track detection
> - Same logic must work for external API synchronization (connector playlists)
> - Bypassing diff would break DRY principle and create maintenance burden
> 
> **Why We Don't Use Simple Reordering:**
> - Need operation counts for metrics/logging (tracks_added, tracks_removed, tracks_moved)
> - External APIs require individual operations for proper error handling
> - Operation-based approach provides better debugging and auditability

---

## ✅ Completed Foundation Work
> [!success] **Phases 1-11 Complete** `#phases-1-11`

### Data Integrity Issues Resolved
- **✅ Duplicate Canonical Mappings**: Fixed 1,119 violations where single connector tracks mapped to multiple canonical tracks
- **✅ Orphaned Records**: Removed 1,145 orphaned connector_tracks, optimized cleanup queries (3000x performance improvement)  
- **✅ Database Constraints**: Added unique constraints to prevent future mapping violations
- **✅ Track Merge Service**: Enhanced with conflict resolution for track_likes and track_metrics

### Architecture Improvements  
- **✅ Track Deduplication**: Fixed repository bypass issues, streamlined to 2 core saving methods
- **✅ Connector Sync Service**: Shared service ensures fresh data consistency across workflows
- **✅ Processing Service**: ConnectorPlaylistProcessingService preserves order, uses track data directly from items
- **✅ Repository Cleanup**: Simplified architecture, restored runtime safety over type perfection

> [!example] **Key Files Created/Enhanced**
> - `scripts/find_duplicate_canonical_mappings.py` - Audit tool for mapping violations
> - `scripts/merge_duplicate_canonical_tracks.py` - Batch cleanup using TrackMergeService  
> - `src/application/services/connector_playlist_sync_service.py` - Shared sync logic
> - `src/application/services/track_merge_service.py` - Enhanced conflict resolution

---

## 📋 Implementation Roadmap

> [!todo] **Current Sprint: Three-Layer Architecture**
> Replace position-based operations with semantic diff analysis + optimal execution strategies

### 🚀 Phase 12: Layer 1 - Semantic Diff Analysis 
> [!info] **Goal**: Replace position-based diff engine with semantic change analysis

**Tasks:**
- [ ] 🔍 **Enhance diff engine** with Longest Increasing Subsequence (LIS) algorithm
- [ ] 🎯 **Separate concerns**: Track identity changes vs position changes  
- [ ] ⚙️ **Generate semantic operations**: Tracks to add/remove + minimal move set
- [ ] 🔄 **Maintain compatibility**: Same interface for existing use cases

### 🎯 Phase 13: Layer 2 - Operation Planning
> [!info] **Goal**: Create execution strategies based on semantic analysis

**Tasks:**
- [ ] 🏗️ **Create planning interface** for different change types
- [ ] ➕ **Simple changes**: Direct add/remove operations  
- [ ] 🔀 **Complex reordering**: LIS-based minimal move set with dependency order
- [ ] 📋 **Output**: Execution plans that avoid position conflicts

### ⚡ Phase 14: Layer 3 - Execution Strategy
> [!info] **Goal**: Separate execution approaches for API vs canonical updates  

**Tasks:**
- [ ] 🔌 **Create execution interface** with API and Canonical strategies
- [ ] 🌐 **API strategy**: Sequential execution with dependency order and error handling
- [ ] 💾 **Canonical strategy**: Atomic state transformation with same operation counts
- [ ] 🔧 **Integration**: Update use cases to use appropriate strategy

### ✅ Phase 15: Testing & Validation  
> [!success] **Status**: `#architecture-complete` - Core issue identified and being resolved

**Implementation Status:**
- ✅ **Layer 1**: Enhanced diff engine with position-aware LIS algorithm
- ✅ **Layer 2**: Created execution strategy framework with API/Canonical strategies  
- ✅ **Layer 3**: Updated use cases to use unified execution strategies
- 🔧 **Critical Bug**: Found and fixing playlist persistence issue for duplicate tracks

> [!bug] **Root Cause Identified** `#duplicate-track-persistence`
> 
> **Issue**: `_manage_playlist_tracks()` in playlist repository updates tracks by `track_id`, breaking playlists with duplicate tracks (same song appearing multiple times).
> 
> **Evidence**:
> - Diff engine correctly calculates 115 operations needed
> - Execution strategy correctly reorders tracks (track 2144 should be at position 63)  
> - Database persistence fails: position 63 ends up with track 2145 instead of 2144
> - Second backup detects incorrect state and generates 115 operations instead of 0
> 
> **Technical Details**:
> ```python
> # BROKEN: Updates all instances of same track to same position
> existing_record = existing_by_track_id[track.id]  
> existing_record.sort_key = sort_key  # Overwrites duplicates!
> ```

### ✅ Phase 16: Fix Duplicate Track Persistence - COMPLETED
> [!success] **Goal**: Fix playlist track updates to handle duplicate tracks correctly

**Tasks:**
- ✅ **Root cause analysis**: Identified track_id-based update logic breaks duplicates
- ✅ **Fix persistence logic**: Replaced with position-based mapping that preserves metadata
- ✅ **Validate fix**: Achieved backup idempotency (0 operations on second run)
- ✅ **Preserve metadata**: Maintained all `added_at` timestamps and track relationships

**Files Modified:**
- ✅ `src/infrastructure/persistence/repositories/playlist/core.py` - Fixed `_manage_playlist_tracks()`
- ✅ `src/domain/playlist/diff_engine.py` - Enhanced with position-aware comparison
- ✅ `src/domain/playlist/execution_strategies.py` - Created unified execution framework

**Test Cases:**
- ✅ **Unchanged playlist**: 0 operations on repeat backup - ACHIEVED!
- ✅ **Position-aware diff**: Position-to-position mapping works correctly
- ✅ **LIS optimization**: Minimal moves using mathematical guarantees
- ✅ **Track mappings**: Connector tracks map correctly to canonical tracks
- ✅ **Metadata preservation**: All `added_at` timestamps and relationships preserved

> [!note] **Final Solution**
> The fix replaced track_id-based updates with position-based mapping:
> 1. **Sort existing records** by `sort_key` to establish position order
> 2. **Map positions 1:1** between current and target playlists  
> 3. **Update only necessary fields** (`track_id`, `sort_key`, `updated_at`)
> 4. **Preserve all metadata** (`added_at`, relationships, etc.)
> 5. **Handle additions/removals** explicitly by track ID comparison
> 
> This ensures each `DBPlaylistTrack` record represents a unique playlist position with preserved metadata, fixing duplicate track handling while maintaining idempotency.

---

## 🎯 Next Steps: Repository Cleanup & Testing

### 🧹 Dead Code Removal (288 → 88 lines)

**Unused Methods to Remove:**
- `get_or_create_many()` + `_get_or_create_many_impl()` (155 lines) - Complex batch logic that no use case actually needs
- `get_or_create()` (20 lines) - Single playlist finder, unused 
- `select_with_relations()` (3 lines) - Trivial wrapper around existing method
- `_RELATIONSHIP_PATHS` constant - Never referenced

**Why Remove:**
- `sync_likes.py` uses `_get_or_create_checkpoint()` (for sync state), not playlist repository methods
- All playlist creation goes through `save_playlist()` or `update_playlist()`
- 52% repository coverage inflated by testing unused code paths

### 📋 Essential Method Test Coverage

**High Priority (used by multiple use cases):**
- `_manage_playlist_tracks()` - Recent duplicate track fix, needs edge case testing
- `_manage_connector_mappings()` - External service sync, currently untested  
- `save_playlist()` / `update_playlist()` - Core persistence, integration tested but need unit tests

**Coverage Gaps:**
- Connector mapping edge cases (missing/invalid external IDs)
- Track persistence error handling (database constraints, transaction failures)
- Playlist metadata updates (name/description changes)

**Test Strategy:**
- Unit tests for individual methods with mocked dependencies
- Integration tests for the fixed `_manage_playlist_tracks()` position-based logic
- Error scenario testing (constraint violations, rollback behavior)