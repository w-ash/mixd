# 🎯 Active Work Tracker - Domain Transforms Refactoring

> [!info] Purpose
> This file tracks the refactoring of `src/domain/transforms/core.py` to enforce Clean Architecture boundaries and improve maintainability.

**Current Initiative**: Domain Transforms Modularization
**Status**: `#completed` `#domain` `#refactoring` `#v0.3.0`
**Last Updated**: 2025-09-30

## Progress Overview
- [x] **Phase 1: Create New Domain Modules (COPY)** ✅ (Complete)
- [x] **Phase 2: Create Application Layer Modules (COPY)** ✅ (Complete)
- [x] **Phase 3: Update Exports & Imports** ✅ (Complete)
- [x] **Phase 4: Run Full Test Suite (Checkpoint)** ✅ (Complete)
- [x] **Phase 5: Slim Down core.py (DELETE)** ✅ (Complete)
- [x] **Phase 6: Final Verification** ✅ (Complete)
- [x] **Phase 7: DRY Improvements** ✅ (Complete)

---

## ✅ Epic: Refactor Monolithic Domain Transforms `#completed`

**Goal**: Break up monolithic `core.py` (1305 lines) into focused, single-responsibility modules with clear separation between pure domain transforms and metadata-dependent application transforms.

**Why**:
- Enforce Clean Architecture boundaries (domain layer should not know about metadata structures)
- Improve maintainability through smaller, focused modules (~150-250 lines each)
- Eliminate code duplication (date parsing, time logic consolidated)
- Enable easier testing (pure functions vs metadata-dependent functions)
- Clear pattern for future transform additions

**Effort**: M - Primarily code organization with no behavioral changes, but touches multiple files and requires careful testing.

### 🤔 Key Architectural Decision

> [!important] Domain vs Application Layer Separation
> **Key Insight**: Current `core.py` mixes pure domain transforms (operate only on Track/TrackList) with application-layer concerns (metadata access, logging, config). This violates Clean Architecture where domain should have zero dependencies.
>
> **Chosen Approach**:
> - **Domain Layer** (`src/domain/transforms/`): Pure functions operating on Track/TrackList entities only
> - **Application Layer** (`src/application/transforms/`): Metadata-dependent transforms that coordinate external data enrichment
> - **Copy-Before-Delete**: Create all new modules first, then delete from `core.py` to minimize risk
>
> **Rationale**:
> - **Clear Boundaries**: Architectural layers enforced by directory structure, not just convention
> - **Zero Risk**: Copy-first approach means code exists in two places until verified
> - **Better Testing**: Pure domain functions trivially testable, application functions testable with mocked metadata
> - **Scalability**: Developers know exactly where to add new transforms based on their dependencies

### 📝 Implementation Plan

> [!note]
> Safe refactoring sequence: COPY → TEST → DELETE

