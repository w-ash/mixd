# 🎯 Spotify `added_at` Timestamp Preservation - Clean Architecture Refactor

> **Purpose**: Preserve Spotify's `added_at` timestamps (when tracks were added to playlists) for temporal analytics and "sort by date added" functionality.

**Status**: `#in-progress` `#architecture-refactor` `#data-integrity`
**Last Updated**: 2025-10-02
**Progress**: Phases 1-2 complete (Domain + Processing Service) | Repository layer next

---

## 🎯 QUICK START FOR CONTINUATION

**What's Done:**
- ✅ `PlaylistEntry` domain entity created (track + added_at + added_by)
- ✅ `Playlist.entries: list[PlaylistEntry]` refactored (was `tracks: list[Track]`)
- ✅ Processing service returns clean `Playlist` (no more connector_metadata hack)
- ✅ Domain layer: pure, type-safe, zero infrastructure refs

**What's Next:**
1. Update `PlaylistRepository._manage_playlist_tracks(entries)` signature
2. Remove connector_metadata extraction (lines 243-257)
3. Update PlaylistMapper to build entries from DB
4. Fix use cases (create, update) to handle Playlist vs TrackList
5. Fix workflow destination nodes
6. Run tests, fix failures

**Files Modified So Far:**
- `src/domain/entities/playlist.py` (PlaylistEntry added, Playlist refactored)
- `src/application/services/connector_playlist_processing_service.py` (returns Playlist)

**Test Status:** Not run yet - expect many failures until repository is updated

---

## Why This Matters
- **User Value**: Essential for understanding listening habits over time
- **Data Integrity**: Preserve Spotify's source of truth for when tracks were actually added
- **Future Features**: Enables smart playlists, trend analysis, seasonal listening patterns
- **Analytics**: Foundation for "rediscovering" old favorites, tracking interest changes

---

## 📝 Lessons Learned from Interim Solution

### **What Went Well (Hack Approach)**:
- ✅ TDD approach - wrote failing test first, confirmed bug, fixed it
- ✅ Repository already had extraction logic - just needed data flow fix
- ✅ 15 lines of code fixed critical data loss bug

### **Architecture Violations (Why We're Refactoring)**:
- ❌ **Domain pollution**: Using `Track.connector_metadata` to carry playlist-position data
- ❌ **Wrong model**: Track entity mixing song identity with membership metadata
- ❌ **Type safety**: Magic dict keys instead of explicit fields

### **The Real Problem**:
We're conflating two concepts:
- **Track** = Musical recording (song, artist, album)
- **Track-in-playlist** = Membership instance (which song, at which position, added when)

**Interim Solution**: Track.connector_metadata hack (REMOVED in this refactor)
**Proper Solution**: PlaylistEntry domain entity (see below)

---

## 🏗️ Clean Architecture Refactor

> **Philosophy**: Personal project. No backwards compatibility. Always choose optimal, clean-break solution.

### **Domain Model - Before vs After**

**WRONG (Current):**
```python
Playlist.tracks: list[Track]  # ❌ Loses position metadata
Track.connector_metadata      # ❌ Hack to carry added_at
```

**CORRECT (New):**
```python
Playlist.entries: list[PlaylistEntry]  # ✅ Position metadata explicit
PlaylistEntry.track: Track             # ✅ Clean separation
PlaylistEntry.added_at: datetime       # ✅ Type-safe, documented
```

---

## 🎯 Domain Model: PlaylistEntry

### **New Domain Entity**

