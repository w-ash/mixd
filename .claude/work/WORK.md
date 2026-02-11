# 🎯 Active Work Tracker - Last.FM Multi-Artist Fallback & Observability

> [!info] Purpose
> This file tracks active development work on the current epic. For strategic roadmap and completed milestones, see [[ROADMAP.md]].

**Current Initiative**: Last.FM Multi-Artist Fallback & Observability Enhancement
**Status**: `#in-progress` `#backend` `#infrastructure` `#v0.3.0`
**Last Updated**: 2025-12-24

## Progress Overview
- [x] **Multi-Artist Fallback Strategy** ✅ (Complete)
- [x] **Comprehensive Logging & Observability** ✅ (Complete)
- [ ] **Test Coverage (11 TDD tests)** 🔜 (In Progress)
- [ ] **Test Performance Fixes**

---

## 🔜 Epic: Last.FM Multi-Artist Fallback & Observability `#in-progress`

**Goal**: Ensure Last.FM track matching tries all available artists for multi-artist tracks and provides comprehensive logging showing when/why different lookup strategies are attempted.

**Why**:
- **User Impact**: Multi-artist tracks (e.g., "Artist1 feat. Artist2") frequently fail to match on Last.FM because the service lists artists in different orders or formats
- **Observability**: Currently impossible to debug why track lookups fail - no logs show MBID vs artist/title attempts or multi-artist fallback logic
- **Production Debugging**: Critical for understanding API behavior and match failures in production

**Effort**: M (Medium) - ~2-3 hours
- 1 file to modify (operations.py)
- 11 new integration tests
- Test performance improvements

### 🔒 System Behavior Contract

**Guaranteed Behaviors** (MUST NOT break):
- MBID lookup still tried first (if available)
- Artist/title fallback still works for single-artist tracks
- Connection errors still trigger tenacity retries (exponential backoff)
- Track-not-found errors still fail fast (no tenacity retry)
- Empty/None results still returned gracefully

**Safe to Change**:
- Artist iteration order (currently tries artists in order)
- Logging verbosity (adding structured logs is safe)
- Internal loop structure (as long as behavior matches)

### 🤔 Architectural Decision Record

**Status**: Accepted
**Date**: 2025-12-24
**Deciders**: Solo dev after analyzing codebase and user request

#### Context & Problem Statement

Last.FM track matching currently only tries the first artist in multi-artist tracks:
```python
if track.artists and track.title:
    artist_name = track.artists[0].name  # Only first artist!
    result = await self.get_track_info(artist_name, track.title)
```

**Problems**:
1. **Lost Matches**: Tracks with multiple artists fail if Last.FM lists them differently (e.g., "Guest feat. Artist" vs "Artist feat. Guest")
2. **Zero Observability**: No logging shows which lookup strategies are tried or why they fail
3. **Connection vs Not-Found**: Logs don't distinguish between network errors (retry) and track-not-found (try next artist)

**Discovered**: Existing fallback strategy exists (MBID → artist/title) but lacks logging and multi-artist support.

#### Decision

Implement **multi-artist fallback with comprehensive logging**:

1. **Multi-Artist Loop**: Try all artists in order until one succeeds
   - Iterate `track.artists` instead of using only `track.artists[0]`
   - Return immediately on first successful match
   - Log each artist attempt with index and remaining count

2. **Structured Logging**: Add 5 key log points
   - MBID attempt start/result
   - Each artist attempt with metadata (index, total, remaining)
   - Connection error vs not-found distinction
   - Final failure with attempted artists list

