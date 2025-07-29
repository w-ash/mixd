# 🎯 Active Work Tracker - v0.2.7 Narada Data Source Nodes

> [!info] Purpose
> This file tracks active development work on the current epic/refactor. For strategic roadmap and completed milestones, see [[BACKLOG]].

**Current Initiative**: v0.2.7 Narada Data Source Nodes  
**Status**: `#not-started` `#data-sources` `#v0-2-7`  
**Last Updated**: July 29, 2025

## Progress Overview

v0.2.7 Narada Data Source Nodes:
- [ ] **Narada Data Source Nodes** 🔜 (Not Started - Current focus)

---

## 🔜 NEW Epic: Narada Data Source Nodes `#not-started`

**Effort**: Medium (M) - Cross-module feature with existing workflow system integration  
**Goal**: Create workflow source nodes that tap directly into Narada's rich canonical track database

### Ultra-DRY Architecture Decision

> [!important] Composition Over Complexity
> **Key Insight**: After analyzing `src/domain/transforms/core.py`, we already have most needed functionality:
> - `filter_by_play_history()` - play count filtering with time windows
> - `sort_by_play_history()` - sorting by play frequency  
> - `filter_by_metric_range()` - metric-based filtering
> - `select_by_method()` - track selection strategies
>
> **Ultra-DRY Approach**: Create simple source nodes + leverage existing transforms rather than complex source nodes with built-in filtering
> - **Source nodes**: Thin orchestration layer that delegates to use cases
> - **Business logic**: Lives in application use cases, not workflow nodes
> - **Composition**: Users combine simple sources with existing transforms
> - **Clean separation**: Workflow orchestration vs business logic boundaries respected

### Revised Implementation Plan

**Phase 1: Core Implementation (Ultra-DRY)**
- [x] **Create GetLikedTracksUseCase** - Business logic for liked track retrieval
- [x] **Create GetPlayedTracksUseCase** - Business logic for play history retrieval  
- [x] **Create source_liked_tracks node** - Thin orchestration wrapper
- [x] **Create source_played_tracks node** - Thin orchestration wrapper

**Phase 2: Performance & Sorting Enhancement**
- [ ] **Add repository sorting methods** - Infrastructure layer: efficient database-level sorting
- [ ] **Update use cases with sort_by parameter** - Application layer: business logic for sorting options
- [ ] **Update source nodes with sort_by config** - Interface layer: orchestration with sorting options

**Phase 3: System Integration & Testing**
- [x] **Register source nodes** - Add to transform registry and node catalog
- [x] **Create example workflows** - Demonstrate composition with existing transforms
- [ ] **Add unit tests** - Test pyramid structure for critical user paths
- [ ] **Integration testing** - Validate source + transform combinations

### Scope & Requirements

**What**: Simple source nodes with meaningful sorting + composition with existing transforms
- **`source.liked_tracks`**: Liked track retrieval with sorting (limit, sort_by)
- **`source.played_tracks`**: Play history retrieval with sorting (limit, sort_by, days_back)
- **Performance-First**: Database-level sorting with single queries, minimal joins
- **Composition Strategy**: Users combine with existing `filter.by_play_history`, `sorter.by_play_history`, etc.
- **Performance Safeguards**: Maximum 10,000 tracks per source, configurable limits

**Why**: Enable workflows based on listening history without requiring playlist containers, while leveraging existing transformation infrastructure for maximum DRY principles.

### Example Ultra-DRY Workflow Composition

**Enhanced source nodes with sorting**:
```json
{
  "type": "source.liked_tracks",
  "config": {
    "limit": 1000,
    "sort_by": "liked_at_desc"
  }
}
```

**Composition with existing transforms**:
```json
{
  "tasks": [
    {
      "type": "source.played_tracks",
      "id": "get_plays",
      "config": {
        "limit": 10000, 
        "sort_by": "total_plays_desc",
        "days_back": 365
      }
    },
    {
      "type": "filter.by_play_history", 
      "id": "filter_frequency",
      "upstream": ["get_plays"],
      "config": {
        "min_plays": 2,
        "max_plays": 50,
        "start_date": "2024-01-01",
        "end_date": "2024-12-31"
      }
    }
  ]
}
```

### **Sorting Options (Performance-First)**

**`source.liked_tracks` Sort Options**:
- `liked_at_desc` - Most recently liked first (default)
- `liked_at_asc` - Oldest likes first
- `title_asc` - Alphabetical by title
- `created_at_desc` - Most recent tracks first
- `random` - Random sampling

**`source.played_tracks` Sort Options**:
- `played_at_desc` - Most recently played first (default)
- `total_plays_desc` - Most played tracks first  
- `last_played_desc` - Tracks by recency of last play
- `first_played_asc` - Discovery order
- `title_asc` - Alphabetical by title
- `random` - Random sampling

### **Benefits of Ultra-DRY Approach**

1. **Leverage Existing Code**: Reuse battle-tested transforms from `core.py`
2. **Simple Source Nodes**: Minimal implementation, clear responsibilities
3. **Composition Flexibility**: Users build complex behavior from simple parts
4. **Clean Architecture**: Business logic in use cases, orchestration in nodes
5. **DRY Principles**: Zero duplication of filtering/sorting logic
6. **Performance**: Existing transforms already optimized for large datasets

### **Clean Architecture Implementation Strategy**

**Domain Layer** (`src/domain/`):
- No changes needed - existing entities and repository interfaces sufficient

**Application Layer** (`src/application/`):
- **Use Cases**: Business logic for track retrieval with sorting parameters
- **Workflows**: Thin orchestration nodes that delegate to use cases

**Infrastructure Layer** (`src/infrastructure/`):
- **Repository Implementations**: Database-level sorting with efficient queries
- **Performance**: Single queries with ORDER BY clauses, leveraging existing indexes

**Interface Layer** (`src/interface/`):
- **CLI Integration**: No changes needed - workflows handle everything

### **Test Strategy (Pyramid Structure)**

**Unit Tests (Fast, Many)**:
- `test_get_liked_tracks_use_case.py` - Business logic validation
- `test_get_played_tracks_use_case.py` - Business logic validation
- Mock UnitOfWork, focus on sorting parameter validation

**Integration Tests (Medium, Fewer)**:
- `test_source_nodes_integration.py` - End-to-end source node functionality
- Real database, validate actual data retrieval and sorting

**Workflow Tests (Slow, Few)**:
- `test_composition_workflows.py` - Critical user paths like "Hidden Gems" workflow
- Validate source + transform composition patterns

### Files to Modify (Enhanced Plan)

**Phase 2 Updates**:
- `src/domain/repositories/interfaces.py` - **Add sort_by parameters to repository protocols**
- `src/infrastructure/persistence/repositories/track/likes.py` - **Add sorting to liked tracks queries**
- `src/infrastructure/persistence/repositories/track/plays.py` - **Add sorting to play history queries**
- `src/application/use_cases/get_liked_tracks.py` - **Add sort_by parameter and validation**
- `src/application/use_cases/get_played_tracks.py` - **Add sort_by parameter and validation**
- `src/application/workflows/source_nodes.py` - **Add sort_by config option**

**Phase 3 Testing**:
- `tests/unit/application/use_cases/test_get_liked_tracks.py` - **Unit tests**
- `tests/unit/application/use_cases/test_get_played_tracks.py` - **Unit tests**
- `tests/integration/test_source_nodes_integration.py` - **Integration tests**
- `tests/integration/test_composition_workflows.py` - **Workflow tests**

---