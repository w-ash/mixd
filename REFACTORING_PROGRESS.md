# Narada Modernization & Refactoring Progress

**Branch:** `refactor/remove-duplication`
**Started:** 2025-11-29
**Last Updated:** 2025-11-29
**Status:** Phase 3 Complete, Phase 4 In Progress

---

## 🎯 Mission: November 2025 Modernization

### Why We're Doing This

This codebase was analyzed for modernization opportunities against November 2025 Python standards. The analysis revealed:

1. **High Baseline Quality (9.2/10)** - Already excellent DDD/hexagonal architecture with zero layer violations
2. **Specific Improvement Areas** - Limited to attrs usage patterns, minor duplication, and type safety enhancements
3. **Goal** - Modernize to 2025 best practices, eliminate ALL duplication, perfect attrs usage

### Key Principles

- **Ruthlessly DRY** - Single code path for any functionality
- **Modern Python 3.13+** - Use latest language features (TypeIs, @override, match statements)
- **2025 attrs Best Practices** - Construction-time validation, frozen/slots everywhere, kw_only for safety
- **Perfect Type Safety** - Zero type: ignore comments, use TypeIs guards
- **Test Pyramid** - 60% unit (pure logic), 35% integration (I/O), 5% E2E

### Architecture Context

**Hexagonal Architecture (Clean):**
```
Interface → Application → Domain ← Infrastructure
```

**Domain Layer Rules:**
- Zero external dependencies (no SQLAlchemy, no HTTP libs)
- Pure business logic only
- Defines protocols/interfaces that infrastructure implements

**Application Layer:**
- Orchestrates use cases
- Uses `async with uow:` for transactions
- Constructor injection (no service locator pattern)

**Testing Architecture:**
- **Unit Tests (60%+):** Pure business logic, no database, no I/O, fast
- **Integration Tests (35%):** Repository/database interactions, async fixtures
- **E2E Tests (5%):** Full workflows through CLI/workflows
- **Never test framework behavior** - Only test OUR business logic

---

## 📊 Current Status

**Commit:** `4d1cc91` - "refactor: replace validate() methods with attrs field validators (Phase 1)"
**Tests:** 627/627 passing ✅
**Production Lines:** ~31,067 (-81 from baseline of 31,148)
**Type Errors:** 0 in refactored files ✅

---

## ✅ Modernization Roadmap

### ✅ Phase 0: Code Formatting (COMPLETE)
**Commit:** `9dc3a84`
**What:** Applied ruff formatting to connector files
**Why:** Consistency baseline before refactoring
**Impact:** 0 lines (formatting only)

**Checklist:**
- [x] Apply ruff formatting to connector error classifiers
- [x] Apply ruff formatting to Spotify operations
- [x] Verify no logic changes
- [x] Commit

---

### ✅ Phase 1: attrs Field Validators (COMPLETE)
**Commit:** `4d1cc91`
**What:** Replace manual `validate()` methods with attrs field validators
**Why:** Follow 2025 attrs best practices (fail-fast construction-time validation)
**Impact:** -81 net production lines

**Context:**
The codebase had 8 use case command classes with manual `validate()` methods that checked field presence, ranges, and choices. This was:
- Not following 2025 attrs best practices
- Allowing invalid objects to be constructed
- Duplicating validation logic (~100 lines total)

**Solution:**
Created reusable attrs field validators that execute at construction time, making it impossible to create invalid command objects.

**Checklist:**
- [x] Create `command_validators.py` with 8 reusable validators (+228 lines)
  - [x] `non_empty_string()` - validates required strings
  - [x] `positive_int_in_range()` - validates bounded integers
  - [x] `optional_positive_int()` - validates optional positive values
  - [x] `optional_in_choices()` - validates enum/choice fields
  - [x] `tracklist_has_tracks_or_metadata()` - validates TrackList
  - [x] `api_batch_size_validator()` - validates against settings
  - [x] Combinator utilities (and_, optional, instance_of)
- [x] Write 30 comprehensive validator tests (+433 lines)
- [x] Refactor 8 use case commands to use field validators (-100 lines):
  - [x] `delete_canonical_playlist.py` (-11 lines)
  - [x] `read_canonical_playlist.py` (-11 lines)
  - [x] `get_liked_tracks.py` (-10 lines)
  - [x] `get_played_tracks.py` (-17 lines)
  - [x] `create_connector_playlist.py` (-16 lines)
  - [x] `create_canonical_playlist.py` (-13 lines)
  - [x] `update_canonical_playlist.py` (-13 lines)
  - [x] `update_connector_playlist.py` (-9 lines)
