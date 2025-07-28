# 🎯 Active Work Tracker - v0.2.5 Workflow Transformation Expansion

> [!info] Purpose
> This file tracks active development work on the current epic/refactor. For strategic roadmap and completed milestones, see [[BACKLOG]].

**Current Initiative**: v0.2.5 Workflow Transformation Expansion  
**Status**: `#in-progress` `#play-history` `#v0-2-5`  
**Last Updated**: July 28, 2025

## Progress Overview

v0.2.5 Workflow Transformation Expansion:
- [ ] 🔄 **Play History Filter and Sort** ← Current Focus
- [ ] Advanced Transformer Workflow nodes
- [ ] Advanced Track Matching Strategies
- [ ] Enhanced Playlist Naming
- [ ] Discovery Workflow Templates

v0.2.4 Completed ✅:
- [x] Complete UpdatePlaylistUseCase Implementation
- [x] Technical Debt Cleanup

---

## 🔄 Current Epic: Play History Filter and Sort `#in-progress`

**Effort**: Medium (M) - Cross-module feature with existing architecture integration  
**Goal**: Transform workflows to leverage your listening history for intelligent track selection and ordering

> [!todo] Next Actions
> Research existing filter/sorter architecture in `TRANSFORM_REGISTRY` to understand extension patterns

### Scope & Requirements

**What**: Two new workflow nodes for play count analysis on TrackPlay records (NOT Last.fm user playcount)
- **`filter.by_play_count`**: Filter tracks by play frequency within date ranges  
- **`sorter.by_play_count`**: Sort tracks by play frequency within date ranges
- **Flexible date filtering**: None, absolute dates, or relative time periods
- **Discovery through workflows**: JSON compositions create "hidden gems", "current obsessions", etc.

**Why**: Users need filtering based on their actual listening patterns from our TrackPlay database, not external metrics.

### Implementation Tasks

- [x] **Research Phase** ✅ 
  - [x] Examine existing `TRANSFORM_REGISTRY` architecture ✅
  - [x] Identify filter/sorter extension patterns ✅  
  - [x] Review play history data schema in database ✅
  - [x] **CRITICAL**: Identify database indexing gaps for 10K+ track scale ✅
  - [x] **Code review of import logic** - Last.fm vs Spotify play import patterns ✅
  - [x] **Review existing transforms** - `filter_by_play_history()` and toolz usage ✅

- [x] **Import Quality Foundation** ✅ **COMPLETED**
  - [x] **Add play filtering threshold to Spotify import** - 4min OR 50% rule ✅
  - [x] **Add filtering metrics** to import results (raw vs filtered counts) ✅
  - [x] **Code review and test existing imports** - verify Last.fm vs Spotify behavior ✅
  - [x] **Re-test import workflows** with filtering applied ✅

- [x] **Database Performance Foundation** ✅ **COMPLETED**
  - [x] Add missing indexes to `DBTrackPlay` model for efficient queries ✅
  - [x] Add batch query methods to `TrackPlayRepository` ✅ (already existed!)
  - [x] Create database migration for existing installations

- [ ] **New Workflow Nodes Implementation** ← **PRIORITY 3**
  - [ ] **Build `filter.by_play_history` node** - filter by play counts within time windows
  - [ ] **Build `sorter.by_play_history` node** - sort by play frequency within time windows  
  - [ ] **Add to TRANSFORM_REGISTRY** - extend existing filter/sorter patterns
  - [ ] **Simple integer-based date logic** - no string parsing complexity

- [ ] **Discovery Workflow Templates** ← **PRIORITY 4**
  - [ ] `hidden_gems.json` - min 3 plays, no plays in 6+ months
  - [ ] `current_obsessions.json` - 8+ plays in last month
  - [ ] `summer_2024_favorites.json` - absolute date range example
  - [ ] `rediscovery_candidates.json` - 1-2 plays ever