```python
@define(frozen=True, slots=True)
class PlaylistEntry:
    """A track's membership in a playlist with position-specific metadata.

    Represents the relationship between a Track and a Playlist, capturing
    when and by whom the track was added. Enables temporal analytics and
    position-aware operations.

    Domain Semantics:
    - Track = song identity (immutable attributes like title, artist)
    - PlaylistEntry = membership instance (mutable relationship metadata)

    The same Track can appear multiple times with different PlaylistEntry
    instances, each with independent added_at timestamps and positions.
    """

    track: Track
    added_at: datetime | None = None  # When added to THIS playlist
    added_by: str | None = None       # Who added it (user ID or service)

    # Future fields:
    # position_metadata: dict[str, Any] = field(factory=dict)
    # playlist_play_count: int = 0  # Plays AFTER being added here
    # last_played_in_playlist: datetime | None = None
```

### **Updated Playlist Entity**

```python
@define(frozen=True, slots=True)
class Playlist:
    """Persistent playlist entity with position-aware track memberships."""

    name: str
    entries: list[PlaylistEntry] = field(factory=list)  # CHANGED from tracks
    description: str | None = None
    id: int | None = None
    connector_playlist_identifiers: dict[str, str] = field(factory=dict)
    metadata: dict[str, Any] = field(factory=dict)

    @property
    def tracks(self) -> list[Track]:
        """Extract tracks without position metadata (convenience)."""
        return [entry.track for entry in self.entries]

    def to_tracklist(self) -> TrackList:
        """Convert to TrackList for workflow processing."""
        return TrackList(tracks=self.tracks)

    @classmethod
    def from_tracklist(cls, name: str, tracklist: TrackList,
                      added_at: datetime | None = None) -> "Playlist":
        """Create Playlist from TrackList with uniform added_at."""
        added_at = added_at or datetime.now(UTC)
        return cls(
            name=name,
            entries=[
                PlaylistEntry(track=t, added_at=added_at)
                for t in tracklist.tracks
            ]
        )

    def with_entries(self, entries: list[PlaylistEntry]) -> "Playlist":
        """Create new playlist with updated entries."""
        return self.__class__(
            name=self.name,
            entries=entries,
            description=self.description,
            id=self.id,
            connector_playlist_identifiers=self.connector_playlist_identifiers.copy(),
            metadata=self.metadata.copy(),
        )

    def sort_by_added_at(self, reverse: bool = False) -> "Playlist":
        """Sort playlist by when tracks were added."""
        sorted_entries = sorted(
            self.entries,
            key=lambda e: e.added_at or datetime.min,
            reverse=reverse
        )
        return self.with_entries(sorted_entries)
```

### **Key Distinction - TrackList vs Playlist**

- **TrackList**: Ephemeral processing (workflows, transforms) - stays `list[Track]`
- **Playlist**: Persistent entity (database) - uses `list[PlaylistEntry]`

---

## 📊 Implementation Progress

### ✅ COMPLETED: Phase 1 - Domain Layer

**File**: `src/domain/entities/playlist.py`

**Changes Made:**
1. ✅ Created `PlaylistEntry` entity:
   ```python
   @define(frozen=True, slots=True)
   class PlaylistEntry:
       track: Track
       added_at: datetime | None = None
       added_by: str | None = None
   ```

2. ✅ Updated `Playlist` entity:
   - Changed `tracks: list[Track]` → `entries: list[PlaylistEntry]`
   - Added `tracks` property for convenience (extracts tracks from entries)
   - Added `to_tracklist()` - converts Playlist → TrackList for workflows
   - Added `from_tracklist(name, tracklist, added_at)` - creates Playlist from TrackList
   - Added `with_entries(entries)` - immutable entry updates
   - Added `sort_by_added_at(reverse)` - temporal sorting
   - Updated all methods to use `entries` instead of `tracks`

3. ✅ Deleted `PlaylistTrack` entity (was DB-specific, not proper domain entity)

**Result**: Clean domain model separating Track (song) from PlaylistEntry (membership).

---

### ✅ COMPLETED: Phase 2 - Processing Service

**File**: `src/application/services/connector_playlist_processing_service.py`