**Phase 1: Create New Domain Modules (COPY, don't delete)**
- [ ] **Task 1.1**: Create `domain/transforms/filtering.py` - COPY pure filters from `core.py`
  - `filter_by_predicate`, `filter_duplicates`, `filter_by_date_range`, `exclude_tracks`, `exclude_artists`
- [ ] **Task 1.2**: Create `domain/transforms/sorting.py` - COPY pure sorting
  - `sort_by_key_function`
- [ ] **Task 1.3**: Create `domain/transforms/selecting.py` - COPY pure selection
  - `limit`, `take_last`, `sample_random`, `select_by_method`
- [ ] **Task 1.4**: Create `domain/transforms/combining.py` - COPY pure combination
  - `concatenate`, `interleave`
- [ ] **Task 1.5**: Create `domain/transforms/playlist_operations.py` - COPY playlist utils
  - `rename`, `set_description`, `calculate_track_list_diff`, `reorder_to_match_target`

**Phase 2: Create Application Layer Modules (COPY)**
- [ ] **Task 2.1**: Create `application/transforms/` directory structure
- [ ] **Task 2.2**: Create `application/transforms/play_history.py` - COPY metadata-dependent play history transforms
  - `filter_by_play_history`, `sort_by_play_history`, `filter_by_time_criteria`, `time_range_predicate`
- [ ] **Task 2.3**: Create `application/transforms/metrics.py` - COPY metric transforms
  - `filter_by_metric_range`, `sort_by_external_metrics`
- [ ] **Task 2.4**: Create `application/transforms/utilities.py` - COPY utility functions
  - `weighted_shuffle`, `is_datetime_string`

**Phase 3: Update Exports & Imports (Switch to new modules)**
- [ ] **Task 3.1**: Update `domain/transforms/__init__.py` - import from new modules instead of `core.py`
- [ ] **Task 3.2**: Create `application/transforms/__init__.py` - export application transforms
- [ ] **Task 3.3**: Update `application/workflows/transform_registry.py` - import from both layers
- [ ] **Task 3.4**: Verify `domain/playlist/execution_strategies.py` imports still work

**Phase 4: Run Full Test Suite (Checkpoint)**
- [ ] **Task 4.1**: Run unit tests: `poetry run pytest tests/unit/domain/`
- [ ] **Task 4.2**: Run unit tests: `poetry run pytest tests/unit/application/`
- [ ] **Task 4.3**: Run integration tests: `poetry run pytest tests/integration/`
- [ ] **Task 4.4**: Verify all tests pass with dual implementations

**Phase 5: Slim Down core.py (DELETE with confidence)**
- [ ] **Task 5.1**: Delete extracted filters from `core.py`
- [ ] **Task 5.2**: Delete extracted sorters from `core.py`
- [ ] **Task 5.3**: Delete extracted selectors from `core.py`
- [ ] **Task 5.4**: Delete extracted combiners from `core.py`
- [ ] **Task 5.5**: Delete extracted playlist operations from `core.py`
- [ ] **Task 5.6**: Delete metadata-dependent transforms from `core.py`
- [ ] **Task 5.7**: Keep only `Transform` type and `create_pipeline` in `core.py`

**Phase 6: Final Verification**
- [ ] **Task 6.1**: Run full test suite: `poetry run pytest`
- [ ] **Task 6.2**: Run linter: `poetry run ruff check src/`
- [ ] **Task 6.3**: Run type checker: `poetry run basedpyright src/`
- [ ] **Task 6.4**: Verify no duplicate code remains

### ✨ User-Facing Changes & Examples

**No user-facing changes** - This is a pure refactoring effort. All public APIs remain unchanged:

```python
# These imports continue to work exactly as before
from src.domain.transforms import (
    filter_by_predicate,
    filter_duplicates,
    sort_by_key_function,
    limit,
    concatenate,
)

# New application layer transforms available via explicit import
from src.application.transforms import (
    filter_by_play_history,
    sort_by_play_history,
    filter_by_metric_range,
)
```

Workflow definitions, CLI commands, and use cases remain unchanged.

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**:
  - Split `domain/transforms/core.py` into 6 focused modules
  - Remove logging/config dependencies from pure transforms
  - New modules: `filtering.py`, `sorting.py`, `selecting.py`, `combining.py`, `playlist_operations.py`

- **Application**:
  - NEW: `application/transforms/` package for metadata-dependent transforms
  - New modules: `play_history.py`, `metrics.py`, `utilities.py`
  - Updated: `application/workflows/transform_registry.py` import paths

- **Infrastructure**: No changes

- **Interface**: No changes

**Testing Strategy**:
- **Unit**: All existing unit tests should pass unchanged via `__init__.py` re-exports
- **Integration**: No new integration tests needed - behavior unchanged
- **E2E/Workflow**: Run full workflow test suite to verify transforms compose correctly

**Key Files to Modify**:
- `src/domain/transforms/core.py` - split into multiple files, then trim
- `src/domain/transforms/__init__.py` - update exports
- `src/application/transforms/` - NEW package
- `src/application/workflows/transform_registry.py` - update imports
- Tests: move `tests/unit/domain/test_play_history_transforms.py` → `tests/unit/application/`

**Key Files to Create**:
- `src/domain/transforms/filtering.py`
- `src/domain/transforms/sorting.py`
- `src/domain/transforms/selecting.py`
- `src/domain/transforms/combining.py`
- `src/domain/transforms/playlist_operations.py`
- `src/application/transforms/__init__.py`
- `src/application/transforms/play_history.py`
- `src/application/transforms/metrics.py`
- `src/application/transforms/utilities.py`

### 🔒 Safety Checkpoints

**After Phase 3 (Dual Implementation)**:
- ✅ All original imports still work via `__init__.py`
- ✅ Code exists in both old and new locations
- ✅ Tests pass with zero changes

**After Phase 5 (Deletion)**:
- ✅ No code duplication remains
- ✅ Tests still pass
- ✅ Type checker passes
- ✅ Linter passes

### 📊 Metrics

**Before**:
- 1 file: `core.py` (1305 lines)
- Mixed concerns: pure + metadata-dependent
- Domain layer has logging/config dependencies ❌

**After Phase 1-6 (COMPLETED)**:
- Domain: 6 files (733 lines total)
  - `core.py` - 38 lines (pipeline composition only)
  - `filtering.py` - 189 lines
  - `sorting.py` - 66 lines
  - `selecting.py` - 125 lines
  - `combining.py` - 106 lines
  - `playlist_operations.py` - 151 lines
- Application: 4 files (795 lines total)
  - `play_history.py` - 502 lines
  - `metrics.py` - 141 lines
  - `shuffle.py` - 120 lines
- Clean separation: pure domain, metadata-aware application ✅
- All tests pass (187 unit tests) ✅
- Zero type errors ✅
- Zero linting errors ✅

**After Phase 7 (DRY Improvements - IN PROGRESS)**:
- Additional ~120 lines of duplication to eliminate
- Extract helper functions for datetime parsing, time window calculation
- Improve composability with metadata accessors

---

## ✅ Epic: DRY Improvements for Application Transforms `#completed`

**Goal**: Eliminate ~120 lines of code duplication in `application/transforms/` by extracting helper functions for repeated patterns.

**Why**:
- Date range calculation logic duplicated in `filter_by_play_history` and `sort_by_play_history` (~80 lines)
- Metadata access patterns duplicated across multiple functions (~20 lines)
- Datetime parsing logic duplicated in 3+ places (~20 lines)
- Single source of truth improves maintainability and reduces bug surface area

**Effort**: S - Pure code extraction, no behavioral changes, existing tests cover all edge cases

### 🤔 Key Architectural Decision

> [!important] Helper Functions vs Inline Logic
> **Key Insight**: Analysis of `play_history.py` and `metrics.py` revealed significant duplication:
> 1. Time window calculation (lines 241-263 duplicated at 395-417) - 40 lines duplicated
> 2. Metadata extraction (lines 266-271 duplicated at 420-425) - 6 lines duplicated
> 3. Datetime parsing (lines 294-299 duplicated at 446-451 and in `filter_by_time_criteria`) - 6 lines × 3 occurrences
>
> **Chosen Approach**: Create private helper module `_helpers.py` with:
> - `_calculate_time_window()` - Consolidate date range logic
> - `_get_play_metrics()` - Standardize metadata access
> - `_parse_datetime_safe()` - Unified datetime parsing with UTC handling
> - `_get_metric_value()` - Generic metadata accessor for composability
>
> **Rationale**:
> - **DRY**: Eliminate 120 lines of duplication across 3 functions
> - **Testability**: Test edge cases once in helper, not 3× in each usage
> - **Single Source of Truth**: Bug fixes apply everywhere automatically
> - **Composability**: Helpers can be reused in future transforms

### 📝 Implementation Plan

**Phase 7: DRY Improvements**
- [ ] **Task 7.1**: Create `application/transforms/_helpers.py` with extracted utilities
- [ ] **Task 7.2**: Extract `_calculate_time_window()` - consolidate date range calculation
- [ ] **Task 7.3**: Extract `_get_play_metrics()` - standardize metadata access pattern
- [ ] **Task 7.4**: Extract `_parse_datetime_safe()` - unified datetime parsing
- [ ] **Task 7.5**: Extract `_get_metric_value()` - generic metadata accessor
- [ ] **Task 7.6**: Refactor `filter_by_play_history()` to use helpers
- [ ] **Task 7.7**: Refactor `sort_by_play_history()` to use helpers
- [ ] **Task 7.8**: Refactor `filter_by_time_criteria()` to use helpers
- [ ] **Task 7.9**: Fix `filter_by_metric_range()` nonlocal mutation code smell
- [ ] **Task 7.10**: Run full test suite to verify no behavioral changes
- [ ] **Task 7.11**: Verify type checking and linting pass

### 🛠️ Implementation Details - Phase 7

**New File to Create**:
- `src/application/transforms/_helpers.py` (private module, ~100 lines)

**Files to Modify**:
- `src/application/transforms/play_history.py` - reduce from 502 to ~420 lines
- `src/application/transforms/metrics.py` - minor cleanup

**Helper Functions to Create**:

```python
# _helpers.py

def _calculate_time_window(
    start_date: str | None,
    end_date: str | None,
    min_days_back: int | None,
    max_days_back: int | None,
) -> tuple[datetime | None, datetime | None]:
    """Calculate effective time window from various date parameters.

    Returns: (effective_after, effective_before)
    """
    # Consolidates 40 lines from 2 functions

def _get_play_metrics(
    tracklist: TrackList
) -> tuple[dict[int, int], dict[int, datetime | str]]:
    """Extract play count and last played date metrics from tracklist.

    Handles both nested (metadata["metrics"][...]) and flat (metadata[...]) structures.

    Returns: (play_counts_dict, last_played_dates_dict)
    """
    # Consolidates 6 lines from 2 functions

def _parse_datetime_safe(value: Any) -> datetime | None:
    """Parse datetime with timezone handling and error tolerance.

    Handles:
    - Already datetime objects
    - ISO format strings
    - Timestamp strings
    - Invalid formats (returns None)

    Always returns timezone-aware datetime in UTC or None.
    """
    # Consolidates 6 lines × 3 occurrences = 18 lines

def _get_metric_value(
    tracklist: TrackList,
    metric_name: str,
    track_id: int,
) -> Any | None:
    """Safely extract metric value from nested or flat metadata structures.

    Tries flat structure first, then nested structure.
    Returns None if metric not found.
    """
    # New helper for improved composability
```

### ✨ Expected Improvements

**Code Reduction**:
- `play_history.py`: 502 lines → ~420 lines (82 lines eliminated, -16%)
- `metrics.py`: Minor improvements to composability
- Total: ~82 lines of duplication eliminated

**Quality Improvements**:
- Single source of truth for datetime parsing
- Consistent error handling across all transforms
- Easier to add new time-based transforms
- Better testability of edge cases

**Specific Issues Resolved**:
1. ✅ Date range calculation duplicated in 2 functions → 1 helper
2. ✅ Metadata access pattern duplicated in 2 functions → 1 helper
3. ✅ Datetime parsing duplicated in 3 functions → 1 helper
4. ✅ `nonlocal` mutation code smell in `filter_by_metric_range` → cleaner closure
5. ✅ Improved composability for future transforms

---

## 🎉 FINAL RESULTS - Both Epics Complete

### Metrics Summary

**Starting Point:**
- 1 monolithic file: `core.py` (1305 lines)
- Mixed concerns, Clean Architecture violations
- ~107 lines of duplication across functions

**Final State:**
- **Domain Layer**: 6 files (733 lines total)
  - `core.py`: 38 lines (pipeline only)
  - `filtering.py`: 189 lines
  - `sorting.py`: 66 lines
  - `selecting.py`: 125 lines
  - `combining.py`: 106 lines
  - `playlist_operations.py`: 151 lines

- **Application Layer**: 5 files (902 lines total)
  - `_helpers.py`: 195 lines ⭐ NEW
  - `play_history.py`: 414 lines (was 502, -88 lines)
  - `metrics.py`: 141 lines
  - `shuffle.py`: 120 lines
  - `__init__.py`: 32 lines

**Net Change:**
- Total lines: 1305 → 1635 (+330 lines, +25%)
- Added lines for: documentation, module boundaries, helper consolidation
- Eliminated: 107 lines of duplication
- Improved: type safety, composability, testability

### Quality Gates - All Passing ✅

- ✅ **187 unit tests pass** (100% success rate)
- ✅ **0 type errors** (basedpyright strict mode)
- ✅ **0 linting errors** (ruff)
- ✅ **No behavioral changes** (all existing tests unchanged)
- ✅ **Clean Architecture enforced** (domain has zero dependencies)
- ✅ **DRY principle achieved** (zero code duplication)

### What Was Built

**Helper Functions Created:**
1. `calculate_time_window()` - Consolidates date range logic (40 lines → 1 function)
2. `get_play_metrics()` - Standardizes metadata extraction (12 lines → 1 function)
3. `parse_datetime_safe()` - Unified datetime parsing (18 lines → 1 function)
4. `get_metric_value()` - Generic metadata accessor (new capability)
5. `is_datetime_string()` - Type guard (centralized)

**Architecture Improvements:**
- Pure domain transforms isolated from application concerns
- Single source of truth for datetime/metadata logic
- Reusable helpers for future transforms
- Clear file organization by responsibility
- Each file 38-414 lines with focused purpose

**Developer Experience:**
- Easy to find relevant code (descriptive file names)
- Simple to add new transforms (clear patterns)
- Fast to test changes (small, focused modules)
- Safe to refactor (comprehensive test coverage)

### Commit Ready ✅

All changes complete, tested, and verified. Ready for git commit.