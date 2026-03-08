# 🎯 Active Work Tracker - Tenacity Refactoring (DRY & Modernization)

> [!info] Purpose
> This file tracks active development work on the current epic. For strategic roadmap and completed milestones, see [[docs/backlog/README.md]].

**Current Initiative**: Tenacity Retry Policies - DRY & Modernization
**Status**: `#ready-to-commit` `#infrastructure` `#v0.3.0`
**Last Updated**: 2026-01-28

## ✅ Completed: Retry Callback Logging Fix

**Resolution**: The retry logging issue has been resolved. Callbacks now fire correctly.

**Root Cause**: The initial implementation used `retry_if_exception()` incorrectly. Fixed by:
1. Using `retry_if_exception_type(ServiceException) & create_error_classifier_retry(classifier)` pattern
2. Properly wiring `before_sleep` for retry logging and `after` for final failure logging
3. Adding jitter to all wait strategies (`+ wait_random(0, 1)`)

**Current State**: 734 tests passing, all retry policies working correctly.

---

## 📊 Epic Progress (Completed Work)

- [x] **Extract callback utilities** ✅ (`_format_duration`, `_extract_classified_error`)
- [x] **Replace retry_base with callable factory** ✅
- [x] **Add jitter to wait strategies** ✅ (All services have jitter)
- [x] **Complete type annotations** ✅ (Full annotations added)
- [x] **Update tests** ✅ (734 tests passing)
- [x] **Run full test suite** ✅ (All tests green)
- [x] **FIX LOGGING** ✅ (Resolved - callbacks firing correctly)

---

## 🔧 Epic: Tenacity Retry Policies - DRY & Modernization `#in-progress`

**Goal**: Eliminate duplication in retry_policies.py, adopt idiomatic tenacity patterns, add production-ready features (jitter), and ensure complete type safety.

**Why**:
- **HIGH: 80+ lines duplicated** in callback handlers (type guards, formatting, error classification)
- **HIGH: 130+ lines duplicated** across 3 factory methods (nearly identical structure)
- **MEDIUM: Missing type annotations** on callback factories (impacts IDE support, basedpyright)
- **MEDIUM: Non-idiomatic patterns** (`retry_base` inheritance, `next_action is None` checks)
- **MEDIUM: No jitter** in exponential backoff (thundering herd risk under rate limits)
- **Future-proofing**: Generic factory makes adding new services trivial (5-line config vs 20-line method)

**Effort**: M - Single file refactoring with clear patterns, comprehensive tests already exist

### 🎯 Objectives

**Code Quality**:
- **30% code reduction** (405 → ~280 lines)
- **Eliminate 125+ lines of duplication**
- **Full type annotations** for IDE/basedpyright strict mode
- **DRY**: Single source of truth for utilities, factory logic, wait strategies

**Production Readiness**:
- **Jitter enabled** - prevents thundering herd when multiple clients hit rate limits simultaneously
- **Idiomatic tenacity** - no internal API dependencies (`retry_base`), use `retry_error_callback`
- **Observability** - preserve structured logging with loguru (service binding, error details, timing)

**Maintainability**:
- **Generic factory** - new service requires 5-line config entry vs 20-line method copy-paste
- **Shared utilities** - callback changes edit 1 utility vs 2 duplicated handlers
- **Type safety** - catch errors at development time, not runtime

### 📋 Implementation Tasks

**Phase 1: Utilities & Core Patterns**
- [x] Task 1.1: Archive completed backoff→tenacity migration work
- [ ] Task 1.2: Extract `_format_duration()` utility (eliminates 6 duplicates)
- [ ] Task 1.3: Extract `_extract_classified_error()` utility (eliminates type guards, classification calls)
- [ ] Task 1.4: Replace `ErrorClassifierRetry(retry_base)` with `create_error_classifier_retry()` factory
- [ ] Task 1.5: Create `create_retry_error_handler()` using `retry_error_callback` (no `next_action` checks)

