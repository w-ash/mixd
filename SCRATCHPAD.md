# 🎯 Active Work Tracker - Advanced Transformer Workflow Nodes

> [!info] Purpose
> This file tracks active development work on the current epic. For strategic roadmap and completed milestones, see [[BACKLOG.md]].

**Current Initiative**: Advanced Transformer Workflow Nodes
**Status**: `#not-started` `#workflow-system` `#v0.2.7`
**Last Updated**: 2025-08-11

## Progress Overview
- [ ] **Enhanced Combiner Operations** 🔜 (Not Started - Current focus)
- [ ] **Advanced Selection & Sampling Features**
- [ ] **Intelligent Sorting Extensions**

---

## 🔜 NEW Epic: Advanced Transformer Workflow Nodes `#not-started`

**Goal**: Implement additional transformer nodes for the workflow system that provide sophisticated track collection manipulation capabilities including advanced combining strategies, intelligent selection methods, and enhanced sorting options.

**Why**: More transformation options enable more powerful workflows. Current workflow system has basic transformers, but users need advanced capabilities like weighted randomization, intelligent mixing strategies, temporal-based sorting, and flexible selection methods to create sophisticated playlist automation and music discovery workflows.

**Effort**: M - Moderate effort extending existing workflow node patterns with new transformation algorithms. Clear architectural patterns exist but requires implementing new business logic and transformation strategies.

### 🤔 Key Architectural Decision
> [!important] Composition Over Complexity - Modular Transformer Pattern
> **Key Insight**: After analyzing the existing workflow transformation system, the most effective approach is to create focused, single-purpose transformer nodes that can be composed together rather than complex multi-feature nodes.
>
> **Chosen Approach**: Implement discrete transformer nodes following the existing `make_node()` pattern, each handling a specific transformation concern. Use the established node factory system for consistent parameter handling and validation.
>
> **Rationale**:
> - **Composability**: Small, focused nodes can be combined for complex workflows
> - **Maintainability**: Single-purpose nodes are easier to test and debug
> - **Consistency**: Follows established workflow architecture patterns

### 📝 Implementation Plan
> [!note]
> Break down the work into logical, sequential tasks.

**Phase 1: Enhanced Combining Operations**
- [ ] **Task 1.1**: Implement `combiner.mix_playlists` with intelligent track interleaving strategies
- [ ] **Task 1.2**: Create `combiner.weighted_merge` with configurable source weighting
- [ ] **Task 1.3**: Add `combiner.balanced_combine` for equal representation from multiple sources

**Phase 2: Advanced Selection & Sampling**
- [ ] **Task 2.1**: Implement `selector.random_sample` with optional weighting support
- [ ] **Task 2.2**: Create `selector.head_tracks` and `selector.tail_tracks` for first/last N selections
- [ ] **Task 2.3**: Add `selector.distribute_evenly` for balanced selection across time periods or metrics

**Phase 3: Intelligent Sorting Extensions**
- [ ] **Task 3.1**: Implement `sorter.by_first_played` and `sorter.by_last_played` for temporal analysis
- [ ] **Task 3.2**: Create `sorter.randomize_weighted` with configurable weighting strategies
- [ ] **Task 3.3**: Add `sorter.by_discovery_potential` combining multiple metrics for music discovery

### ✨ User-Facing Changes & Examples

**New Combiner Node Types:**
```yaml
# Mix tracks intelligently from multiple playlists
combiner:
  type: mix_playlists
  parameters:
    sources: ["source1", "source2"]
    strategy: "round_robin"  # Options: round_robin, weighted, balanced

# Weighted merge with source priorities
combiner:
  type: weighted_merge
  parameters:
    sources: {"playlist1": 0.6, "playlist2": 0.4}
```

**New Selector Node Types:**
```yaml
# Random sampling with optional weighting
selector:
  type: random_sample
  parameters:
    count: 50
    weight_by: "popularity"  # Optional metric for weighting

# First/last N tracks selection
selector:
  type: head_tracks
  parameters:
    count: 25
```

**New Sorter Node Types:**
```yaml
# Sort by temporal play data
sorter:
  type: by_first_played
  parameters:
    reverse: false

# Weighted randomization
sorter:
  type: randomize_weighted
  parameters:
    weight_by: "play_count"
    randomization_factor: 0.3
```

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**: Extend TrackList manipulation utilities for advanced transformations
- **Application**: Create new transformation algorithms and weighting strategies  
- **Infrastructure**: Implement new node factory methods for parameter handling
- **Interface**: Register new transformer nodes in workflow node catalog

**Testing Strategy**:
- **Unit**: Test individual transformation algorithms with known inputs
- **Integration**: Test complex workflow compositions with multiple new transformers
- **Performance**: Validate performance with large track collections (10K+ tracks)

**Key Files to Modify**:
- `src/application/workflows/node_factories.py` - Add new transformer factory methods
- `src/application/workflows/node_catalog.py` - Register new transformer nodes
- `src/application/workflows/transformers/` - New transformation implementation modules
- `src/domain/entities/track.py` - Extend TrackList utilities if needed
- `tests/unit/workflows/test_advanced_transformers.py` - Unit tests for new transformers
- `tests/integration/test_complex_workflows.py` - Integration tests for advanced workflow patterns