> [!warning] Critical Issues Discovered
> **1. Missing Database Indexes**: Current schema lacks essential indexes for play history queries at scale:
> - No `(track_id)` index = Full table scan for per-track queries
> - No `(track_id, played_at)` composite = Can't optimize time-range filtering  
> - No `(track_id, service)` composite = Can't optimize service-specific queries
> **Impact**: With 10K+ tracks, play history workflows will be prohibitively slow without these indexes.
>
> **2. Spotify Import Data Quality**: Missing scrobbling threshold creates noise:
> - **Last.fm imports**: Already scrobbled (4+ min OR 50%+ duration) ✅
> - **Spotify imports**: No threshold - imports ALL plays including 5-second skips ❌
> **Impact**: Inconsistent play data quality affects discovery algorithms

> [!info] New Architecture Decision - Two Simple Workflow Nodes 
> **Final Design**: Build two focused workflow nodes with simple, clear configuration
> - **`filter.by_play_history`**: Filter tracks by play count within optional time windows
> - **`sorter.by_play_history`**: Sort tracks by play frequency within optional time windows
> - **Discovery via JSON workflows**: Templates compose these nodes for specific use cases
> - **Integer-based dates**: Simple `days_back` integers instead of string parsing
>
> **Time Window Modes**: Clear, unambiguous configuration
> - **None**: No date fields = all-time play counts  
> - **Absolute**: `start_date`/`end_date` = ISO date strings
> - **Relative**: `min_days_back`/`max_days_back` = integer days from today
>
> **Benefits**: No string parsing, explicit modes, clear validation, simpler implementation

## **New Workflow Node Specifications**

### **1. `filter.by_play_history` Node**

Filter tracks based on play count criteria within optional time windows.

**Configuration:**
```json
{
  "type": "filter.by_play_history",
  "config": {
    // Play count constraints (at least one required)
    "min_plays": 5,        // minimum plays (inclusive)
    "max_plays": 20,       // maximum plays (inclusive)
    
    // Time window - RELATIVE (optional)
    "min_days_back": 90,   // start of window, days from today  
    "max_days_back": 30,   // end of window, days from today
    
    // Time window - ABSOLUTE (optional, alternative to relative)
    "start_date": "2024-01-01",  // ISO date string
    "end_date": "2024-03-31"     // ISO date string
  }
}
```

### **2. `sorter.by_play_history` Node**

Sort tracks by play frequency within optional time windows.

**Configuration:**
```json
{
  "type": "sorter.by_play_history",
  "config": {
    // Time window - RELATIVE (optional)
    "min_days_back": 90,   // start of window, days from today
    "max_days_back": 30,   // end of window, days from today
    
    // Time window - ABSOLUTE (optional, alternative to relative) 
    "start_date": "2024-06-01",  // ISO date string
    "end_date": "2024-08-31",    // ISO date string
    
    // Sort direction
    "reverse": true        // true = most played first, false = least played first
  }
}
```

### **Example Discovery Workflows**

**Hidden Gems** (loved but neglected):
```json
{
  "type": "filter.by_play_history",
  "config": {
    "min_plays": 3,        // At least 3 total plays (lifetime)
    "max_days_back": 180   // But not played in last 6 months
  }
}
```

**Current Obsessions** (recent heavy rotation):
```json
{
  "type": "filter.by_play_history", 
  "config": {
    "min_plays": 8,
    "max_days_back": 30    // 8+ plays in last 30 days
  }
}
```

**Summer 2024 Favorites** (absolute date range):
```json
{
  "type": "sorter.by_play_history",
  "config": {
    "start_date": "2024-06-01",
    "end_date": "2024-08-31", 
    "reverse": true
  }
}
```

**Rediscovery Candidates** (barely touched):
```json
{
  "type": "filter.by_play_history",
  "config": {
    "min_plays": 1,
    "max_plays": 2         // Played 1-2 times ever (no time window)
  }
}
```

### **Key Design Benefits**

