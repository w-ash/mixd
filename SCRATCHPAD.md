# 🎯 Active Work Tracker - Spotify Import Deduplication Fix

> [!info] Purpose
> This file tracks active development work on the current epic. For strategic roadmap and completed milestones, see [[BACKLOG.md]].

**Current Initiative**: Fix Spotify Import Filtering & Deduplication  
**Status**: `#completed` `#infrastructure` `#v1.x`  
**Last Updated**: 2025-01-31

## Progress Overview
- [x] **Fix Play Filtering Logic** ✅ (COMPLETED - Fixed threshold logic)
- [x] **Fix Persistence Layer Bug** ✅ (COMPLETED - Parameter name mismatch)  
- [x] **Fix Deduplication Logic** ✅ (COMPLETED - All issues resolved)
- [x] **Fix SQLite Query Limits** ✅ (COMPLETED - Expression tree batching)
- [x] **Enhanced Import Statistics** ✅ (COMPLETED - Separated filtering vs duplicates)
- [x] **Track Canonical Metrics** ✅ (COMPLETED - New vs existing tracks)

---

## 🔜 COMPLETED: Spotify Import Core Fixes `#completed`

**Goal**: Fix broken Spotify import system where legitimate plays were filtered out and nothing was persisting to database
**Why**: Users reported imports showing "None imported" despite processing thousands of plays
**Effort**: M - Required debugging complex pipeline with multiple failure points

### 🤔 Key Architectural Decision
> [!important] Multi-Layer Defense Strategy
> **Key Insight**: Found two critical bugs: (1) Inverted filtering logic using `min()` instead of proper 4min+ threshold, (2) Parameter name mismatch `_raw_data` vs `raw_data` causing template method failure
>
> **Chosen Approach**: Fix both issues while maintaining clean architecture boundaries and adding comprehensive logging for debugging
>
> **Rationale**:
> - **Clean Architecture**: All fixes in infrastructure layer, no application changes needed
> - **Defense in Depth**: Application-level deduplication + database constraints + enhanced logging
> - **DRY Compliance**: Leverages existing `bulk_upsert()` and helper patterns

### 📝 Implementation Plan

**Phase 1: Fix Filtering Logic** ✅ COMPLETED
- [x] **Task 1.1**: Replace `min()` with correct 4min+ always include logic
- [x] **Task 1.2**: Add warning logging for missing duration info
- [x] **Task 1.3**: Update filtering tests to match corrected logic

**Phase 2: Fix Persistence Layer** ✅ COMPLETED  
- [x] **Task 2.1**: Debug why 4396 plays created but None imported
- [x] **Task 2.2**: Found parameter mismatch in `_handle_checkpoints()` 
- [x] **Task 2.3**: Fixed `_raw_data` -> `raw_data` parameter name

**Phase 3: Add Application-Level Deduplication** 🔄 IN PROGRESS
- [x] **Task 3.1**: Add `_find_existing_plays()` helper to query existing records
- [x] **Task 3.2**: Add `_filter_duplicates()` helper to remove duplicates  
- [x] **Task 3.3**: Fix timezone normalization bug (timezone-aware vs naive comparison) ✅ COMPLETED
- [x] **Task 3.4**: Test complete deduplication flow works correctly ✅ COMPLETED

**Phase 4: Database Safety Net** ✅ COMPLETED
- [x] **Task 4.1**: Add UNIQUE constraint on `(track_id, service, played_at, ms_played)` ✅
- [x] **Task 4.2**: Applied existing migration `1127d60f4cad` ✅  
- [x] **Task 4.3**: Verified `bulk_upsert()` lookup keys match constraint columns ✅

**Phase 5: Fix SQLite Expression Tree Limit** ✅ COMPLETED
- [x] **Task 5.1**: Identified root cause - massive OR queries in `_find_existing_plays()` ✅
- [x] **Task 5.2**: Implement batching using toolz `partition_all` (follows CLAUDE.md patterns) ✅
- [x] **Task 5.3**: Test large file import works without SQL query limits ✅
- [x] **Task 5.4**: Verify deduplication accuracy maintained across batched queries ✅

