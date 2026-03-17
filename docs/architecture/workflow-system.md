# Workflow System

How Narada's database-first workflow architecture ensures data consistency across cross-service operations. This is the most important constraint for anyone writing workflow nodes.

## Critical Design Principle: Database-Centric Operations

**All workflow operations work exclusively on database tracks (`tracks` table), never directly on external connector data.**

This architectural constraint ensures system consistency and enables sophisticated cross-service operations that would be impossible with external-only data.

## Database Schema Relationships

```
External Playlists → Database Persistence → Workflow Operations

┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Spotify         │    │ Database        │    │ Workflows       │
│ Playlist        │───▶│ Persistence     │───▶│ (Enrichment,    │
│                 │    │                 │    │  Sorting, etc.) │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Core Database Tables

### Primary Track Storage
- **`tracks`** - Internal canonical track representations (what workflows operate on)
- **`track_metrics`** - Metrics storage linked to internal tracks by `track_id`
- **`playlists`** - Internal playlist representations

### Connector Integration
- **`connector_tracks`** - External service track representations
- **`track_mappings`** - Links internal tracks to external tracks (many-to-one)
- **`connector_playlists`** - External service playlist representations

## Mandatory Database Persistence Flow

### 1. Source Node Responsibility
Source nodes (e.g., `spotify_playlist_source`) must:
- Fetch external playlist data
- Convert to domain entities (without database IDs)
- **Call `SavePlaylistUseCase` to persist to database**
- Return tracks with populated database IDs

### 2. Track Upsert Strategy
The `TrackUpsertEnrichmentStrategy` ensures database consistency:
```python
# Repository handles upsert automatically via connector ID
saved_track = await self.track_repos.core.save_track(track)
# Returns track with database ID populated
```

### 3. Workflow Operations
All downstream operations (enrichment, sorting, filtering) work with database tracks:
- **Input**: Tracks with `track.id != None`
- **Metrics**: Stored in `track_metrics` table by `track_id`
- **Enrichment**: Uses database track IDs for identity resolution

## Critical Developer Safeguards

### Database ID Requirements
```python
# ✅ Correct: Tracks have database IDs
for track in tracklist.tracks:
    assert track.id is not None, "Track must have database ID"
```

### Error Detection
Common failure pattern - tracks without database IDs:
```python
# ❌ Broken: Enrichment fails silently
if not tracks_with_ids:
    logger.warning("No tracks with database IDs - enrichment skipped")
    return {}
```

### Source Node Pattern
```python
# ✅ Correct source node implementation
async def external_playlist_source(context, config):
    # 1. Fetch external playlist
    external_tracks = await connector.get_playlist(playlist_id)

    # 2. Convert to domain entities
    domain_tracks = [convert_to_domain(track) for track in external_tracks]

    # 3. MANDATORY: Persist to database
    save_command = SavePlaylistCommand(
        tracklist=TrackList(tracks=domain_tracks),
        enrichment_config=EnrichmentConfig(enabled=True),
        persistence_options=PersistenceOptions(operation_type="create_internal"),
    )
    result = await SavePlaylistUseCase().execute(save_command)

    # 4. Return tracks with database IDs
    return {"tracklist": TrackList(tracks=result.enriched_tracks)}
```

## Data Consistency Benefits

### 1. Cross-Service Operations
- Sort Spotify playlist by Last.fm play counts
- Sync likes between services through track matching
- Build sophisticated filters using cross-service data

### 2. Reliable Enrichment
- Enrichment services require database track IDs
- Metrics stored with consistent track references
- Caching and freshness work with stable track identities

### 3. Audit and History
- Complete operation history linked to database tracks
- Temporal data analysis across services
- Reliable backup and restoration

## Common Anti-Patterns to Avoid

### ❌ Operating on External Data Directly
```python
# Wrong: Working with connector tracks directly
for spotify_track in spotify_playlist.tracks:
    # This breaks cross-service operations
    metric = lastfm.get_playcount(spotify_track.id)
```

### ❌ Skipping Database Persistence
```python
# Wrong: Bypassing database persistence
return {"tracklist": TrackList(tracks=external_tracks)}
# These tracks have no database IDs!
```

### ❌ Missing ID Validation
```python
# Wrong: No validation of database IDs
async def enrichment_step(tracklist):
    # Silently fails if tracks lack database IDs
    return await enrich_tracks(tracklist.tracks)
```

## Architecture Validation

To ensure architectural compliance:

1. **All tracks entering workflows must have database IDs**
2. **Source nodes must call `SavePlaylistUseCase`**
3. **Enrichment and metrics operations require database tracks**
4. **Cross-service operations work through database mappings**

This database-first approach is fundamental to Narada's ability to provide unified operations across music services while maintaining data consistency and enabling sophisticated cross-service workflows.