3. **Error Handling**: Distinguish retry scenarios
   - **Connection errors**: Let tenacity retry (don't try next artist)
   - **Not-found errors**: Skip to next artist (no tenacity retry)

4. **TDD Implementation**: 11 tests covering all paths
   - 4 MBID fallback tests
   - 4 multi-artist fallback tests
   - 3 error handling tests

#### Consequences

**Positive**:
- **Better Match Rate**: Multi-artist tracks match more often by trying all artists
- **Full Observability**: Logs show exact fallback flow (MBID → artist1 → artist2 → etc.)
- **Production Debugging**: Can see why lookups fail and what was attempted
- **Error Clarity**: Clear distinction between transient errors (retry) and not-found (next artist)

**Negative**:
- **More API Calls**: Multi-artist tracks make N calls instead of 1 (where N = number of artists)
- **Latency Impact**: Each failed artist adds ~1-2s (tenacity retry time)
- **Rate Limit Risk**: More calls per track could hit Last.FM rate limits faster

**Mitigations**:
- First artist usually succeeds (most tracks have 1 artist or Last.FM matches first)
- Structured logging helps identify tracks needing optimization
- Can add max_artists_to_try limit later if needed

**Neutral**:
- Test suite grows by 11 tests (~30-45s of test time)
- Code complexity increases slightly (loop instead of single call)

### Implementation Tasks

**Phase 1: Multi-Artist Strategy**
- [x] Analyze current `get_track_info_intelligent()` implementation
- [x] Replace single artist call with artist loop
- [x] Add structured logging for each artist attempt
- [x] Handle first success (early return)
- [x] Handle all failures (comprehensive warning)

**Phase 2: MBID Logging**
- [x] Add MBID attempt start log
- [x] Add MBID success log
- [x] Add MBID failure + fallback log

**Phase 3: TDD Test Coverage**
- [ ] Create test file: `test_intelligent_track_lookup.py`
- [ ] Implement 4 MBID fallback tests
- [ ] Implement 4 multi-artist fallback tests
- [ ] Implement 3 error handling tests
- [ ] All 11 tests passing

**Phase 4: Test Performance**
- [ ] Add pytest marker definitions to `pyproject.toml`
- [ ] Mark 5 slow tests with `@pytest.mark.slow`
- [ ] Verify fast test suite runs in <1 minute

### Testing Strategy

**Test Categories**:
1. **MBID Fallback** (4 tests): MBID success, MBID→artist, no MBID, both fail
2. **Multi-Artist** (4 tests): First succeeds, second succeeds, all fail, single artist
3. **Error Handling** (3 tests): Connection retry, not-found no retry, error type distinction

**Coverage Goals**:
- All code paths in `get_track_info_intelligent()`
- All log statements fire correctly
- Connection vs not-found behavior verified

### Risk Assessment

**Low Risk**:
- ✅ Only modifies one method in one file
- ✅ Comprehensive test coverage (11 tests)
- ✅ Behavior contract preserved (all existing behavior maintained)

**Medium Risk**:
- ⚠️ API call increase (more calls = more rate limit exposure)
- **Mitigation**: Monitor in production, add max_artists_to_try if needed

**Monitoring**:
- Track multi-artist match success rate
- Monitor Last.FM API call volume
- Watch for rate limit errors in logs

---

## 📝 Implementation Notes

### Changes Made

**File**: `src/infrastructure/connectors/lastfm/operations.py:119-199`

**Before**:
```python
# Fallback to artist/title matching using optimal method
if track.artists and track.title:
    artist_name = track.artists[0].name  # Only tries first!
    result = await self.get_track_info(artist_name, track.title)
    return result
```

**After**:
```python
# Fallback to artist/title matching - try all artists in order
if track.artists and track.title:
    for idx, artist in enumerate(track.artists):
        artist_name = artist.name
        logger.info(
            "Attempting Last.FM lookup via artist/title",
            artist=artist_name,
            title=track.title,
            artist_index=idx,
            total_artists=len(track.artists),
            fallback_from_mbid=bool(mbid),
            track_id=track.id,
        )
        result = await self.get_track_info(artist_name, track.title)
        if result and result.lastfm_title:
            logger.info(
                "Last.FM artist/title lookup successful",
                artist=artist_name,
                artist_index=idx,
                found_title=result.lastfm_title,
                track_id=track.id,
            )
            return result
        logger.info(
            "Last.FM lookup failed with artist, trying next",
            artist=artist_name,
            artist_index=idx,
            remaining_artists=len(track.artists) - idx - 1,
            track_id=track.id,
        )
    # All artists failed
    logger.warning(
        "Last.FM lookup failed with all artists",
        tried_artists=[a.name for a in track.artists],
        title=track.title,
        track_id=track.id,
    )
```

### Next Steps

1. **Create TDD test suite** - 11 tests for comprehensive coverage
2. **Fix test performance** - Mark slow tests, add markers
3. **Verify test suite** - Ensure 839+ tests passing
4. **Archive this file** - Move to `docs/work-archive/` when complete