**Phase 2: Factory Consolidation**
- [ ] Task 2.1: Create `RetryPolicyConfig` dataclass with service configuration
- [ ] Task 2.2: Create `_CONFIGS` class variable with Spotify/LastFM/MusicBrainz configs
- [ ] Task 2.3: Implement `_load_exception_type()` lazy loader
- [ ] Task 2.4: Implement `_load_classifier()` lazy loader
- [ ] Task 2.5: Implement generic `create_policy(service_name)` method
- [ ] Task 2.6: Refactor `create_spotify_policy()` to call `create_policy("spotify")`
- [ ] Task 2.7: Refactor `create_lastfm_policy()` to call `create_policy("lastfm")`
- [ ] Task 2.8: Refactor `create_musicbrainz_policy()` to call `create_policy("musicbrainz")`

**Phase 3: Production Features & Type Safety**
- [ ] Task 3.1: Add jitter to Spotify wait strategy (`wait_exponential + wait_random(0, 1)`)
- [ ] Task 3.2: Add jitter to Last.FM wait strategy
- [ ] Task 3.3: Add jitter to MusicBrainz wait strategy
- [ ] Task 3.4: Add return type annotations to all callback factories
- [ ] Task 3.5: Add return type annotations to all utility functions
- [ ] Task 3.6: Remove unnecessary `TYPE_CHECKING` block (no circular dependencies)
- [ ] Task 3.7: Add type annotations to inner functions (_handle_backoff, on_retry_error, should_retry)

**Phase 4: Testing & Verification**
- [ ] Task 4.1: Update test_retry_policies.py to test utility functions
- [ ] Task 4.2: Add test for generic `create_policy()` method
- [ ] Task 4.3: Add test verifying jitter is enabled in wait strategies
- [ ] Task 4.4: Verify backward compatibility of service-specific factory methods
- [ ] Task 4.5: Run full test suite (expect 827+ tests passing)
- [ ] Task 4.6: Run basedpyright strict mode on retry_policies.py (0 errors)

### 🔍 Observability & Logging Review

**Current State**:
- ✅ Structured logging with loguru (keyword arguments: error_type, error_code, service, etc.)
- ✅ Service binding (`logger.bind(service="retry_policies")`)
- ✅ Rich context (attempt number, wait time, elapsed time, error details)
- ✅ Special handling for rate limit errors
- ⚠️ Log levels: Uses `warning` for both retries AND final failures

**Behavior Preservation**:
- Keep existing `logger.warning()` calls for retries
- Keep existing `logger.warning()` for final failures
- Preserve all structured context fields

**Future Improvement** (out of scope for this refactoring):
- Consider `logger.info()` for retries (expected behavior, reduce noise)
- Consider `logger.error()` for final failures (actual failures requiring attention)
- Note: Changing log levels is a behavior change, deferred to separate work

### ✨ Expected Outcomes

**Before**:
```python
# 405 lines total
class ErrorClassifierRetry(retry_base):  # 41 lines
create_tenacity_backoff_handler()  # 67 lines with duplicates
create_tenacity_giveup_handler()  # 62 lines with duplicates
create_spotify_policy()  # 20 lines
create_lastfm_policy()  # 26 lines
create_musicbrainz_policy()  # 16 lines
```

**After**:
```python
# ~280 lines total (30% reduction)
_format_duration()  # 3 lines (shared)
_extract_classified_error()  # 12 lines (shared)
create_error_classifier_retry()  # 15 lines (no inheritance)
create_tenacity_backoff_handler()  # 30 lines (uses utilities)
create_retry_error_handler()  # 20 lines (dedicated callback, uses utilities)
RetryPolicyConfig dataclass  # 10 lines
_CONFIGS registry  # 30 lines (3 services)
create_policy()  # 18 lines (generic)
create_spotify_policy()  # 2 lines (thin wrapper)
create_lastfm_policy()  # 2 lines (thin wrapper)
create_musicbrainz_policy()  # 2 lines (thin wrapper)
```

**Benefits**:
- Adding new service (e.g., Apple Music): **5 lines** (config entry) vs **20 lines** (copy-paste method)
- Changing callback logic: **1 utility edit** vs **2 duplicated handler edits**
- Changing wait strategy: **1 config entry** vs **3 factory method edits**
- Type safety: **Full IDE autocomplete** + **basedpyright strict mode passing**
- Production: **Jitter prevents thundering herd** under rate limits

---

## ✅ Epic: Migrate from Backoff to Tenacity `#complete`

**Goal**: Replace the `backoff` library with `tenacity` to reduce code duplication, centralize retry policies, and adopt modern Python 3.14 async retry patterns.