**Changes Made:**
1. ✅ Return type changed: `TrackList` → `Playlist`
2. ✅ Removed `Track.connector_metadata` hack - NO MORE MAGIC DICT KEYS!
3. ✅ Clean conversion logic:
   ```python
   # Parse ISO timestamp from Spotify
   added_at = datetime.fromisoformat(playlist_item.added_at) if playlist_item.added_at else None

   # Create PlaylistEntry with track + metadata
   entry = PlaylistEntry(
       track=domain_track,
       added_at=added_at,
       added_by=playlist_item.added_by_id
   )
   playlist_entries.append(entry)
   ```

4. ✅ Return proper Playlist:
   ```python
   return Playlist(
       name=connector_playlist.name,
       entries=playlist_entries,
       description=connector_playlist.description,
       connector_playlist_identifiers={connector_name: connector_playlist_id},
       metadata={...}
   )
   ```

**Result**: Service now creates clean Playlist with PlaylistEntry objects. Zero hacks.

---

### 🔄 IN PROGRESS: Phase 3 - Repository Layer

**Files to modify:**
- `src/infrastructure/persistence/repositories/playlist/core.py`
- `src/infrastructure/persistence/repositories/playlist/mapper.py`

**TODO - Signature Changes:**
```python
# Before (WRONG)
async def _manage_playlist_tracks(
    self,
    playlist_id: int,
    tracks: list[Track],  # ❌
    operation: str = "create"
) -> None:

# After (CORRECT)
async def _manage_playlist_tracks(
    self,
    playlist_id: int,
    entries: list[PlaylistEntry],  # ✅
    operation: str = "create"
) -> None:
```

**TODO - Remove connector_metadata Extraction (lines 243-257):**
```python
# DELETE THIS ❌
added_at = None
if hasattr(track, "connector_metadata") and track.connector_metadata:
    for connector_name, metadata in track.connector_metadata.items():
        if metadata and metadata.get("added_at"):
            try:
                added_at = datetime.fromisoformat(metadata["added_at"])
                ...

# REPLACE WITH ✅
added_at = entry.added_at  # Direct access!
```

**TODO - Update Mapper:**
```python
# PlaylistMapper.to_domain()
async def to_domain(self, db_model: DBPlaylist) -> Playlist:
    entries = [
        PlaylistEntry(
            track=await self._track_mapper.to_domain(pt.track),
            added_at=pt.added_at,
            added_by=pt.metadata.get("added_by") if pt.metadata else None
        )
        for pt in db_model.playlist_tracks
    ]

    return Playlist(
        name=db_model.name,
        entries=entries,  # ✅
        ...
    )
```

---

### 📋 PENDING: Phase 4 - Use Cases

**Files to modify:**
- `src/application/use_cases/create_canonical_playlist.py`
- `src/application/use_cases/update_canonical_playlist.py`

**TODO - CreateCanonicalPlaylistUseCase:**
```python
@define
class CreateCanonicalPlaylistCommand:
    name: str
    source: TrackList | Playlist  # Accept EITHER!
    description: str | None = None

async def execute(...):
    # Handle both input types
    if isinstance(command.source, TrackList):
        playlist = Playlist.from_tracklist(
            name=command.name,
            tracklist=command.source,
            added_at=datetime.now(UTC)
        )
    else:
        playlist = command.source  # Already a Playlist with entries

    await playlist_repo.save_playlist(playlist)
```

**TODO - UpdateCanonicalPlaylistUseCase:**
```python
async def execute(...):
    existing = await playlist_repo.get_playlist_by_id(playlist_id)
    new_tracks = command.tracklist.tracks

    # Match entries: preserve added_at for existing, new timestamp for new tracks
    track_to_entry = {e.track.id: e for e in existing.entries}

    new_entries = []
    for track in new_tracks:
        if track.id in track_to_entry:
            new_entries.append(track_to_entry[track.id])  # Preserve!
        else:
            new_entries.append(PlaylistEntry(track=track, added_at=datetime.now(UTC)))

    updated_playlist = existing.with_entries(new_entries)
    await playlist_repo.update_playlist(updated_playlist)
```

