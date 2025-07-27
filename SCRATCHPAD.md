# 🎯 Active Work Tracker - v0.2.4 Playlist Workflow Expansion

> [!info] Purpose
> This file tracks active development work on the current epic/refactor. For strategic roadmap and completed milestones, see [[BACKLOG]].

**Current Initiative**: v0.2.4 Playlist Workflow Expansion  
**Status**: `#in-progress` `#play-history` `#v0-2-4`  
**Last Updated**: July 27, 2025

## Progress Overview

v0.2.4 Playlist Workflow Expansion:
- [x] ✅ Complete UpdatePlaylistUseCase Implementation
- [x] ✅ Technical Debt Cleanup  
- [ ] 🔄 **Play History Filter and Sort** ← Current Focus
- [ ] Advanced Transformer Workflow nodes
- [ ] Advanced Track Matching Strategies
- [ ] Enhanced Playlist Naming
- [ ] Discovery Workflow Templates

---

## 🔄 Current Epic: Play History Filter and Sort `#in-progress`

**Effort**: Medium (M) - Cross-module feature with existing architecture integration  
**Goal**: Extend existing filter and sorter node categories to support play history metrics

> [!todo] Next Actions
> Research existing filter/sorter architecture in `TRANSFORM_REGISTRY` to understand extension patterns

### Scope & Requirements

**What**: Enable granular control over finding tracks based on listening behavior
- Frequently/rarely played tracks
- Seasonal patterns  
- Discovery gaps
- Listening recency for advanced playlist curation

**Why**: Users need sophisticated filtering based on their actual listening patterns, not just external metrics like popularity.

### Implementation Tasks

- [ ] **Research Phase**
  - [ ] Examine existing `TRANSFORM_REGISTRY` architecture
  - [ ] Identify filter/sorter extension patterns
  - [ ] Review play history data schema in database

- [ ] **Core Filtering Features**
  - [ ] Play count filtering (e.g., tracks played >10 times, <5 times)
  - [ ] Time-period analysis (e.g., tracks played >5 times in July 2024)
  - [ ] Relative time periods (last 30 days, past week, this month)

- [ ] **Sorting Features**  
  - [ ] Play recency sorting (most/least recently played)
  - [ ] Play frequency sorting within time windows
  - [ ] Discovery gap identification (tracks not played recently)

- [ ] **Integration**
  - [ ] Build on existing metric-based filtering patterns
  - [ ] Ensure compatibility with current workflow node architecture
  - [ ] Add comprehensive test coverage

> [!info] Architecture Decision
> Leverage existing filter/sorter architecture in `TRANSFORM_REGISTRY` rather than creating new node types. This maintains consistency with current workflow patterns.

### Dependencies
- None (standalone feature building on existing infrastructure)

### Files Likely to Change
- `src/application/workflows/transform_registry.py`
- `src/domain/transforms/` (new play history transforms)
- Workflow definition files for testing

---

## ✅ Recently Completed Work

### UpdatePlaylistUseCase Implementation `#completed`
**Achievement**: Production-ready Spotify API operations with sophisticated reordering algorithms

**Key Changes**:
- Replaced placeholder implementations with real Spotify API operations
- Added proper null checks and type safety improvements  
- Implemented sophisticated track reordering logic
- Added comprehensive error handling

### Technical Debt Cleanup `#completed`
**Achievement**: Massive refactor moving business logic from workflow nodes to use cases

**Architecture Change**: 
- **Before**: Workflow nodes contained business logic
- **After**: Workflow nodes handle orchestration, use cases contain business logic
- **Result**: Clean separation of concerns following Clean Architecture principles

**Impact**: Simplified workflow nodes to pure delegators while consolidating complex logic in testable use cases.

### Track Metrics Architecture Fix `#completed`
**Key Decision**: Database-first caching with `track_metrics` table as source of truth

**Pattern Established**:
1. Get existing metrics from track_metrics table
2. Identify missing metrics for requested types  
3. Fetch fresh metadata only for missing/stale metrics
4. Combine existing + newly extracted metrics

**Files Changed**:
- `src/infrastructure/services/track_metrics_manager.py` - Core architecture implementation
- `src/infrastructure/connectors/spotify.py` - Added `batch_get_track_info()` method
- `src/infrastructure/services/metric_freshness_controller.py` - Renamed for accuracy

**Testing Pattern**: Established class-level monkeypatching for `@define(slots=True)` attrs classes.

---

## Architecture Notes

### Clean Architecture Compliance
- Domain layer: Pure business logic, no external dependencies
- Application layer: Use cases orchestrate domain operations
- Infrastructure layer: External service integrations
- Interface layer: CLI/future web interface

### Workflow Node Pattern
- Nodes handle orchestration and parameter passing
- Use cases contain all business logic
- Clear separation enables easy testing and maintenance

### Database-First Caching
- Always check existing data before external API calls
- Respect freshness controller decisions about staleness
- Combine cached and fresh data intelligently