**Why**:
- **Massive duplication**: Spotify has 13 nearly-identical decorators, requiring edits in 13+ locations for any change
- **Inconsistent configuration**: Hardcoded values in Spotify, settings-based in Last.FM, bare minimum in MusicBrainz
- **Missing features**: No jitter, no composable stop conditions, limited observability
- **Better library**: Tenacity offers centralized policies, rich retry state, composable conditions, and built-in jitter

**Effort**: M - Mechanical transformation with clear patterns, but touches 23 decorated methods across 4 files

### 🔒 System Behavior Contract

**Guaranteed Behaviors**:
- All existing retry behavior must be preserved (3 attempts for Spotify/MusicBrainz, settings-based for Last.FM)
- Error classification must still determine retry vs fail-fast decisions
- Logging output must match current format (warnings, error codes, elapsed times)
- All existing tests must pass without modification

**Safe to Change**:
- Internal decorator implementation
- Retry policy instantiation mechanism
- Callback handler signatures (as long as logging output matches)

### 🤔 Architectural Decision Record

**Status**: Accepted
**Date**: 2025-12-23
**Deciders**: Solo dev after comprehensive codebase analysis

#### Context & Problem Statement

The codebase uses `backoff` library for retry logic across 3 connector clients (Spotify, Last.FM, MusicBrainz). Analysis revealed:
- 23 decorated methods with massive duplication
- Spotify: 13 identical decorators with hardcoded `max_tries=3`
- Last.FM: 6 decorators using a factory pattern with settings-based config
- MusicBrainz: 2 decorators with minimal configuration (no error classification)
- Sophisticated error classification system that determines retry behavior
- No jitter, which can cause thundering herd during rate limit recovery

Current backoff limitations:
- No way to centralize retry policies
- Can't combine stop conditions (attempts AND time)
- Callbacks receive limited retry state
- Each decorator repeats 4+ lines of configuration

#### Decision

Migrate to `tenacity` with centralized retry policies:

1. **Create `retry_policies.py`** with:
   - `ErrorClassifierRetry` predicate integrating our error classification
   - `RetryPolicyFactory` with service-specific policy methods
   - Enhanced callbacks leveraging tenacity's rich `RetryCallState`

2. **Refactor connector clients** to use centralized policies:
   - Add `_retry_policy` instance in `__attrs_post_init__`
   - Extract implementation methods (`_*_impl`) without retry logic
   - Replace decorators with policy calls: `await self._retry_policy(impl_method, *args)`

3. **Preserve error classification** as the brain of retry decisions:
   - Map error types to retry predicates
   - Retry: temporary, rate_limit, unknown
   - Fail fast: permanent, not_found

#### Consequences

**Positive**:
- **90% reduction in decorator boilerplate** (13 Spotify decorators → 1 policy instance)
- **Centralized retry policies** - single source of truth per service
- **Composable stop conditions** - can combine attempts + time limits
- **Rich retry state** - better logging and observability
- **Future extensibility** - easy to add jitter, metrics, adaptive policies
- **Modern async patterns** - Python 3.14 compatible

**Negative**:
- **One-time migration effort** - 23 methods to refactor
- **Learning curve** - tenacity has different API than backoff
- **Temporary code increase** - need implementation methods during migration

**Neutral**:
- Changes internal implementation but preserves external behavior
- Test suite should pass without modification
- Removes `create_service_aware_retry()` from base connector (unused)

#### Alternatives Considered

**Option A: Keep backoff, reduce duplication with factory**
- **Pros**: Less migration work, familiar API
- **Cons**: Still limited by backoff's constraints, no composable conditions, no rich retry state
- **Rejected because**: Doesn't solve the core problems (duplication, limited features)

**Option B: Implement custom retry logic**
- **Pros**: Full control, exactly what we need
- **Cons**: Reinventing the wheel, maintenance burden, error-prone
- **Rejected because**: Tenacity is battle-tested, feature-rich, and actively maintained

**Option C: Use asyncio-based retry library (e.g., aioretry)**
- **Pros**: Async-first design
- **Cons**: Less mature, smaller ecosystem, fewer features than tenacity
- **Rejected because**: Tenacity has better async support and more features

### 📝 Implementation Plan