- [x] Update 16 existing tests for construction-time validation
- [x] Verify all 627 tests pass
- [x] Run type checking (0 errors)
- [x] Commit with detailed message

**Files Changed:**
```
src/application/use_cases/_shared/command_validators.py           +228 lines (NEW)
tests/unit/application/use_cases/_shared/test_command_validators.py  +433 lines (NEW)
src/application/use_cases/*.py                                    -100 lines (8 files)
tests/unit/application/use_cases/test_get_*.py                    ~40 lines (2 files updated)
```

**Key Learning:**
attrs field validators > BaseCommand base class. Construction-time validation prevents invalid state and provides better error messages.

---

### ✅ Phase 2: Conversion Utilities Consolidation (COMPLETE - SKIPPED)
**Decision:** SKIPPED - Differences are semantic, not duplication
**Impact:** 0 lines (no changes needed)
**What:** Analyzed conversion logic across 3 connector files for consolidation opportunities
**Why:** Verify DRY principle - ensure no duplicate conversion logic

**Context:**
Initial analysis suggested ~60 lines of duplication in:
- `spotify/conversions.py` (170 lines)
- `lastfm/conversions.py` (291 lines)
- `musicbrainz/conversions.py` (195 lines)

**Analysis Findings:**
After detailed line-by-line review, discovered only **3 lines of true duplication**:
- `datetime.now(UTC)` for `last_updated` field (appears once per file)

**Why Differences Are Semantic (Not Duplication):**
- **Spotify:** `artists = [Artist(name=a["name"]) for a in track["artists"]]` - Simple API list
- **LastFM:** Match statement for dict/string variants - API inconsistency handling
- **MusicBrainz:** Nested iteration through artist-credit objects - Complex API structure

Each connector handles fundamentally different API response formats. What looks like "similar code" is actually service-specific adaptation logic.

**Decision Rationale:**
- Only 3 lines of TRUE duplication (threshold was <20 lines)
- Differences reflect genuine API format variations
- Consolidation would create abstraction without value
- Service-specific code aids debugging and maintenance

**Checklist:**
- [x] Read and analyze all 3 conversion files in detail
- [x] Identify TRUE duplication (not just similar-looking code)
- [x] **Decision:** <20 lines of true duplication → SKIP this phase
- [x] Document analysis and decision in this file

**Outcome:**
Phase SKIPPED with documented reasoning. Conversion files are correctly service-specific.

---

### ✅ Phase 3: @dataclass → @define Migration (COMPLETE)
**Impact:** 0 net lines (consistency improvement only)
**What:** Converted last 3 @dataclass usages to @define
**Why:** Achieve 100% attrs consistency across codebase

**Context:**
Found exactly 3 files still using standard library `@dataclass`:
1. `application/workflows/context.py:331` - `ConcreteWorkflowContext`
2. `application/workflows/node_context.py:22` - `NodeContext`
3. `infrastructure/connectors/_shared/rate_limited_batch_processor.py:51` - `WorkItem`

These were the last remaining dataclass usages in a codebase that's 98% attrs.

**Changes Made:**
- **ConcreteWorkflowContext**: `@dataclass` → `@define(slots=True)` (NOT frozen - has mutable shared_session)
- **NodeContext**: `@dataclass(frozen=True)` → `@define(frozen=True, slots=True)` (custom __init__ works correctly)
- **WorkItem**: `@dataclass` → `@define(frozen=True, slots=True)` + `field(default_factory=...)` → `field(factory=...)`

**Checklist:**
- [x] Convert `application/workflows/context.py`
  - [x] Change `@dataclass` → `@define(slots=True)` (mutable for shared_session)
  - [x] Update import: `from dataclasses import dataclass` → `from attrs import define`
- [x] Convert `application/workflows/node_context.py`
  - [x] Change `@dataclass(frozen=True)` → `@define(frozen=True, slots=True)`
  - [x] Verify custom `__init__` with `object.__setattr__` still works
  - [x] Update import
- [x] Convert `infrastructure/connectors/_shared/rate_limited_batch_processor.py`
  - [x] Change `@dataclass` → `@define(frozen=True, slots=True)`
  - [x] Change `field(default_factory=time.time)` → `field(factory=time.time)`
  - [x] Update import: add `field` to attrs import
- [x] Run all tests (627 passing ✅)
- [x] Run type checking (0 errors ✅)
- [x] Commit with detailed message

**Key Learning:**
- attrs `factory=` replaces `default_factory=`
- Dependency containers should use `@define(slots=True)` not frozen (mutable state)
- Frozen classes with custom `__init__` work correctly with attrs when using `object.__setattr__`

