# 🎯 Active Work Tracker - Enhanced Match Failure Handling

> [!info] Purpose
> This file tracks active development work on improving match failure handling across the music matching system. For strategic roadmap and completed milestones, see [[BACKLOG.md]].

**Current Initiative**: Enhanced Match Failure Handling
**Status**: `#in-progress` `#matching` `#v0.1.0`
**Last Updated**: 2025-08-07

## Progress Overview
- [x] **Pyright Type Issues Resolution** ✅ (Completed - Foundation work)
- [x] **Enhanced Result Types with Failure Reasons** ✅ (Completed - Phase 1)
- [x] **Structured Logging for Match Failures** ✅ (Completed - Phase 1)
- [x] **DRY Failure Utilities** ✅ (Added - Composable approach)

---

## 🔜 NEW Epic: Thoughtful Match Failure Handling `#in-progress`

**Goal**: Replace silent match failures with structured failure tracking and intelligent handling, while maintaining clean separation between match attempt results and workflow control decisions.

**Why**: Currently, the system silently returns empty dictionaries when matches fail, providing no visibility into failure patterns or reasons. This makes debugging difficult and prevents optimization of matching strategies. The system needs to distinguish between "no match found" vs "API error" vs "invalid data" while letting calling functions decide how to handle failures.

**Effort**: M - Moderate effort requiring changes across multiple architectural layers but leveraging existing clean architecture patterns.

### 🤔 Key Architectural Decision
> [!important] Structured Failure Reporting with Caller Control
> **Key Insight**: The current system conflates "no results found" with all other failure types by returning empty dicts. This loses valuable diagnostic information and prevents intelligent retry/fallback strategies. However, match failure handling decisions should remain with the calling workflow, not embedded in the matching providers.
>
> **Chosen Approach**: Extend the existing `MatchResult` domain type with structured failure reasons while maintaining the provider contract. Add comprehensive warning-level logging for all match failures, but delegate workflow control decisions to application-layer use cases.
>
> **Rationale**:
> - **Separation of Concerns**: Providers report what happened, applications decide what to do about it
> - **Observability**: Structured failure data enables debugging and optimization
> - **Flexibility**: Calling functions can choose to fail fast, continue with partial results, or implement retry logic

### 📝 Implementation Plan
> [!note]
> Break down the work into logical, sequential tasks.

**Phase 1: Domain & Type Foundation**
- [ ] **Task 1.1**: Add `MatchFailureReason` enum to domain types (no_isrc, api_error, no_results, invalid_data, rate_limited, auth_error)
- [ ] **Task 1.2**: Extend `MatchResult` with optional `failure_reason` and `failure_details` fields
- [ ] **Task 1.3**: Update `RawProviderMatch` type to support failure cases

**Phase 2: Infrastructure Provider Updates**
- [ ] **Task 2.1**: Update Spotify provider to return structured failures with warning logs
- [ ] **Task 2.2**: Update MusicBrainz provider to return structured failures with warning logs  
- [ ] **Task 2.3**: Update Last.fm provider to return structured failures with warning logs
- [ ] **Task 2.4**: Add comprehensive warning-level logging for all failure scenarios

**Phase 3: Testing & Validation**
- [ ] **Task 3.1**: Add unit tests for failure reason classification
- [ ] **Task 3.2**: Add integration tests verifying proper logging behavior
- [ ] **Task 3.3**: Verify existing application logic handles new result structure gracefully

### ✨ User-Facing Changes & Examples

**Enhanced Logging Output**:
```
[WARNING] Spotify ISRC match failed for track 12345: No ISRC available (reason: no_isrc)
[WARNING] MusicBrainz artist/title search failed for track 67890: API timeout (reason: api_error, details: connection_timeout_5s)
[WARNING] Spotify search returned no results for "Artist - Track Title" (reason: no_results)
```

**Application Layer Benefits**:
- Use cases can distinguish transient failures (api_error) from permanent ones (no_isrc)
- Retry logic can be applied selectively based on failure type
- Bulk operations can continue processing and report failure summaries at completion

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**: Add `MatchFailureReason` enum and extend `MatchResult` with failure tracking
- **Application**: No changes required - existing use cases will work with enhanced result types
- **Infrastructure**: Update all matching providers to return structured failures and log warnings
- **Interface**: No changes required - CLI will benefit from improved logging visibility

**Testing Strategy**:
- **Unit**: Test failure reason classification for each provider's error scenarios
- **Integration**: Verify proper warning logging behavior during match failures  
- **E2E/Workflow**: Validate that applications continue to function with enhanced failure data

**Key Files to Modify**:
- `src/domain/matching/types.py` - Add failure reason enum and extend MatchResult
- `src/infrastructure/matching_providers/spotify.py` - Add structured failure handling
- `src/infrastructure/matching_providers/musicbrainz.py` - Add structured failure handling
- `src/infrastructure/matching_providers/lastfm.py` - Add structured failure handling
- `tests/unit/domain/matching/test_types.py` - Test new failure types
- `tests/integration/matching_providers/` - Test failure logging behavior

**Design Principles Maintained**:
- **Clean Architecture**: Failure reasons defined in domain, providers implement, applications consume
- **Separation of Concerns**: Providers report failures, applications decide workflow actions
- **Batch-First Design**: Failure tracking works seamlessly with collection-based operations
- **Immutable Domain**: Failure reasons are immutable enums with structured data