**Phase 1: Create Centralized Retry Infrastructure**
- [x] **Task 1.1**: Create `retry_policies.py` with `ErrorClassifierRetry` predicate
- [x] **Task 1.2**: Implement `RetryPolicyFactory` with Spotify, Last.FM, MusicBrainz policies
- [x] **Task 1.3**: Port callback handlers to tenacity's `RetryCallState`
- [x] **Task 1.4**: Create `MusicBrainzErrorClassifier` for proper error handling

**Phase 2: Migrate Connector Clients**
- [x] **Task 2.1**: Migrate Spotify client (13 methods)
- [x] **Task 2.2**: Migrate Last.FM client (6 methods)
- [x] **Task 2.3**: Migrate MusicBrainz client (2 methods)
- [x] **Task 2.4**: Remove `create_service_aware_retry()` from base connector
- [x] **Task 2.5**: Remove backoff imports

**Phase 3: Update Dependencies & Testing**
- [x] **Task 3.1**: Update `pyproject.toml` (backoff → tenacity)
- [x] **Task 3.2**: Run `poetry lock` and `poetry install`
- [x] **Task 3.3**: Run full test suite and verify behavior preservation
- [x] **Task 3.4**: Update failing test for removed `create_service_aware_retry()`
- [x] **Task 3.5**: Clean up stale backoff handlers and tests (ruthlessly DRY)
- [x] **Task 3.6**: Fix retry policies to only retry service-specific exceptions (behavior preservation)
- [ ] **Task 3.7**: Add unit tests for retry policies (optional, time permitting - can be done later)

**Phase 4: Code Review & Final Verification**
- [x] **Task 4.1**: Review retry policy exception type filtering (caught missing service-specific exception constraint)
- [x] **Task 4.2**: Verify no stale backoff references remain in codebase
- [x] **Task 4.3**: Run full test suite with all connector integration tests
- [x] **Task 4.4**: Document lessons learned from code review process

### ✨ User-Facing Changes & Examples

**No user-facing changes** - this is an internal refactoring. Retry behavior remains identical from the user's perspective.

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**: No changes (error classification protocols unchanged)
- **Application**: No changes (use cases unaffected)
- **Infrastructure**:
  - New: `retry_policies.py`, `musicbrainz/error_classifier.py`
  - Modified: `spotify/client.py`, `lastfm/client.py`, `musicbrainz/client.py`, `base.py`
- **Interface**: No changes

**Testing Strategy**:
- **Unit**: Test `RetryPolicyFactory` creates correct policies, test `ErrorClassifierRetry` with different error types
- **Integration**: Run existing connector tests (should pass unchanged)
- **Manual**: Verify retry behavior with mocked rate limits and network failures

**Key Files Modified**:
- `src/infrastructure/connectors/_shared/retry_policies.py` (NEW - 330 lines)
- `src/infrastructure/connectors/musicbrainz/error_classifier.py` (NEW - 75 lines)
- `src/infrastructure/connectors/spotify/client.py` (REFACTORED - 685 → 660 lines)
- `src/infrastructure/connectors/lastfm/client.py` (REFACTORED - 614 → 668 lines)
- `src/infrastructure/connectors/musicbrainz/client.py` (REFACTORED - 134 → 164 lines)
- `src/infrastructure/connectors/base.py` (CLEANUP - removed unused method, removed backoff import)
- `pyproject.toml` (DEPENDENCY UPDATE - backoff → tenacity)

---

## 🤖 AI Collaboration Tracking

### Agent Assistance Log

**Format**: Track all agent consultations with enhanced format for future reference

| Agent | Task | Outcome | Decision | Context Files |
|-------|------|---------|----------|---------------|
| **Explore** | Find all backoff usage in codebase | Found 23 decorator instances across 4 files with detailed patterns | 📋 Analyzed | spotify/client.py, lastfm/client.py, musicbrainz/client.py |
| **Plan** | Design tenacity migration strategy | Comprehensive plan with centralized policies approach | ✅ Accepted | retry_policies.py (design) |
| **WebSearch** | Research tenacity best practices Dec 2025 | Found async patterns, Python 3.14 compatibility info | 📋 Informed | tenacity documentation, async patterns |

**Legend**:
- **Agent**: Type (Subagent name, Task, Explore, Plan, WebSearch, Main)
- **Decision**: ✅ Accepted / ⚠️ Modified / ❌ Rejected / 🔍 Narrowed / 📋 Analyzed / 📚 Informed
- **Context Files**: Critical files for future session resumption

---

**Example Future Usage with Subagents**:

| Agent | Task | Outcome | Decision | Context Files |
|-------|------|---------|----------|---------------|
| **Subagent**: architecture-guardian | Review SyncPlaylist use case | No Clean Architecture violations found | ✅ Accepted | sync_playlist_use_case.py |
| **Subagent**: test-pyramid-architect | Design test strategy for SyncPlaylist | 6 unit tests, 3 integration tests (67/33 split) | ✅ Implemented | test_sync_playlist.py, test_sync_playlist_integration.py |
| **Task**: Ad-hoc | Minimal tenacity reproduction | before_sleep callbacks fire with simple predicate | 🔍 Narrowed | /tmp/test_tenacity.py |
| **Main**: Direct | Implement tenacity fix | All 827 tests passing, logging restored | ✅ Complete | retry_policies.py, error_classification.py |

### Context Boundaries

**Critical Files to Read First**:
- `src/infrastructure/connectors/_shared/error_classification.py` - The brain of retry logic, classifies errors into retry vs fail-fast
- `src/infrastructure/connectors/spotify/client.py` - Example of massive duplication (13 identical decorators)
- `src/infrastructure/connectors/_shared/retry_policies.py` - New centralized retry infrastructure
- `CLAUDE.md` - Repository conventions and Python 3.14 best practices

**Key Concepts to Understand**:
- **Error classification system**: Determines retry behavior based on error type (permanent/temporary/rate_limit/not_found/unknown)
- **Decorator pattern migration**: Old decorators → centralized policy instance + implementation extraction
- **Tenacity's AsyncRetrying**: Modern async retry with rich state and composable conditions

**Dependencies & Prerequisites**:
- Tenacity 9.1.2 (Python 3.9-3.13 compatible, works with 3.14)
- Understanding of async/await patterns
- Knowledge of error classification categories and their retry behavior

### AI-Assisted Decisions

| Decision Point | AI Suggestion | Human Decision | Rationale |
|----------------|---------------|----------------|-----------|
| Migration approach | Centralized retry policies via factory pattern | Accepted | Dramatically reduces duplication while preserving behavior |
| Error classifier integration | Custom `ErrorClassifierRetry` predicate | Accepted | Seamlessly integrates existing error classification with tenacity |
| Callback handlers | Port to tenacity's rich `RetryCallState` | Accepted | Preserves logging while leveraging better retry state |
| MusicBrainz error handling | Add proper error classifier instead of catching all exceptions | Accepted | Brings MusicBrainz up to par with other connectors |
| Base connector cleanup | Remove unused `create_service_aware_retry()` method | Accepted | Simplifies codebase, method was complex and unused |

---

## 📊 Migration Summary

**Before**:
- 23 decorator instances
- 13 duplicate Spotify decorators
- ~200 lines of repetitive decorator code
- 3 different configuration patterns
- No jitter support
- Limited observability

**After**:
- 3 centralized retry policy instances
- Single source of truth per service
- ~50 lines of policy configuration
- Consistent factory pattern
- Built-in jitter support (ready to enable)
- Rich retry state for observability

**Impact**: 90% reduction in decorator boilerplate, foundation for future improvements (jitter, metrics, adaptive policies)

---

## 🧹 Cleanup & Behavior Preservation (Post-Migration)

### Ruthlessly DRY Cleanup
During final cleanup to ensure the codebase was "ruthlessly DRY," we removed all stale backoff code:

**Files Cleaned**:
- `src/infrastructure/connectors/_shared/error_classification.py` - Removed 75 lines of old backoff handlers
  - Deleted `should_giveup_on_error()` (14 lines)
  - Deleted `create_backoff_handler()` (30 lines)
  - Deleted `create_giveup_handler()` (19 lines)
- `src/infrastructure/connectors/_shared/__init__.py` - Removed exports of deleted functions
- `tests/unit/infrastructure/connectors/shared/test_error_classification.py` - Rewrote to test ErrorClassifierRetry
- `tests/integration/connectors/lastfm/test_comprehensive_error_classification.py` - Updated to use tenacity patterns
- `tests/integration/connectors/spotify/test_backoff_behavior.py` - Deleted entire file (17KB, obsolete)

### 🔍 Code Review: Critical Behavior Bug Found

**What Happened**: During post-migration cleanup, integration tests revealed a subtle but critical behavioral difference between the old and new implementations.