**Result:** Codebase now 100% attrs - zero dataclass usages remain.

---

### 🚧 Phase 4: Add Missing frozen=True/slots=True (PENDING)
**Target:** 0 net lines (+10% memory efficiency)
**What:** Add missing attrs parameters to 24 classes
**Why:** Performance and immutability guarantees

**Context:**
Found 24 classes with incomplete attrs decorators:
- Some have `@define(frozen=True)` but missing `slots=True`
- Some have `@define(slots=True)` but missing `frozen=True`

**Best Practice:**
- **Value objects/entities:** `@define(frozen=True, slots=True)` - Immutable
- **Services:** `@define(slots=True)` only - Mutable state allowed

**Checklist:**
- [ ] Audit all 24 classes to determine which should be immutable
- [ ] For value objects (Track, Playlist, commands):
  - [ ] Add both `frozen=True, slots=True`
- [ ] For services (coordinators, processors):
  - [ ] Add `slots=True` only (keep mutable)
- [ ] Files to check:
  - [ ] `domain/entities/summary_metrics.py`
  - [ ] `domain/entities/track.py`
  - [ ] `domain/services/progress_coordinator.py`
  - [ ] `infrastructure/services/playlist_operation_service.py`
  - [ ] Plus 20 more files (see ultrathink analysis in chat)
- [ ] Run all tests
- [ ] Verify no `FrozenInstanceError` for legitimately mutable classes
- [ ] Commit: "refactor: add missing frozen/slots attrs parameters"

---

### 🚧 Phase 5: Add kw_only=True for API Safety (PENDING)
**Target:** 0 net lines (API clarity improvement)
**What:** Add `kw_only=True` to command classes with 3+ fields
**Why:** Prevent fragile positional arguments, force explicit field names

**Context:**
Currently ALL command classes allow positional arguments:
```python
# Fragile (current):
cmd = CreatePlaylistCommand("name", tracklist, "description", metadata, timestamp)

# Safe (after kw_only):
cmd = CreatePlaylistCommand(
    name="name",
    tracklist=tracklist,
    description="description"
)
```

**Checklist:**
- [ ] Add `kw_only=True` to all command classes with 3+ fields (~30-40 files)
- [ ] Search codebase for any positional usage of these commands
- [ ] Convert positional → keyword arguments if found
- [ ] Run all tests (some might fail if using positional args)
- [ ] Fix any test failures
- [ ] Commit: "refactor: add kw_only=True to command classes for API safety"

**Search Command:**
```bash
grep -r "@define(frozen=True, slots=True)" src/application/use_cases/ | wc -l
```

---

### 🚧 Phase 6: attrs Converters (PENDING)
**Target:** -20 net lines
**What:** Add `field(converter=...)` for automatic type coercion
**Why:** Eliminate manual conversion code

**Context:**
Connector conversion files have manual type coercion:
```python
duration_ms = int(raw_data.get("duration", 0))  # Manual conversion
```

Can use attrs converters:
```python
duration_ms: int = field(converter=int)  # Automatic conversion
```

**Checklist:**
- [ ] Identify manual type conversions in connector conversion files
- [ ] Add converters to ConnectorTrack/ConnectorPlaylist classes:
  - [ ] `duration_ms: int = field(converter=int)`
  - [ ] `popularity: int | None = field(converter=optional(int))`
- [ ] Create custom converters for complex patterns (date parsing)
- [ ] Remove manual conversion code
- [ ] Run all tests
- [ ] Commit: "refactor: use attrs converters for automatic type coercion"

---

### 🚧 Phase 7: TypeIs Type Guards (PENDING)
**Target:** -20 `# type: ignore` comments
**What:** Replace `hasattr()` + `# type: ignore` with TypeIs guards
**Why:** Type safety without suppressing type checker

**Context:**
Found 34 `# type: ignore` comments in codebase. Many can be replaced with TypeIs type guards (Python 3.13 feature):
```python
# Before:
if hasattr(obj, 'field'):  # type: ignore
    return obj.field  # type: ignore

# After:
def has_field(obj: Any) -> TypeIs[ObjWithField]:
    return hasattr(obj, 'field')

if has_field(obj):
    return obj.field  # Type checker knows obj has 'field'!
```

**Checklist:**
- [ ] Review all 34 `# type: ignore` comments
- [ ] Create TypeIs guards for common patterns
- [ ] Replace hasattr patterns with type guards
- [ ] Target: Remove 20 of 34 type ignore comments
- [ ] Run basedpyright to verify 0 new errors
- [ ] Commit: "refactor: use TypeIs type guards for better type safety"

