# Data Model

How Mixd resolves track identity across services, handles temporal data, and manages entity lifecycle.

## Entity Resolution Model

```
tracks (canonical) ↔ track_mappings ↔ connector_tracks (service-specific)
```

**Benefits**:
- Complete service metadata preservation
- Many-to-many track relationships
- Confidence scoring for match quality
- Independent service updates

### Spotify Stale ID Resolution

Spotify tracks can change IDs when relinked (label transfers, catalogue cleanup). The `inward_resolver.py` module handles three resolution scenarios for historical data:

**Three-tier resolution strategy**:
1. **DIRECT**: `GET /tracks/{id}` returns the same ID — 100% confidence, primary mapping
2. **REDIRECT**: `GET /tracks/{old_id}` returns a track with a *different* `.id` — 100% confidence, dual mapping (new ID primary, old ID secondary for cache)
3. **SEARCH FALLBACK**: `GET /tracks/{id}` returns 404 (true dead) — artist+title search at 70% confidence, dual mapping (found ID primary, dead ID secondary)

**Dedup signals** (the only valid merge criteria):
1. **Spotify redirect** — authoritative "these two IDs are the same track"
2. **ISRC match** — same recording by definition (already in `save_track()`)

Name/artist matching is explicitly **not** used for dedup — different versions (live, acoustic, remaster) are different canonical tracks. The search fallback is approximate and flagged with lower confidence.

The secondary mapping in scenarios 2 and 3 ensures future imports with the old ID resolve instantly via the bulk lookup fast path (no API call needed).

## Temporal Data Design

- **Immutable Events**: Play history, sync operations
- **Time-Series Metrics**: Play counts, listener counts
- **Checkpoint System**: Incremental sync state

**Benefits**:
- Complete audit trail
- Efficient incremental operations
- Historical analysis capability

## Hard Delete Pattern

All entities use hard deletion for simplicity and performance. Data recovery relies on external API re-import and database backups.

**Benefits**:
- Simplified queries (no is_deleted filters)
- Better performance (smaller indexes)
- Cleaner data model
- External APIs serve as source of truth for recovery