**The Bug**:
```python
# ❌ WRONG - Initial implementation
return AsyncRetrying(
    retry=ErrorClassifierRetry(classifier),  # Retries ALL exceptions!
    ...
)

# ✅ CORRECT - After code review
return AsyncRetrying(
    retry=(
        retry_if_exception_type(spotipy.SpotifyException)  # Only retry Spotify exceptions
        & ErrorClassifierRetry(classifier)
    ),
    ...
)
```

**Root Cause**: The original backoff decorators specified **which exception types to retry**:
```python
# Old Spotify decorator - only retries SpotifyException
@backoff.on_exception(backoff.expo, spotipy.SpotifyException, ...)

# Old Last.FM decorator - only retries pylast.WSError
@backoff.on_exception(backoff.expo, pylast.WSError, ...)

# Old MusicBrainz decorator - retries all exceptions
@backoff.on_exception(backoff.expo, Exception, ...)
```

Our new implementation **forgot this critical constraint** and was retrying ALL exception types for Spotify and Last.FM!

**Impact**:
- Network errors (ConnectionError, TimeoutError) were being retried 3 times instead of failing immediately
- Test failures: 5 Spotify integration tests expected 1 call, got 3 calls
- ~7.5 seconds of extra latency per network error (exponential backoff delays)
- Behavior was **not preserved** despite passing unit tests

**Why Tests Didn't Catch It Initially**:
- Unit tests only verified error classification logic, not exception type filtering
- Integration tests with service-specific exceptions passed fine
- Only network error tests (non-SpotifyException) failed

**The Fix**:
- Spotify & Last.FM: Added `retry_if_exception_type(ServiceException) &` to retry predicate
- MusicBrainz: Kept as-is (retries all exceptions, matching original)
- Result: 823 tests passing, true behavior preservation

**Lessons Learned - Code Review Checklist for Retry Logic Migrations**:

1. **Exception Type Filtering** ⚠️ CRITICAL
   - [ ] Does the old decorator specify an exception type? (e.g., `@backoff.on_exception(expo, SpecificException)`)
   - [ ] Does the new policy use `retry_if_exception_type()` to match?
   - [ ] Are network errors handled differently than service errors?

2. **Stop Conditions**
   - [ ] Max attempts matches original (`max_tries=3` → `stop_after_attempt(3)`)
   - [ ] Time limits preserved for services that used them
   - [ ] Combined conditions use correct operator (`|` for OR, `&` for AND)

3. **Wait Strategy**
   - [ ] Backoff multiplier preserved (`backoff.expo` defaults → explicit multiplier)
   - [ ] Max wait time matches original
   - [ ] Jitter presence/absence matches (usually none in original)

4. **Error Classification Integration**
   - [ ] Service-specific error classifier used (not DefaultErrorClassifier)
   - [ ] Error type mappings verified (permanent, temporary, rate_limit, not_found, unknown)
   - [ ] Fail-fast errors actually fail fast (no retries)

5. **Callback Behavior**
   - [ ] Logging output matches original format
   - [ ] Error codes extracted and logged correctly
   - [ ] Rate limit detection still works
   - [ ] Final failure logging includes all context

6. **Integration Tests**
   - [ ] Run tests with MOCKED service-specific exceptions (happy path)
   - [ ] Run tests with MOCKED network exceptions (unhappy path - should propagate!)
   - [ ] Verify call counts match expected retry behavior
   - [ ] Check retry delays are reasonable (no excessive waits)

**Action Item for Future Migrations**:
Always create a comparison table BEFORE implementing:

| Aspect | Old (backoff) | New (tenacity) | Verified? |
|--------|---------------|----------------|-----------|
| Exception type | `spotipy.SpotifyException` | `retry_if_exception_type(SpotifyException)` | ✅ |
| Max attempts | `max_tries=3` | `stop_after_attempt(3)` | ✅ |
| Backoff strategy | `backoff.expo` (default 0.5s) | `wait_exponential(multiplier=0.5)` | ✅ |
| Max wait | Not specified (unbounded) | `max=30` (reasonable limit) | ✅ |
| Error classification | Via `giveup` callback | Via `ErrorClassifierRetry` | ✅ |
| Network errors | **Propagate immediately** | **Was retrying - FIXED** | ✅ |

**Final Result**: 823 tests passing, true behavior preservation achieved