**Phase 6: Enhanced Import Statistics** ✅ COMPLETED
- [x] **Task 6.1**: Replace `skipped_count` with `filtered_count` + `duplicate_count` ✅
- [x] **Task 6.2**: Update repository layer to return tuple `(inserted, duplicates)` ✅
- [x] **Task 6.3**: Aggregate filtering stats in Spotify adapter ✅
- [x] **Task 6.4**: Update UI to show separate "Filtered (Too Short)" and "Filtered (Duplicates)" ✅

**Phase 7: Track Canonical Metrics** ✅ COMPLETED
- [x] **Task 7.1**: Track new canonical tracks created during import ✅
- [x] **Task 7.2**: Track existing canonical tracks with new plays added ✅
- [x] **Task 7.3**: Display "New Tracks: X, Updated Tracks: Y" in UI ✅
- [x] **Task 7.4**: Minimal code addition following clean architecture ✅

### ✨ User-Facing Changes & Examples

**Before (Broken)**:
```
Spotify Import
  Plays Processed    8370
  Tracks Affected    4396  
  Imported           None  ❌
  Success Rate       0%
```

**After (Enhanced)**:
```
Spotify Import  
  Plays Processed      8370
  Plays Saved          4396  ✅ (Clear naming)
  Imported             4396  ✅ (Actually works)
  Filtered (Too Short) 3850  ✅ (Expected behavior)
  Filtered (Duplicates)  124  ✅ (Deduplication working)
  Success Rate         52.5%
```

**After (Final Enhancement)**:
```
Spotify Import  
  Plays Processed      8370
  Plays Saved          4396  ✅
  Imported             4396  ✅  
  New Tracks             89  ✅ (Canonical tracks created)
  Updated Tracks       1205  ✅ (Existing tracks with new plays)
  Filtered (Too Short) 3850  ✅
  Filtered (Duplicates)  124  ✅
  Success Rate         52.5%
```

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**: `operations.py` - added canonical track fields to `OperationResult`
- **Application**: `results.py` - added canonical track fields to `ImportResultData` + `ResultFactory`
- **Infrastructure**: `spotify_play_adapter.py` filtering logic + zero-overhead canonical tracking, `plays.py` deduplication, `spotify_import_service.py` parameter fix + canonical metrics aggregation
- **Interface**: `ui.py` renamed "Tracks Affected" → "Plays Saved" + display "New Tracks" / "Updated Tracks"

**Latest Issue - SQLite Expression Tree Limit**:
```sql
-- Problem: 4396 plays = 4396 OR conditions in single query
SELECT * FROM track_plays WHERE 
  (track_id = ? AND service = ? AND played_at = ? AND ms_played = ?) OR
  (track_id = ? AND service = ? AND played_at = ? AND ms_played = ?) OR
  ... (4394 more OR conditions) ...
  
-- SQLite limit: Maximum expression tree depth = 1000
-- Our query depth: ~4396 (EXCEEDS LIMIT!)
```

**Solution - Clean Architecture Batching**:
```python
# Use existing toolz patterns (CLAUDE.md guidance)
from toolz import partition_all

# Batch into chunks of 200 (well under 1000 limit)
for batch in partition_all(200, plays):
    # Process each batch separately
    existing_keys.update(await self._query_batch(batch))
```

**Testing Strategy**:
- **Unit**: Filtering logic tests updated and passing
- **Integration**: Small file (2 plays) working, large file (8370 plays) working
- **E2E/Workflow**: Re-import same file should show 0 new plays (currently failing due to timezone bug)