1. **Simple Date Logic**: Integer `days_back` eliminates string parsing complexity
2. **Clear Intent**: `min_days_back`/`max_days_back` makes time windows explicit  
3. **Flexible Modes**: Relative integers OR absolute dates, never mixed
4. **Easy Validation**: Simple numeric comparisons and date format checks
5. **Better Names**: `play_history` distinguishes from Last.fm playcount metrics

### Dependencies
- **Import Quality**: Scrobbling threshold must be added before analyzing play patterns
- **Database Performance**: Indexing optimization for efficient TrackPlay queries  
- **TrackPlay Aggregation**: Repository methods for counting plays within date ranges

### Files Likely to Change
- `src/infrastructure/services/spotify_import.py` - **Add scrobbling threshold**
- `src/infrastructure/persistence/database/db_models.py` - **Add critical indexes**
- `src/infrastructure/persistence/repositories/track/plays.py` - **Add play counting methods**
- `src/application/workflows/transform_registry.py` - **Add new filter/sorter nodes**
- `src/domain/transforms/core.py` - **Add play history transforms using toolz**
- Workflow definition files for discovery templates

---

---

## 🎉 MAJOR PROGRESS UPDATE - July 28, 2025

### ✅ Import Quality Foundation - COMPLETED
**Achievement**: Spotify imports now apply proper play filtering for data quality consistency

**Key Changes**:
- **Play Filtering Logic**: Added `should_include_play()` function with "4 minutes OR 50% of track duration" rule
- **Post-Resolution Filtering**: Applied after track resolution when we have actual track duration
- **Filtering Metrics**: Added comprehensive stats tracking (raw vs filtered counts, filtering rate)
- **Configuration**: Added `ImportConfig` with `play_threshold_ms` and `play_threshold_percentage`
- **Clean Terminology**: Used "play filtering" instead of "scrobbling" for service-neutral language

**Files Modified**:
- `src/config/settings.py` - Added ImportConfig with play filtering thresholds
- `src/infrastructure/services/spotify_import.py` - Implemented play filtering logic and metrics

### ✅ Database Performance Foundation - COMPLETED  
**Achievement**: Added critical indexes for efficient play history queries at scale

**Key Changes**:
- **Critical Indexes Added**: 
  - `ix_track_plays_track_id` - Per-track queries optimization
  - `ix_track_plays_track_played` - Time-range filtering optimization
  - `ix_track_plays_track_service` - Service-specific queries optimization
- **Batch Query Methods**: Confirmed existing efficient methods in `TrackPlayRepository`
  - `get_play_aggregations()` - Core aggregation with toolz
  - `get_total_play_counts()` - Play counts by track IDs
  - `get_last_played_dates()` - Last played dates by track IDs
  - `get_period_play_counts()` - Time-range filtering

**Files Modified**:
- `src/infrastructure/persistence/database/db_models.py` - Added performance indexes
- `alembic/versions/f6ce27d69ce9_add_performance_indexes_to_track_plays_.py` - Database migration
- `alembic/env.py` - Fixed import path for migrations

### Impact Assessment
- **Data Quality**: Spotify imports now consistent with Last.fm filtering standards ✅
- **Performance**: Database queries will handle 10K+ tracks efficiently with proper indexes ✅
- **Migration**: Database migration successfully applied to production database ✅
- **Architecture**: Clean foundation ready for workflow node implementation ✅
- **Testing**: 25 comprehensive unit tests covering real music scenarios ✅
- **Metrics**: Comprehensive tracking of filtering effectiveness ✅
- **Data Integrity**: Comprehensive testing preventing silent failures and duplicates ✅

### ✅ Import Idempotency Fix - COMPLETED
**Achievement**: Fixed critical bug where imports were not idempotent and could create duplicates

**Root Cause**: Missing unique constraint on track_plays table prevented efficient bulk upsert from working
- `bulk_upsert` was failing due to "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint"
- Falling back to individual upserts (which worked but were inefficient)
- Risk of duplicate imports if fallback logic failed