**Search Commands:**
```bash
grep -r "# type: ignore" src/ | wc -l
grep -r "hasattr(" src/ | wc -l
```

---

### 🚧 Phase 8: @override Decorators (PENDING)
**Target:** +60 decorators (refactoring safety)
**What:** Add `@override` to all method overrides
**Why:** Type checker validates we're actually overriding a parent method

**Context:**
Python 3.12+ has `@override` decorator. When refactoring, if parent method is renamed, type checker will catch overrides that are now orphaned.

**Checklist:**
- [ ] Add `@override` to repository implementations:
  - [ ] All `get_by_id()`, `get_by_ids()`, `save()` overrides
- [ ] Add to connector error classifier overrides:
  - [ ] All `classify_http_status()`, `classify_text_patterns()` overrides
- [ ] Add to matching provider overrides
- [ ] Estimate: 50-70 total @override decorators
- [ ] Run basedpyright to verify all overrides are valid
- [ ] Commit: "refactor: add @override decorators for refactoring safety"

**Pattern:**
```python
from typing import override

class SpotifyRepo(BaseRepository[DBTrack, Track]):
    @override
    async def get_by_ids(self, ids: list[str]) -> list[Track]:
        ...
```

---

### 🚧 Phase 9: Service Layer Audit (PENDING)
**Target:** Architecture clarity
**What:** Clarify `application/services/` vs `infrastructure/services/`
**Why:** Currently unclear which services belong where

**Context:**
Two service directories exist:
- `application/services/` - 8 files (orchestration)
- `infrastructure/services/` - 7 files (adapters)

**Hexagonal Architecture Rules:**
- **Application services:** Orchestrate multiple use cases/domain services, no I/O
- **Infrastructure services:** Implement domain protocols, adapter pattern, handle I/O

**Checklist:**
- [ ] Review all 8 files in `application/services/`
  - [ ] Verify they orchestrate business logic only
  - [ ] No direct database or HTTP calls
- [ ] Review all 7 files in `infrastructure/services/`
  - [ ] Verify they implement domain protocols
  - [ ] Check they're adapters for external systems
- [ ] Move any misplaced files
- [ ] Document service layer boundaries in ARCHITECTURE.md
- [ ] Commit if any files moved: "refactor: reorganize service layer for clarity"

**Decision:** May find no changes needed - that's OK, clarity achieved through audit.

---

### 🚧 Phase 10: Cleanup (PENDING)
**Target:** Code hygiene
**What:** Resolve TODOs, unused imports, stub files
**Why:** Complete modernization with clean codebase

**Checklist:**
- [ ] Resolve 3 TODO/FIXME comments:
  - [ ] Check `pyproject.toml`
  - [ ] Check `tests/unit/domain/test_playlist_operations.py`
  - [ ] Check `src/infrastructure/connectors/musicbrainz/conversions.py`
- [ ] Run `poetry run ruff check . --select F401` for unused imports
- [ ] Fix any unused imports found
- [ ] Apple Music decision:
  - [ ] Either: Complete Apple Music connector implementation
  - [ ] Or: Remove `apple_music/error_classifier.py` stub
- [ ] Final verification:
  - [ ] All tests pass
  - [ ] 0 type errors
  - [ ] 0 linting errors
- [ ] Commit: "chore: resolve TODOs and cleanup unused code"

---

### 🚧 Phase 11: Test Suite Review (PENDING - FINAL CRITICAL STEP)
**Target:** Ensure test quality and pyramid compliance
**What:** Review all new test files to avoid over-testing
**Why:** Tests should test OUR business logic, not framework behavior

**Context:**
During modernization, we added:
- `test_command_validators.py` - 30 tests (433 lines)
- Updated 16 existing tests

