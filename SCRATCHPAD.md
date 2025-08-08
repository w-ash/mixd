# 🎯 Active Work Tracker - Narada Data Source Nodes

> [!info] Purpose  
> This file tracks active development work on the current epic. For strategic roadmap and completed milestones, see [[BACKLOG.md]].

**Current Initiative**: Narada Data Source Nodes  
**Status**: `#not-started` `#workflow-system` `#v0.2.7`  
**Last Updated**: 2025-08-06

## Progress Overview
- [ ] **Source Node Architecture Design** 🔜 (Not Started - Current focus)
- [ ] **Performance Safeguards Implementation**
- [ ] **Built-in Filtering System**

---

## 🔜 NEW Epic: Narada Data Source Nodes `#not-started`

**Goal**: Create workflow source nodes that tap directly into Narada's rich canonical track database, enabling workflows based on listening history and preferences without requiring playlist containers.

**Why**: Currently, workflows can only source tracks from playlists or external services. This limitation prevents users from creating workflows based on their actual listening patterns, liked tracks across services, or play history analysis. By adding native Narada data sources, we unlock powerful workflow patterns like "tracks I loved but haven't heard recently" or "my most-played tracks from last year" without requiring users to manually maintain playlists as containers.

**Effort**: M - Cross-module feature touching workflow system, domain repositories, and CLI interface with moderate architectural complexity but clear patterns from existing workflow nodes.

### 🤔 Key Architectural Decision
> [!important] Repository-First Data Access with Performance Safeguards  
> **Key Insight**: After analyzing the existing workflow system and repository patterns, the most effective approach is to leverage existing repository bulk operations with intelligent query optimization and built-in performance limits.
>
> **Chosen Approach**: Create specialized source nodes that use repository bulk queries with configurable limits, automatic pagination, and efficient filtering at the database level. Each source node will implement the existing `SourceNodeProtocol` and delegate to optimized repository methods.
>
> **Rationale**:
> - **Performance**: Database-level filtering prevents memory issues with large datasets
> - **Consistency**: Uses proven repository patterns from existing import/export systems  
> - **Scalability**: Built-in limits (10,000 tracks max) prevent overwhelming downstream workflow nodes

### 📝 Implementation Plan
> [!note]
> Break down the work into logical, sequential tasks.

**Phase 1: Source Node Architecture Foundation**
- [ ] **Task 1.1**: Create `SourceLikedTracksNode` implementing `SourceNodeProtocol` with connector filtering support
- [ ] **Task 1.2**: Create `SourcePlayedTracksNode` with time range and frequency filter parameters
- [ ] **Task 1.3**: Add repository methods for bulk liked track retrieval with service filtering
- [ ] **Task 1.4**: Add repository methods for play history retrieval with configurable date ranges and play count thresholds

**Phase 2: Performance Safeguards & Filtering**
- [ ] **Task 2.1**: Implement maximum track limits (configurable, default 10,000) with clear user messaging
- [ ] **Task 2.2**: Add efficient database pagination for large result sets
- [ ] **Task 2.3**: Create built-in filtering system for date ranges, service filters, and play count thresholds
- [ ] **Task 2.4**: Add query optimization and indexing validation for performance

**Phase 3: Integration & User Experience**
- [ ] **Task 3.1**: Register new source nodes in workflow node factory
- [ ] **Task 3.2**: Add CLI parameter validation and help text for new node types
- [ ] **Task 3.3**: Create example workflows demonstrating discovery patterns
- [ ] **Task 3.4**: Add comprehensive error handling for edge cases (empty results, invalid date ranges)

### ✨ User-Facing Changes & Examples

**New Source Node Types Available:**

```yaml
# Source liked tracks across all services
source:
  type: liked_tracks
  parameters:
    connectors: ["spotify", "lastfm"]  # Optional: filter by service
    limit: 5000                        # Optional: max tracks (default 10000)

# Source tracks from play history
source:
  type: played_tracks  
  parameters:
    date_from: "2024-01-01"           # Optional: start date
    date_to: "2024-12-31"             # Optional: end date
    min_play_count: 3                 # Optional: minimum plays
    services: ["spotify"]             # Optional: filter by service
    limit: 1000                       # Optional: max tracks
```

**Example Discovery Workflows:**

```bash
# Create workflow to find loved tracks not heard recently
narada workflows create rediscovery.yaml

# Find most-played tracks from last year for "Best of 2024" playlist  
narada workflows create best-of-year.yaml
```

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**: Add bulk query methods to `TrackRepositoryProtocol` and `PlayRepositoryProtocol`
- **Application**: Create new use cases for bulk liked track and play history retrieval with filtering
- **Infrastructure**: Implement optimized repository queries with proper indexing and pagination
- **Interface**: Add new source node types to workflow system and CLI validation

**Testing Strategy**:
- **Unit**: Test source node parameter validation, repository query building, and filtering logic
- **Integration**: Test database query performance with large datasets and proper result limiting
- **E2E/Workflow**: Validate complete workflow execution with new source nodes and downstream transformers

**Key Files to Modify**:
- `src/domain/repositories/interfaces.py` - Add bulk query method protocols
- `src/application/use_cases/get_liked_tracks.py` - Enhance for bulk operations
- `src/application/use_cases/get_played_tracks.py` - New use case for play history sourcing
- `src/infrastructure/persistence/repositories/track/` - Implement optimized bulk queries
- `src/infrastructure/persistence/repositories/plays.py` - Add play history retrieval methods
- `src/interface/workflows/nodes/source/` - New source node implementations
- `tests/unit/workflows/test_source_nodes.py` - Unit tests for new source nodes
- `tests/integration/test_workflow_data_sources.py` - Integration tests for data source workflows