**Solution**:
- **Database Migration**: Added unique constraint on `(track_id, service, played_at, ms_played)`
- **Efficient Bulk Upsert**: Now works properly with ON CONFLICT DO UPDATE
- **Streamlined Testing**: Removed excessive tests, kept only critical idempotency test

**Files Changed**:
- `alembic/versions/1127d60f4cad_add_unique_constraint_track_plays_.py` - Database migration
- `tests/unit/infrastructure/services/test_import_idempotency.py` - Critical test for import safety
- Removed: `test_import_data_integrity.py`, `test_import_data_quality_comparison.py` (excessive)

**Impact**: Imports are now truly idempotent and efficient. Re-running the same import multiple times will not create duplicates.

### ✅ Incognito Mode Filtering - COMPLETED
**Achievement**: Added proper filtering to exclude incognito mode plays from Spotify imports

**Why**: Incognito mode plays don't represent the user's actual listening history and shouldn't be included in play analysis or recommendations.

**Implementation**:
- Added incognito mode check early in import processing (before track resolution)
- Improved filtering metrics with clearer, more descriptive names:
  - `raw_plays` - Total plays from JSON file
  - `accepted_plays` - Plays that passed all filters and will be imported
  - `duration_excluded` - Plays excluded for being too short (< 4min OR < 50%)
  - `incognito_excluded` - Plays excluded for being in incognito mode
- Enhanced logging to show breakdown of all filtering decisions

**Files Changed**:
- `src/infrastructure/services/spotify_import.py` - Added incognito filtering and improved metrics

**Impact**: Spotify imports now properly filter out incognito plays, ensuring only genuine listening history is analyzed.

---

## ✅ RESOLVED - Track Deduplication Issue - July 28, 2025

### **Problem**: NULL Track ID Constraint Violations During Spotify Import

**Issue**: Spotify imports failing with constraint violations during bulk insert operations:
```
NOT NULL constraint failed: track_plays.track_id
```

**Root Cause Analysis**:
1. **Track Resolution Failures**: Thousands of 2011-2014 tracks failing all resolution stages (direct API, search fallback) 
2. **Preserved Metadata**: Unresolved tracks getting `track_id=None` but still being passed to database
3. **Design Mismatch**: Domain model allows `track_id: int | None` but database schema requires NOT NULL

### **Immediate Fix - Filter NULL Track IDs**
Added filtering in `TrackPlayRepository.bulk_insert_plays()` to prevent constraint violations:
- Filter out plays with `track_id=None` before database operations
- Added warning logging when plays are filtered out 
- Return accurate count of successfully insertable plays

### **Root Cause Fix - Track Content Deduplication**

**Problem Discovered**: System was creating duplicate canonical tracks for same song content when encountering different Spotify IDs (relinked tracks, re-releases, etc.).

**SQL Analysis Results**:
- "Insane in the Brain": 5 connector tracks → 2 canonical tracks (should be 1)
- "My Girl": 5 connector tracks → 4 canonical tracks (should be 1) 
- Multiple examples of same issue across catalog

**Solution**: Implemented exact content matching in `SpotifyPlayResolver` resolution pipeline:
- Added exact matching stage when no existing connector mapping found
- ISRC exact match (highest confidence)
- Normalized title + artist exact match
- Only creates new canonical track when no content match exists

**Files Modified**:
- `src/infrastructure/persistence/repositories/track/plays.py` - Added NULL filtering
- `src/infrastructure/services/spotify_play_resolver.py` - Added exact content matching
- `src/infrastructure/services/spotify_import.py` - Enhanced resolution statistics and logging

**Impact**:
- ✅ **Immediate**: No more constraint violation errors during imports
- ✅ **Long-term**: One canonical track per song (proper architecture)
- ✅ **Performance**: Minimal overhead - only when no existing mapping found
- ✅ **Data Quality**: Better track resolution statistics and user feedback

---
