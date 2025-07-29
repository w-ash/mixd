# 🎯 Active Work Tracker - v0.2.5 Workflow Transformation Expansion

> [!info] Purpose
> This file tracks active development work on the current epic/refactor. For strategic roadmap and completed milestones, see [[BACKLOG]].

**Current Initiative**: v0.2.5 Workflow Transformation Expansion  
**Status**: `#completed` `#play-history` `#v0-2-5`  
**Last Updated**: July 29, 2025

## Progress Overview

v0.2.5 Workflow Transformation Expansion:
- [x] **Foundation Work** ✅ (Import Quality, Database Performance, Bug Fixes)
- [x] **Play History Filter and Sort** ✅ (Complete - Both filter and sorter nodes implemented)
- [x] **Discovery Workflow Templates** ✅ (Complete - All 4 templates created)

v0.2.6 Planned (moved from v0.2.5):
- [ ] Advanced Transformer Workflow nodes
- [ ] Advanced Track Matching Strategies  
- [ ] Enhanced Playlist Naming

---

## ✅ COMPLETED Epic: Play History Filter and Sort `#completed`

**Effort**: Medium (M) - Cross-module feature with existing architecture integration  
**Goal**: Transform workflows to leverage your listening history for intelligent track selection and ordering

> [!todo] Next Actions
> 1. Implement `filter.by_play_history` workflow node
> 2. Implement `sorter.by_play_history` workflow node
> 3. Create discovery workflow templates

### Scope & Requirements

**What**: Two new workflow nodes for play count analysis on TrackPlay records (NOT Last.fm user playcount)
- **`filter.by_play_history`**: Filter tracks by play count within optional time windows  
- **`sorter.by_play_history`**: Sort tracks by play frequency within optional time windows
- **Flexible date filtering**: None, absolute dates, or relative time periods
- **Discovery through workflows**: JSON compositions create "hidden gems", "current obsessions", etc.

**Why**: Users need filtering based on their actual listening patterns from our TrackPlay database, not external metrics.

### Implementation Status

✅ **COMPLETED - Foundation Work** (All prerequisite work finished)
- [x] **Research Phase** - Architecture analysis and pattern identification
- [x] **Import Quality Foundation** - Play filtering thresholds and metrics
- [x] **Database Performance Foundation** - Critical indexes for play history queries  
- [x] **Import Reliability Fixes** - Idempotency and deduplication issues resolved

✅ **COMPLETED - Workflow Node Implementation**
- [x] **Build `filter.by_play_history` node** - filter by play counts within time windows
- [x] **Build `sorter.by_play_history` node** - sort by play frequency within time windows  
- [x] **Add to TRANSFORM_REGISTRY** - extend existing filter/sorter patterns
- [x] **Simple integer-based date logic** - no string parsing complexity

✅ **COMPLETED - Discovery Templates**
- [x] `hidden_gems.json` - min 3 plays, no plays in 6+ months
- [x] `current_obsessions.json` - 8+ plays in last month
- [x] `summer_2024_favorites.json` - absolute date range example
- [x] `rediscovery_candidates.json` - 1-2 plays ever

## **New Workflow Node Specifications**

> [!info] Architecture Decision - Two Simple Workflow Nodes 
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

### Files to Modify (Next Steps)
- `src/application/workflows/transform_registry.py` - **Add new filter/sorter nodes**
- `src/domain/transforms/core.py` - **Add play history transforms using toolz**
- Workflow definition files for discovery templates

### Architecture Patterns (Reference)
- Extend existing filter/sorter patterns in `TRANSFORM_REGISTRY`
- Use toolz functional patterns for play history aggregation
- Repository methods for counting plays within date ranges already exist

---

## ✅ Foundation Work Completed

All prerequisite work for implementing play history workflow nodes has been completed:

### Primary Connector Mapping Foundation ✅ **COMPLETED**
- **Schema Enhancement**: Added `is_primary` flag and `connector_name` to `track_mappings` table
- **Migration Success**: Processed 33,742 mappings, created 32,163 primary mappings with perfect 1:1 ratio
- **Spotify Relinking**: Integrated automatic primary mapping updates when Spotify relinks tracks
- **Data Integrity**: Database constraint enforces one primary per track-connector pair
- **Performance**: Added optimized indexes for mapping lookups and primary queries

**Files**: `src/infrastructure/persistence/database/db_models.py`, migration `520a98e0da93`, `src/infrastructure/services/spotify_play_resolver.py`

### Import Quality Foundation
- **Play Filtering**: Added "4 minutes OR 50% duration" rule to Spotify imports for consistency with Last.fm
- **Incognito Filtering**: Exclude incognito mode plays from imports
- **Configuration**: Added `ImportConfig` with `play_threshold_ms` and `play_threshold_percentage`
- **Metrics**: Track raw vs filtered play counts for import visibility

**Files**: `src/config/settings.py`, `src/infrastructure/services/spotify_import.py`

### Database Performance Foundation  
- **Indexes**: Added `ix_track_plays_track_id`, `ix_track_plays_track_played`, `ix_track_plays_track_service`
- **Migration**: `f6ce27d69ce9_add_performance_indexes_to_track_plays_.py`
- **Repository**: Confirmed existing efficient batch query methods in `TrackPlayRepository`

**Files**: `src/infrastructure/persistence/database/db_models.py`, `alembic/env.py`

### Import Reliability Fixes
- **Import Idempotency**: Added unique constraint on `(track_id, service, played_at, ms_played)`
- **Track Deduplication**: Added NULL filtering in repository and exact content matching in resolver
- **Content Matching**: ISRC exact match + normalized title/artist exact match

**Migrations**: `1127d60f4cad_add_unique_constraint_track_plays_.py`  
**Files**: `src/infrastructure/persistence/repositories/track/plays.py`, `src/infrastructure/services/spotify_play_resolver.py`

---