---

### 📋 PENDING: Phase 5 - Workflows

**Files to modify:**
- Destination nodes (~3 files)

**Key Changes:**
- Source/Transform nodes: UNCHANGED (still use TrackList)
- Destination nodes: Convert TrackList → Playlist at boundaries

```python
# Workflow destination
async def execute(...):
    tracklist = context.get_tracklist()

    # Create playlist from workflow result
    playlist = Playlist.from_tracklist(
        name=workflow.output_name,
        tracklist=tracklist,
        added_at=datetime.now(UTC)
    )

    await use_case.create_playlist(playlist)
```

---

### 📋 PENDING: Phase 6 - Diff Engine

**File**: `src/domain/playlist/diff_engine.py`

**TODO - Compare Entries:**
```python
def calculate_diff(
    current: Playlist,  # Has entries
    target: Playlist,   # Has entries
) -> PlaylistDiff:
    # Compare entries (not just tracks)
    # Detect "re-added" (different added_at) vs "moved" (same added_at)
```

---

## 📋 Implementation Plan

### **Phase 1: Domain Layer** 🏗️
**File**: `src/domain/entities/playlist.py`

1. Create PlaylistEntry entity
2. Update Playlist: tracks → entries
3. Add conversion methods (to_tracklist, from_tracklist, with_entries, sort_by_added_at)
4. Handle existing PlaylistTrack entity (delete or rename)

### **Phase 2: Application Services** 🔄
**File**: `src/application/services/connector_playlist_processing_service.py`

**Remove hack:**
```python
# DELETE ❌
track_with_metadata = domain_track.with_connector_metadata(
    connector_name,
    {"added_at": playlist_item.added_at, "added_by": playlist_item.added_by_id}
)
```

**Clean conversion:**
```python
# ADD ✅
entry = PlaylistEntry(
    track=domain_track,
    added_at=datetime.fromisoformat(playlist_item.added_at) if playlist_item.added_at else None,
    added_by=playlist_item.added_by_id
)
```

### **Phase 3: Repository Layer** 💾
**File**: `src/infrastructure/persistence/repositories/playlist/core.py`

1. Update `_manage_playlist_tracks(entries: list[PlaylistEntry])` signature
2. Remove connector_metadata extraction (lines 243-257)
3. Direct extraction: `entry.added_at`
4. Update PlaylistMapper to build entries from DBPlaylistTrack

### **Phase 4: Use Cases** 📦
**Files**: `create_canonical_playlist.py`, `update_canonical_playlist.py`

1. Create: Use `Playlist.from_tracklist()` for TrackList sources
2. Update: Match entries to preserve existing added_at
3. Accept `TrackList | Playlist` in commands

### **Phase 5: Workflows** 🔀
**Files**: Destination nodes

- Source/Transform nodes: UNCHANGED (use TrackList)
- Destination nodes: Convert TrackList → Playlist at boundaries

### **Phase 6: Diff Engine** 🔄
**File**: `src/domain/playlist/diff_engine.py`

- Compare `list[PlaylistEntry]` instead of `list[Track]`
- Detect "re-added" (different added_at) vs "moved" (same added_at)
- Preserve metadata during operations

---

## 🎁 Features Enabled

### **Immediate Benefits**
1. ✅ Sort by date added: `playlist.sort_by_added_at()`
2. ✅ Temporal queries: "tracks added in January 2024"
3. ✅ Duplicate handling: Same track, different positions, independent added_at
4. ✅ Type safety: No magic dict keys
5. ✅ Clean architecture: Track=song, PlaylistEntry=membership

### **Future Features Unlocked**
1. **Staleness Metrics**: `days_since_added` property
2. **Smart Playlists**: "Rediscover old tracks", "Fresh additions"
3. **Playlist Analytics**: Adding habits over time, seasonal patterns
4. **Position-Aware Plays**: Plays that occurred AFTER track was added
5. **Workflow Enhancements**: Preserve manual adds, refresh auto-adds