**Key Files Modified**:
- `src/infrastructure/adapters/spotify_play_adapter.py` - Fixed filtering logic + return filtering stats
- `src/infrastructure/services/spotify_import_service.py` - Fixed parameter mismatch + aggregate stats
- `src/infrastructure/services/base_play_importer.py` - Updated to handle duplicate counts
- `src/infrastructure/persistence/repositories/track/plays.py` - Added deduplication + batching logic
- `src/infrastructure/persistence/database/db_models.py` - Added unique constraint
- `src/domain/entities/operations.py` - Added `filtered_count` + `duplicate_count` fields
- `src/domain/repositories/interfaces.py` - Updated protocol to return tuple
- `src/application/utilities/results.py` - Enhanced `ImportResultData` structure
- `src/interface/shared/ui.py` - Enhanced display with separate filter categories
- `alembic/versions/1127d60f4cad_add_unique_constraint_track_plays_.py` - Applied migration
- `scripts/cleanup_plays.py` - Database cleanup utility
- `tests/unit/infrastructure/services/test_spotify_import_filtering.py` - Updated tests

**Architecture Compliance**:
- ✅ **Clean Architecture**: All fixes remain in infrastructure layer
- ✅ **DRY Principle**: Leveraging existing toolz patterns and base repository methods
- ✅ **Defensive Programming**: Application-level deduplication + database constraint safety net

**Status**: ✅ **COMPLETED** - All Major Issues Resolved + Enhanced Metrics Added

**Completed Work**:
1. [x] **Fixed timezone normalization** in deduplication comparison ✅ 
2. [x] **Tested complete deduplication flow** works end-to-end ✅ 
3. [x] **Cleaned up excessive debug logging** ✅ 
4. [x] **Added database unique constraint** for safety net ✅
5. [x] **Applied migration** to add `uq_track_plays_deduplication` constraint ✅
6. [x] **Cleaned corrupted plays** from database for fresh testing ✅
7. [x] **Fixed SQLite expression tree limit** using toolz `partition_all` batching ✅
8. [x] **Enhanced metrics** - distinguish filtered vs duplicate plays ✅
9. [x] **Track canonical metrics** - zero-overhead new vs existing tracks insight ✅

**Latest Enhancement - Detailed Import Statistics**:
Replaced unclear "Skipped" with meaningful breakdown:
```
# Before (unclear)
Skipped: 3974  (❓ Why were these skipped?)

# After (actionable)
Filtered (Too Short): 3850  (✅ Working correctly)
Filtered (Duplicates): 124   (✅ No redundant data)
```

**Final Enhancement - Track Canonical Metrics**:
9. ✅ **Show new vs existing canonical tracks** 

**Goal**: Provide insight into data growth vs enrichment ✅ ACHIEVED
- **New Tracks**: How many canonical tracks were created (new music discovered) ✅
- **Updated Tracks**: How many existing canonical tracks got new plays added ✅

**Implementation Strategy (KISS + DRY + Clean Architecture)** ✅ EXECUTED:
- **Infrastructure**: Track stats during `process_records()` - zero overhead, reuse existing logic ✅
- **Application**: Add `new_tracks_count` + `updated_tracks_count` to `ImportResultData` + `OperationResult` ✅ 
- **Interface**: Display both counts with clear labels ✅
- **Clean Architecture**: All changes flow through proper layer boundaries ✅

**Benefits** ✅ DELIVERED:
- **User Insight**: "Did I discover new music (high new tracks) or just re-listen (high updated tracks)?" ✅
- **Data Quality**: Track canonical track creation patterns over time ✅
- **Zero Performance Impact**: Leverage existing track resolution logic, no additional queries ✅

**Files Modified for Canonical Metrics**:
- `src/domain/entities/operations.py` - Added `new_tracks_count` + `updated_tracks_count` fields
- `src/application/utilities/results.py` - Added fields to `ImportResultData` + `ResultFactory`
- `src/infrastructure/adapters/spotify_play_adapter.py` - Zero-overhead counting in existing loops
- `src/infrastructure/services/spotify_import_service.py` - Metrics aggregation across batches  
- `src/interface/shared/ui.py` - Display "New Tracks" / "Updated Tracks" in import results

**Status**: ✅ **FULLY COMPLETED** - All Spotify Import Enhancements Complete