**Testing Anti-Patterns to Check:**
❌ Testing attrs framework behavior (e.g., testing that validators raise ValueError)
❌ Testing Python language features (e.g., testing that frozen classes can't be modified)
❌ Testing library functionality (e.g., testing that match statements work)
✅ Testing OUR validation rules (e.g., limit must be 1-10000 for business reason)
✅ Testing OUR business logic (e.g., playlist diff calculation)
✅ Testing OUR integration patterns (e.g., repository UnitOfWork transaction)

**Test Pyramid Check:**
- Unit tests should be ~60% - Pure logic, no I/O, fast
- Integration tests should be ~35% - Database/repository interactions
- E2E tests should be ~5% - Full CLI/workflow execution

**Checklist:**
- [ ] Review `test_command_validators.py` (30 tests):
  - [ ] Are we testing OUR validators or attrs framework?
  - [ ] Do tests validate business rules or just technical validation?
  - [ ] Remove any tests that just verify attrs works
  - [ ] Keep tests that verify our specific validation rules (e.g., limit 1-10000)
- [ ] Review updated test files:
  - [ ] Verify construction-time validation tests are valuable
  - [ ] Remove any redundant tests
- [ ] Check test pyramid balance:
  - [ ] Count unit vs integration vs E2E tests
  - [ ] Verify 60/35/5 split approximately
- [ ] Document testing patterns for future:
  - [ ] What makes a good unit test in this codebase
  - [ ] When to write integration vs unit tests
  - [ ] Examples of over-testing to avoid
- [ ] If changes made, commit: "test: refine test suite for quality and pyramid compliance"
- [ ] If no changes needed, document: "✅ Test suite review complete - all tests valuable"

**Key Question for Each Test:**
*"Are we testing OUR code's behavior, or are we testing that Python/attrs works?"*

---

## 📈 Expected Final Impact

| Metric | Current | Target | Change |
|--------|---------|--------|--------|
| Production Lines | 31,067 | ~30,777 | -290 |
| Test Lines | ~860 tests | ~860 tests | Refined quality |
| Type Errors | 0 | 0 | Maintained |
| attrs Consistency | 97% | 100% | +3% |
| Type Safety | Good | Excellent | TypeIs, @override |
| Code Duplication | Minimal | Zero | Perfect DRY |

---

## 🔒 Quality Gates (Every Commit)

Run before EVERY commit:
```bash
# 1. Tests
poetry run pytest tests/unit/ -q

# 2. Type checking
poetry run basedpyright src/

# 3. Linting
poetry run ruff check .

# 4. Formatting
poetry run ruff format .
```

All must pass with:
- ✅ 627+ tests passing
- ✅ 0 type errors
- ✅ 0 linting errors
- ✅ Consistent formatting

---

## 📚 Reference: Previously Completed Refactoring

These phases were completed in earlier sessions (November 25-26):

### HTTPErrorClassifier Base Class
**Commit:** `66f4798`
**Impact:** -153 net lines
Extracted shared HTTP error classification from 3 connectors (Spotify, LastFM, Apple Music). Created base class with modern match statements for status codes and text pattern detection.

### BaseMatchingProvider Template Method
**Commit:** `7d1cc81`
**Impact:** +93 net lines (but eliminated ~240 lines of duplication)
Extracted template method pattern for track matching workflow (ISRC → artist/title fallback) from 3 matching providers. Preserved service-specific optimizations.

---

## 🚀 How to Continue This Work

1. **Checkout the branch:**
   ```bash
   git checkout refactor/remove-duplication
   git pull
   ```

2. **Verify current state:**
   ```bash
   poetry run pytest tests/unit/ -q  # Should show 627 passing
   ```

3. **Pick up where we left off:**
   - Currently: Phase 2 (Conversion Utilities) in progress
   - Check the Phase 2 checklist above
   - Follow TDD approach: tests first, then implementation

4. **Before starting work:**
   - Read the "Context" section for the phase
   - Understand the "Why" behind the change
   - Follow the checklist step-by-step

5. **After completing a phase:**
   - Mark checklist items complete
   - Update "Last Updated" date at top
   - Commit progress file with phase changes
   - Move to next phase

6. **Don't skip Phase 11:**
   - The test review is CRITICAL
   - Ensures we're not over-testing framework behavior
   - Maintains test pyramid and architecture best practices

---

## 💡 Key Architectural Principles

Remember these when making changes:

1. **Domain Layer is Sacred**
   - Zero external dependencies
   - Pure business logic only
   - Defines protocols, infrastructure implements them

2. **Fail-Fast Validation**
   - Use attrs validators for construction-time checks
   - Invalid objects should be impossible to create

3. **Test Pyramid**
   - Unit tests: Pure logic, no I/O
   - Integration tests: Repository/database
   - E2E tests: Full workflows
   - Never test framework behavior

4. **Modern Python 3.13+**
   - Use TypeIs, @override, match statements
   - No Union[], Optional[], typing.Generic
   - Use X | Y, class Foo[T]:

5. **Single Responsibility**
   - One code path per functionality
   - If you see duplication, extract it
   - If you see multiple ways to do something, consolidate

---

**Questions?** Review the completed Phase 1 as a pattern to follow. Check git log for commit message examples. All quality gates must pass before committing.
