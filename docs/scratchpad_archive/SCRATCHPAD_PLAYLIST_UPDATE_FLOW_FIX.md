# 🎯 Active Work Tracker - Playlist Update Flow Fix

> [!info] Purpose
> This file tracks active development work on fixing the playlist update workflow. The test workflow is completing but producing incorrect results (sorting issues, connector update failures).

**Current Initiative**: Fix End-to-End Playlist Update Test Flow
**Status**: `#in-progress` `#workflow` `#playlist-operations`
**Last Updated**: 2025-09-07

## Progress Overview
- [x] **Fixed URI translation issue** ✅ (Canonical URIs now resolve to Spotify URIs)
- [x] **Fixed source playlist track count issue** ✅ (Updated playlist IDs to use correct ~100 track playlists)
- [x] **Root cause analysis completed** ✅ (Identified architectural confusion between track attributes vs external metrics)
- [ ] **Fix track attribute sorting in transform registry** 🔜 (Current focus - TDD in progress)
- [ ] **Fix connector playlist update persistence failures**
- [ ] **Validate end-to-end workflow**

---

## 🔜 Epic: Track Attribute vs Metric Sorting Architecture `#in-progress`

**Goal**: Enable straightforward sorting by native Track entity attributes (`title`, `album`, `duration_ms`, etc.) without breaking complex external metric sorting system.

**Why**: Currently, simple track attributes like "title" are incorrectly routed through the external metrics system (designed for `lastfm_user_playcount`, `spotify_popularity`, etc.), causing "inf" fallback values when attributes aren't found in external metrics.

**Effort**: M - Requires clean architectural separation at the transform registry layer.

### 🤔 Key Architectural Analysis
> [!important] Two Distinct Sorting Use Cases Being Conflated
> 
> **The Core Problem**: Track attributes and external metrics require fundamentally different handling but are being forced through the same resolution pipeline.
>
> **Track Attributes** (Simple case - should be straightforward):
> - `title`, `album`, `duration_ms`, `release_date`, `artists[0].name`
> - Already present in `Track` entity (`src/domain/entities/track.py`)
> - No external API calls, no caching, no freshness considerations
> - Should be: `lambda track: track.title`
>
> **External Metrics** (Complex case - requires sophisticated handling):
> - `lastfm_user_playcount`, `spotify_popularity`, `lastfm_listeners`
> - Managed by `MetricsApplicationService` (`src/application/services/metrics_application_service.py`)
> - Requires caching, freshness checks, external API calls
> - Uses connector registries (`src/infrastructure/connectors/_shared/metrics.py`)
> - Handled by `EnrichTracksUseCase` (`src/application/use_cases/enrich_tracks.py`)
>
> **Current Broken Flow**:
> 1. Workflow config: `"metric_name": "title"`
> 2. Transform registry: Passes "title" as string to `sort_by_attribute`
> 3. Domain sorting: Looks for "title" in external metrics → not found → `float("inf")`
>
> **Correct Flow Should Be**:
> - **Track attributes**: Transform registry creates `lambda track: track.title`
> - **External metrics**: Transform registry triggers metrics resolution system

### 📝 Implementation Plan (TDD Approach)
> [!note]
> Using Test-Driven Development to fix the architectural separation cleanly.

**Phase 1: Track Attribute Sorting Fix** (ARCHITECTURE PROBLEM IDENTIFIED)
- [x] **Task 1.1**: Write failing tests for track attribute sorting (`tests/unit/domain/test_core_transforms.py`)
- [x] **Task 1.2**: Add track attribute resolver to transform registry (`src/application/workflows/transform_registry.py`)
- [x] **Task 1.3**: Update `"by_metric"` sorter to use attribute resolver for Track properties  
- [ ] **Task 1.4**: ~~Verify tests pass and external metrics still work~~ **REPLACED - ARCHITECTURE FIX NEEDED**

**ARCHITECTURAL CLEANUP PLAN (CURRENT PHASE):**
- **Root Cause**: Domain layer mixing business logic with pure transformations
- **Solution**: Clean separation of concerns with single-purpose functions
- **Goal**: Maintainable, DRY, and testable architecture

**NEW CLEAN ARCHITECTURE:**
- **Three Data Sources**: Track attributes, External metrics, Play history  
- **Three Pure Domain Functions**: One per data source, no mixed concerns
- **Application Layer Orchestration**: Clean routing and business decisions