---

## 🧪 Testing Strategy

**New Integration Tests**: `tests/integration/test_playlist_entry_architecture.py`
- `test_playlist_entry_preserves_added_at()`
- `test_duplicate_tracks_have_independent_entries()`
- `test_workflow_to_playlist_conversion()`
- `test_update_preserves_existing_added_at()`
- `test_sort_by_added_at()`

**Repository Tests**:
- `test_save_playlist_with_entries()`
- `test_load_playlist_reconstructs_entries()`

---

## 📊 Impact Summary

| Component | Files | Breaking? | Complexity |
|-----------|-------|-----------|------------|
| Domain | 1 | Yes | Low |
| Processing Service | 1 | Yes | Low |
| Repository | 1 | Yes | Medium |
| Use Cases | 2 | Yes | Low |
| Workflows | ~3 | Minimal | Low |
| Tests | 5 new + 3 updated | N/A | Medium |
| **TOTAL** | **~13** | **Clean breaks** | **2-3 days** |

---

## ✅ Definition of Done

### **Architecture**
- [x] PlaylistEntry entity created in domain layer ✅ DONE
- [x] Playlist has `entries: list[PlaylistEntry]` ✅ DONE
- [x] TrackList stays pure (`tracks: list[Track]`) ✅ DONE
- [ ] Repository maps PlaylistEntry → DBPlaylistTrack directly (IN PROGRESS)
- [x] Zero uses of connector_metadata hack in processing service ✅ DONE

### **Functionality**
- [ ] Spotify backup preserves added_at timestamps (blocked on repo)
- [ ] Workflow → Playlist conversion creates entries (blocked on use cases)
- [ ] Update operations preserve existing added_at (blocked on use cases)
- [x] Sort by date added implemented: `playlist.sort_by_added_at()` ✅ DONE
- [x] Duplicate tracks have independent entries (domain model supports it) ✅ DONE

### **Code Quality**
- [x] Type-safe (no magic dict keys in processing service) ✅ DONE
- [ ] Integration tests pass (not yet run - expect failures)
- [x] Domain layer pure (no infrastructure refs) ✅ DONE
- [ ] All existing tests updated and passing (expect many failures)

### **Future-Ready**
- [x] Foundation for staleness metrics (PlaylistEntry.added_at) ✅ DONE
- [x] Foundation for position-aware plays ✅ DONE
- [x] Foundation for smart playlist features ✅ DONE
- [x] Clean foundation for temporal analytics ✅ DONE

---

## 🚧 Current State Summary

### **What's Working:**
- ✅ Domain entities are clean and type-safe
- ✅ PlaylistEntry properly models track membership
- ✅ Processing service creates proper Playlist objects
- ✅ No more connector_metadata hacks in service layer

### **What's Broken (Expected):**
- ❌ Repository still expects `list[Track]`, gets `list[PlaylistEntry]` → **Type errors**
- ❌ Use cases still pass TrackList, repo expects Playlist → **Type errors**
- ❌ Workflows still use old patterns → **Runtime errors**
- ❌ All playlist integration tests → **Will fail**

### **Next Steps for Continuation:**
1. **Phase 3**: Update repository signatures and extraction logic
2. **Phase 4**: Update use cases to handle Playlist vs TrackList
3. **Phase 5**: Update workflow destination nodes
4. **Phase 6**: Update diff engine
5. **Testing**: Fix all broken tests
6. **Cleanup**: Remove old test files, update docs

### **Estimated Remaining Work:**
- Repository layer: 2-3 hours
- Use cases: 1-2 hours
- Workflows: 1 hour
- Diff engine: 1 hour
- Testing/fixes: 2-3 hours
- **Total**: ~8-12 hours remaining