**Phase 2: Clean Architecture Implementation** ✅ **COMPLETED**
- [x] **Task 2.1**: Create pure `sort_by_external_metrics()` domain function
- [x] **Task 2.2**: Add centralized metric classification system (TRACK_ATTRIBUTES, EXTERNAL_METRICS, PLAY_HISTORY_METRICS)
- [x] **Task 2.3**: Update transform registry with clean routing system
- [x] **Task 2.4**: Remove/deprecate complex `sort_by_attribute()` function
- [x] **Task 2.5**: Clean up stale and redundant code

**Phase 3: Validation and Integration**
- [x] **Task 3.1**: Validate all unit tests pass with new architecture ✅ (ALL 12 SORTING TESTS PASS)
- [ ] **Task 3.2**: Test full workflow integration (title sorting should work) 🔜 **READY TO TEST**
- [ ] **Task 3.3**: Debug any remaining connector playlist update issues
- [ ] **Task 3.4**: Confirm end-to-end playlist update success

## 🎉 **CLEAN ARCHITECTURE SUCCESS**

**What We Accomplished:**
✅ **Eliminated architectural confusion** - No more mixed concerns in domain layer  
✅ **Three pure domain functions** - One per data source, single responsibility  
✅ **Centralized classification** - DRY principle with TRACK_ATTRIBUTES/EXTERNAL_METRICS/PLAY_HISTORY_METRICS  
✅ **Clean separation of concerns** - Application makes decisions, domain executes  
✅ **Removed 116 lines of complex code** - Deleted entire `sort_by_attribute()` function  
✅ **Zero test failures** - All 12 sorting tests pass with new architecture

### ✨ Expected Workflow Results
**Current Broken Behavior**: "inf" values in title sorting, connector playlist update failures
**Expected Correct Behavior**:
1. Load ~100 tracks from source playlist
2. Select first 10 tracks for removal  
3. Filter source to ~90 tracks (remove the 10)
4. Add 10 new tracks from additional playlist = ~100 total
5. Sort all tracks alphabetically by title (no "inf" values)
6. Successfully update Spotify playlist with sorted tracks
7. Report correct metrics and track information

### 🛠️ Key Files and Their Responsibilities

**Core Architecture Files**:
- `src/domain/entities/track.py` - Track entity with native attributes (`title`, `album`, etc.)
- `src/domain/transforms/core.py` - Pure sorting functions (should stay unchanged)
- `src/application/workflows/transform_registry.py` - **FIX HERE** - Strategy configuration layer
- `src/application/workflows/node_catalog.py` - Node type registrations

**External Metrics System** (Complex case - keep working as-is):
- `src/application/services/metrics_application_service.py` - Metrics lifecycle management
- `src/application/use_cases/enrich_tracks.py` - External metadata enrichment
- `src/infrastructure/connectors/_shared/metrics.py` - Connector metric registries

**Workflow Execution**:
- `src/application/workflows/definitions/test_playlist_update.json` - Test workflow definition
- `src/application/workflows/node_factories.py` - Node creation logic
- `src/application/workflows/destination_nodes.py` - Playlist update operations

**Testing Files**:
- `tests/unit/domain/test_core_transforms.py` - **TDD tests written** - Track attribute sorting
- Need integration tests for full workflow

### 🎯 **Clean Architectural Principles**

> **Single Responsibility**: Each domain function handles exactly one data source
> 
> **Separation of Concerns**: Application layer makes decisions, domain layer executes
> 
> **DRY Principle**: Centralized metric classification, no duplicated routing logic
> 
> **Clean Boundaries**: No business logic in domain, no implementation details in application

### 🧹 **Code Cleanup Plan**

**Functions to Remove/Deprecate:**
- `sort_by_attribute()` - Replace with three specialized functions
- `_extract_track_metric()` - No longer needed with clean separation
- Complex conditional logic in transform registry - Replace with classification system

**New Clean Architecture:**
```python
# Domain Layer - Pure functions
sort_by_key_function()      # Track attributes (title, album, etc.)
sort_by_external_metrics()  # External metrics (spotify_popularity, lastfm_user_playcount)  
sort_by_play_history()      # Play history (total_plays, last_played_date)

# Application Layer - Business routing
TRACK_ATTRIBUTES = {"title", "album", "release_date", "duration_ms", "artist"}
EXTERNAL_METRICS = {"spotify_popularity", "lastfm_user_playcount", "lastfm_listeners"}
PLAY_HISTORY_METRICS = {"total_plays", "plays_last_30_days", "last_played_date"}
```

**Result**: Clean, maintainable, testable code with proper separation of concerns.