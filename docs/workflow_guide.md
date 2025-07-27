# Narada Workflow Architecture Guide

**Version**: 2.0 (Clean Architecture Complete)  
**Status**: Production-ready with sophisticated playlist update capabilities

## Core Concepts

Narada's workflow architecture enables declarative transformation pipelines through a clean separation of node definition from execution logic. Built on Clean Architecture principles with comprehensive test coverage, this system provides enterprise-grade playlist management capabilities including sophisticated differential updates, conflict resolution, and cross-platform synchronization.

### Architectural Principles

1. **Separation of Concerns** - Workflow definitions describe *what* should happen, not *how* it happens
2. **Compositional Design** - Simple nodes combine to create complex behaviors
3. **Directed Acyclic Graphs** - Tasks execute in dependency order without circular references
4. **Immutable Data Flow** - Each transformation produces new state rather than mutating existing state
5. **Standardized Interfaces** - Nodes follow consistent contracts for composability
6. **Registry-Based Discovery** - Transform implementations are registered in a central registry for maintainability

## Workflow JSON Structure

A workflow is defined in JSON as a directed acyclic graph (DAG) of tasks:

```json
{
  "id": "workflow_id",
  "name": "Human-Readable Name",
  "description": "Workflow purpose description",
  "version": "1.0",
  "tasks": [
    {
      "id": "task_unique_id",
      "type": "node.type",
      "config": {
        "key": "value"
      },
      "upstream": ["dependency_task_id"]
    }
  ]
}
```

### Key Elements

- **id**: Unique identifier for the workflow
- **name**: Human-readable workflow name
- **description**: Purpose and behavior description
- **version**: Semantic version for tracking changes
- **tasks**: Array of task definitions that form the execution graph

### Task Definition

- **id**: Unique identifier within this workflow
- **type**: Node type that implements the behavior
- **config**: Node-specific configuration
- **upstream**: Array of task IDs that must complete before this task executes

## Node Reference

### Source Nodes

| Node Type | Description | Configuration |
|----------------|-------------|--------------|
| `source.playlist` | **Universal playlist source with smart ID resolution** | `playlist_id`: Playlist identifier (required)<br>`connector`: Optional connector name ("spotify", "apple_music", etc.)<br><br>**Smart ID Resolution:**<br>• No connector: `playlist_id` is canonical ID, reads from database<br>• With connector: `playlist_id` is external ID, checks for existing mapping<br>  - If exists: updates existing canonical playlist<br>  - If not exists: creates new canonical playlist with connector mapping |

### Enricher Nodes

| Node Type | Description | Configuration |
|----------------|-------------|--------------|
| `enricher.lastfm` | Resolves tracks to Last.fm and fetches play counts | `username`: Optional Last.fm username<br>`batch_size`: Optional batch size for requests<br>`concurrency`: Optional concurrency limit |
| `enricher.spotify` | Enriches tracks with Spotify popularity and explicit flags | `max_age_hours`: Optional freshness requirement for cached data |
| `enricher.play_history` | Enriches tracks with play counts and listening history from internal database | `metrics`: Array of metrics to include ["total_plays", "last_played_dates", "period_plays"]<br>`period_days`: Number of days back for period-based metrics |

### Filter Nodes

| Node Type | Description | Configuration |
|----------------|-------------|--------------|
| `filter.deduplicate` | Removes duplicate tracks | (No configuration required) |
| `filter.by_release_date` | Filters tracks by release date | `max_age_days`: Maximum age in days<br>`min_age_days`: Minimum age in days |
| `filter.by_tracks` | Excludes tracks from input that are present in exclusion source | `exclusion_source`: Task ID of exclusion source |
| `filter.by_artists` | Excludes tracks whose artists appear in exclusion source | `exclusion_source`: Task ID of exclusion source<br>`exclude_all_artists`: Boolean, if true, excludes tracks if any artist is present in the exclusion source |
| `filter.by_metric` | Filters tracks based on metric value range | `metric_name`: Metric to filter by<br>`min_value`: Minimum value (inclusive)<br>`max_value`: Maximum value (inclusive)<br>`include_missing`: Whether to include tracks without the metric |
| `filter.by_play_history` | **Advanced play history filtering with flexible date and play count constraints** | `min_plays`: Minimum play count (inclusive)<br>`max_plays`: Maximum play count (inclusive)<br>`after_date`: Earliest date for play history (absolute)<br>`before_date`: Latest date for play history (absolute)<br>`days_back`: Number of days back from now (relative)<br>`days_forward`: Number of days forward from now (relative)<br>`include_missing`: Include tracks with no play history<br>**Note**: At least one constraint required. Relative dates take precedence over absolute dates. |

### Sorter Nodes

| Node Type | Description | Configuration |
|----------------|-------------|--------------|
| `sorter.by_metric` | Sorts tracks by any metric specified in config | `metric_name`: Name of metric to sort by (e.g., "lastfm_user_playcount", "lastfm_global_playcount", "lastfm_listeners", "spotify_popularity")<br>`reverse`: Boolean to reverse sort order |

### Selector Nodes

| Node Type | Description | Configuration |
|----------------|-------------|--------------|
| `selector.limit_tracks` | Limits playlist to specified number of tracks | `count`: Maximum number of tracks<br>`method`: Selection method (`first`, `last`, or `random`) |

### Combiner Nodes

| Node Type | Description | Configuration |
|----------------|-------------|--------------|
| `combiner.merge_playlists` | Combines multiple playlists into one | `sources`: Array of task IDs to combine |
| `combiner.concatenate_playlists` | Joins playlists in specified order | `order`: Array of task IDs in desired concatenation order |
| `combiner.interleave_playlists` | Interleaves tracks from multiple playlists | `sources`: Array of task IDs to interleave |

### Destination Nodes

| Node Type | Description | Configuration |
|----------------|-------------|--------------|
| `destination.create_playlist` | **Creates playlists with optional connector sync** | `name`: Playlist name (required)<br>`description`: Optional playlist description<br>`connector`: Optional connector name ("spotify", "apple_music", etc.) - auto-creates on connector if specified |
| `destination.update_playlist` | **Updates playlists with smart ID resolution and flexible options** | `playlist_id`: Playlist ID (required) - canonical OR connector ID<br>`connector`: Optional connector name - determines ID interpretation and auto-syncs<br>`append`: Boolean - true=append tracks, false=overwrite with preservation (default: false)<br>`name`: Optional - update playlist name<br>`description`: Optional - update playlist description |

## Workflow Patterns

### Multi-Source Aggregation

This pattern combines tracks from multiple sources:

```json
{
  "tasks": [
    { "id": "source1", "type": "source.playlist", "config": {"playlist_id": "id1", "connector": "spotify"} },
    { "id": "source2", "type": "source.playlist", "config": {"playlist_id": "id2", "connector": "spotify"} },
    { "id": "combine", "type": "combiner.merge_playlists", "config": {"sources": ["source1", "source2"]}, "upstream": ["source1", "source2"] }
  ]
}
```

### Filter Chain

This pattern applies multiple sequential filters:

```json
{
  "tasks": [
    { "id": "source", "type": "source.playlist", "config": {"playlist_id": "id", "connector": "spotify"} },
    { "id": "filter1", "type": "filter.by_release_date", "config": {"max_age_days": 90}, "upstream": ["source"] },
    { "id": "filter2", "type": "filter.not_in_playlist", "config": {"reference": "exclude_source"}, "upstream": ["filter1", "exclude_source"] }
  ]
}
```

### Enrichment and Transformation

This pattern enhances tracks with external data before transformation:

```json
{
  "tasks": [
    { "id": "source", "type": "source.playlist", "config": {"playlist_id": "id", "connector": "spotify"} },
    { "id": "enrich", "type": "enricher.resolve_lastfm", "upstream": ["source"] },
    { "id": "transform", "type": "sorter.by_lastfm_user_playcount", "config": {"reverse": true}, "upstream": ["enrich"] }
  ]
}
```

### Advanced Playlist Updates

This pattern uses sophisticated differential operations to update existing playlists while preserving track metadata:

```json
{
  "tasks": [
    { "id": "source", "type": "source.playlist", "config": {"playlist_id": "source_id", "connector": "spotify"} },
    { "id": "enrich", "type": "enricher.spotify", "upstream": ["source"] },
    { "id": "filter", "type": "filter.by_metric", "config": {"metric_name": "popularity", "min_value": 60}, "upstream": ["enrich"] },
    { "id": "update", "type": "destination.update_playlist", 
      "config": {
        "playlist_id": "target_playlist_id",
        "operation_type": "update_spotify",
        "conflict_resolution": "local_wins",
        "preserve_order": true,
        "dry_run": false
      }, 
      "upstream": ["filter"] 
    }
  ]
}
```

## Best Practices

### General Workflow Design
1. **Explicit Dependencies** - Always specify upstream tasks even when seemingly obvious
2. **Task Naming** - Use descriptive IDs that reflect purpose, not implementation
3. **Configuration Validation** - Include sensible defaults where possible
4. **Workflow Decomposition** - Break complex workflows into logical groupings of tasks
5. **Error Handling** - Design for graceful degradation when nodes fail
6. **Idempotent Design** - Workflows should produce the same result when executed multiple times

### Advanced Playlist Updates
7. **Preview First** - Use `dry_run: true` to preview changes before execution
8. **Conservative Conflict Resolution** - Start with `local_wins` for predictable behavior
9. **Comprehensive Matching** - Use `track_matching_strategy: "comprehensive"` for cross-platform reliability
10. **Preserve Order** - Set `preserve_order: true` to maintain existing playlist structure where possible
11. **Monitor Performance** - Check API call estimates for large playlists to stay within rate limits
12. **Test Extensively** - Validate workflows with representative data before production use

## Extending the System

The node-based architecture allows for system extension through the transform registry:

1. Add transform implementations to the `TRANSFORM_REGISTRY` in node_factories.py
2. Register the node with appropriate metadata in workflow_nodes.py
3. Document the node's purpose and configuration
4. Create workflows that leverage the new node

This extensibility model enables continuous evolution without increasing architectural complexity.

## Example: Play History Intelligence Workflow

This example demonstrates the new play history enrichment and filtering capabilities:

```json
{
  "id": "rediscover_forgotten_favorites",
  "name": "Rediscover Forgotten Favorites",
  "description": "Find tracks you loved but haven't played recently",
  "version": "1.0",
  "tasks": [
    {
      "id": "source",
      "type": "source.playlist",
      "config": {
        "playlist_id": "YOUR_LIKED_SONGS_ID",
        "connector": "spotify"
      }
    },
    {
      "id": "enrich_history",
      "type": "enricher.play_history",
      "config": {
        "metrics": ["total_plays", "last_played_dates"],
        "period_days": 365
      },
      "upstream": ["source"]
    },
    {
      "id": "filter_old_favorites",
      "type": "filter.by_play_history",
      "config": {
        "min_plays": 10,
        "days_back": 180,
        "include_missing": false
      },
      "upstream": ["enrich_history"]
    },
    {
      "id": "sort_by_total_plays",
      "type": "sorter.by_metric",
      "config": {
        "metric_name": "total_plays",
        "reverse": true
      },
      "upstream": ["filter_old_favorites"]
    },
    {
      "id": "limit_selection",
      "type": "selector.limit_tracks",
      "config": {
        "count": 25,
        "method": "first"
      },
      "upstream": ["sort_by_total_plays"]
    },
    {
      "id": "create_rediscovery_playlist",
      "type": "destination.create_playlist",
      "config": {
        "name": "Rediscovered Favorites",
        "description": "Tracks you loved but haven't played in 6+ months",
        "connector": "spotify"
      },
      "upstream": ["limit_selection"]
    }
  ]
}
```

This workflow demonstrates:
- **Play History Enrichment**: Adds comprehensive listening data to track metadata
- **Flexible Time Filtering**: Finds tracks not played in the last 180 days but with high historical play counts
- **Intelligence-Driven Discovery**: Uses your own listening patterns to surface forgotten favorites
- **Automated Curation**: Creates a new playlist ready for immediate listening

## Example: Universal Playlist Source

This example demonstrates the new `source.playlist` node with smart ID resolution:

```json
{
  "id": "connector_agnostic_workflow",
  "name": "Connector-Agnostic Playlist Processing",
  "description": "Process playlists from any source with automatic upsert logic",
  "version": "2.0",
  "tasks": [
    {
      "id": "spotify_source",
      "type": "source.playlist",
      "config": {
        "playlist_id": "37i9dQZEVXcDXjmJJAvgkA",
        "connector": "spotify"
      }
    },
    {
      "id": "canonical_source",
      "type": "source.playlist", 
      "config": {
        "playlist_id": "123"
      }
    },
    {
      "id": "enrich",
      "type": "enricher.spotify",
      "config": {},
      "upstream": ["spotify_source"]
    },
    {
      "id": "destination",
      "type": "destination.create_playlist",
      "config": {
        "name": "Processed Playlist",
        "description": "Combined and enriched tracks",
        "connector": "spotify"
      },
      "upstream": ["enrich"]
    }
  ]
}
```

Key Features Demonstrated:
- **Connector-based sourcing**: `spotify_source` fetches from Spotify and automatically creates/updates canonical playlist
- **Canonical sourcing**: `canonical_source` reads directly from internal database using canonical ID
- **Automatic upsert**: Existing canonical playlists are updated, new ones are created as needed
- **Clean Architecture**: All operations use proper use cases with UnitOfWork pattern

## Example: Discovery Mix Workflow

```json
{
  "id": "discovery_mix",
  "name": "New Release Discovery Mix", 
  "description": "Create a playlist of recent tracks sorted by play count",
  "version": "2.0",
  "tasks": [
    {
      "id": "source",
      "type": "source.playlist",
      "config": {
        "playlist_id": "37i9dQZEVXcDXjmJJAvgkA",
        "connector": "spotify"
      }
    },
    {
      "id": "filter_date",
      "type": "filter.by_release_date",
      "config": {
        "max_age_days": 90
      },
      "upstream": ["source"]
    },
    {
      "id": "resolve",
      "type": "enricher.resolve_lastfm",
      "config": {},
      "upstream": ["filter_date"]
    },
    {
      "id": "sort",
      "type": "sorter.by_metric",
      "config": {
        "metric_name": "lastfm_user_playcount",
        "reverse": true
      },
      "upstream": ["resolve"]
    },
    {
      "id": "limit",
      "type": "selector.limit_tracks",
      "config": {
        "count": 50,
        "method": "first"
      },
      "upstream": ["sort"]
    },
    {
      "id": "destination",
      "type": "destination.create_playlist",
      "config": {
        "name": "Discovery Mix (90 days)",
        "description": "Recent releases sorted by play count",
        "connector": "spotify"
      },
      "upstream": ["limit"]
    }
  ]
}

## Example: Multi-Metric Workflow

This example demonstrates using the new generic metric filter and sorter nodes:

```json
{
  "id": "popular_gems",
  "name": "Popular Gems with Few Listens",
  "description": "Popular tracks that you haven't played much",
  "version": "1.0",
  "tasks": [
    {
      "id": "source",
      "type": "source.playlist",
      "config": {
        "playlist_id": "YOUR_PLAYLIST_ID",
        "connector": "spotify"
      }
    },
    {
      "id": "enrich_spotify",
      "type": "enricher.spotify",
      "config": {},
      "upstream": ["source"]
    },
    {
      "id": "filter_popular",
      "type": "filter.by_metric",
      "config": {
        "metric_name": "spotify_popularity",
        "min_value": 70,
        "include_missing": false
      },
      "upstream": ["enrich_spotify"]
    },
    {
      "id": "enrich_lastfm",
      "type": "enricher.lastfm",
      "config": {},
      "upstream": ["filter_popular"]
    },
    {
      "id": "filter_few_plays",
      "type": "filter.by_metric",
      "config": {
        "metric_name": "lastfm_user_playcount",
        "max_value": 5,
        "include_missing": true
      },
      "upstream": ["enrich_lastfm"]
    },
    {
      "id": "sort_by_global",
      "type": "sorter.by_metric",
      "config": {
        "metric_name": "lastfm_global_playcount",
        "reverse": true
      },
      "upstream": ["filter_few_plays"]
    },
    {
      "id": "limit",
      "type": "selector.limit_tracks",
      "config": {
        "count": 30,
        "method": "first"
      },
      "upstream": ["sort_by_global"]
    },
    {
      "id": "destination",
      "type": "destination.create_playlist",
      "config": {
        "name": "Popular Gems to Discover",
        "description": "Popular tracks you haven't listened to much yet",
        "connector": "spotify"
      },
      "upstream": ["limit"]
    }
  ]
}
```

## Example: Advanced Playlist Update Workflow

This example demonstrates sophisticated playlist updates with differential operations:

```json
{
  "id": "smart_playlist_sync",
  "name": "Smart Playlist Synchronization",
  "description": "Update existing playlist with filtered and sorted tracks while preserving Spotify metadata",
  "version": "2.0",
  "tasks": [
    {
      "id": "source_discover",
      "type": "source.playlist",
      "config": {
        "playlist_id": "37i9dQZEVXcJZTJkGMJOhH",
        "connector": "spotify"
      }
    },
    {
      "id": "enrich_spotify",
      "type": "enricher.spotify",
      "config": {},
      "upstream": ["source_discover"]
    },
    {
      "id": "filter_popular",
      "type": "filter.by_metric",
      "config": {
        "metric_name": "popularity",
        "min_value": 70,
        "include_missing": false
      },
      "upstream": ["enrich_spotify"]
    },
    {
      "id": "enrich_lastfm",
      "type": "enricher.lastfm",
      "config": {},
      "upstream": ["filter_popular"]
    },
    {
      "id": "filter_unplayed",
      "type": "filter.by_metric",
      "config": {
        "metric_name": "lastfm_user_playcount",
        "max_value": 3,
        "include_missing": true
      },
      "upstream": ["enrich_lastfm"]
    },
    {
      "id": "sort_by_global",
      "type": "sorter.by_metric",
      "config": {
        "metric_name": "lastfm_global_playcount",
        "reverse": true
      },
      "upstream": ["filter_unplayed"]
    },
    {
      "id": "limit_selection",
      "type": "selector.limit_tracks",
      "config": {
        "count": 25,
        "method": "first"
      },
      "upstream": ["sort_by_global"]
    },
    {
      "id": "update_target",
      "type": "destination.update_playlist",
      "config": {
        "playlist_id": "YOUR_TARGET_SPOTIFY_PLAYLIST_ID",
        "connector": "spotify",
        "append": false
      },
      "upstream": ["limit_selection"]
    }
  ]
}
```

This workflow demonstrates:
- **Multi-stage filtering**: Combines Spotify popularity with Last.fm play history
- **Sophisticated sorting**: Uses global play counts for discovery potential  
- **Smart ID resolution**: Automatically resolves Spotify playlist IDs to canonical playlists
- **Overwrite with preservation**: Uses differential algorithm to minimize changes and preserve metadata
- **Clean configuration**: Simple, intuitive destination node setup

## Example: Dry-Run Preview Workflow

This example shows how to preview playlist changes before applying them:

```json
{
  "id": "preview_updates",
  "name": "Preview Playlist Changes",
  "description": "Preview what changes would be made to a playlist without applying them",
  "version": "1.0",
  "tasks": [
    {
      "id": "source",
      "type": "source.playlist",
      "config": {
        "playlist_id": "source_playlist_id",
        "connector": "spotify"
      }
    },
    {
      "id": "transform",
      "type": "sorter.by_metric",
      "config": {
        "metric_name": "popularity",
        "reverse": true
      },
      "upstream": ["source"]
    },
    {
      "id": "preview",
      "type": "destination.update_playlist",
      "config": {
        "playlist_id": "target_spotify_playlist_id",
        "connector": "spotify",
        "append": false
      },
      "upstream": ["transform"]
    }
  ]
}
```

This workflow demonstrates simplified playlist updates:
- **Smart ID Resolution**: Automatically handles Spotify playlist ID → canonical playlist mapping
- **Preservation Algorithm**: Overwrite mode uses sophisticated diff engine to minimize changes
- **Clean Configuration**: Simple, intuitive parameters without complex operation types

## Advanced Features

### Universal Playlist Sourcing

The new `source.playlist` node provides connector-agnostic playlist access with intelligent ID resolution:

#### Smart ID Resolution
- **Canonical Mode**: When no `connector` specified, `playlist_id` treated as internal canonical ID
- **Connector Mode**: When `connector` specified, `playlist_id` treated as external service ID
- **Automatic Detection**: System automatically determines if canonical playlist already exists for external ID
- **Upsert Logic**: Existing canonical playlists are updated, missing ones are created automatically

#### Connector Support
- **Spotify**: Full support with automatic track fetching and metadata extraction
- **Extensible Design**: Easy to add support for Apple Music, YouTube Music, etc.
- **Consistent Interface**: Same configuration pattern across all connector types

#### Data Flow Architecture
1. **Fetch External**: Retrieves playlist and tracks from external service using bulk operations
2. **Check Mapping**: Uses `ReadCanonicalPlaylistUseCase` to find existing canonical playlist
3. **Smart Operation**: Either updates existing playlist or creates new one based on mapping
4. **Metrics Extraction**: Automatically extracts and saves track metrics (popularity, etc.)
5. **Clean Return**: Provides standardized result format for downstream nodes

#### Performance Features
- **Bulk API Calls**: Fetches all tracks in optimal batches to minimize API requests
- **Database Upsert**: Tracks are automatically saved/updated in canonical database
- **Clean Architecture**: Proper separation between workflow orchestration and business logic
- **Transaction Safety**: All operations use UnitOfWork pattern for data consistency


### Simplified Playlist Operations

The destination nodes provide intuitive playlist management with sophisticated algorithms underneath:

#### Smart Playlist Creation (`destination.create_playlist`)
- **Always Creates Canonical**: Internal database playlist created for all operations
- **Optional Connector Sync**: Specify `connector` to auto-create on external services
- **Automatic Linking**: Canonical and connector playlists are automatically linked
- **Clean Configuration**: Just name, description, and optional connector

#### Intelligent Playlist Updates (`destination.update_playlist`)
- **Smart ID Resolution**: Automatically determines whether playlist ID is canonical or connector-based
- **Append vs Overwrite**: Choose between adding tracks (`append: true`) or replacement with preservation (`append: false`)
- **Metadata Updates**: Optional name and description updates in same operation
- **Auto-Creation**: Missing canonical playlists are automatically created when updating connector playlists

#### Under the Hood: Advanced Algorithms
- **Differential Engine**: Sophisticated diff algorithm minimizes API calls and preserves metadata
- **Track Matching**: Multi-strategy matching using Spotify IDs, ISRC codes, and metadata similarity
- **Operation Sequencing**: Proper remove→add→move order preserves track addition timestamps
- **Optimistic Updates**: Database immediately reflects successful API operations

### Production Considerations

#### Performance Features
- **Efficient Operations**: Automatic batching and minimal API calls through differential algorithms
- **Smart Caching**: Optimistic database updates reduce redundant API calls
- **Rate Limiting**: Built-in exponential backoff and retry logic
- **Progress Tracking**: Real-time feedback for long-running operations

#### Reliability Features
- **Clean Architecture**: Business logic separated from workflow orchestration
- **Transaction Safety**: UnitOfWork pattern ensures atomic operations
- **Input Validation**: Comprehensive validation with clear error messages
- **Error Recovery**: Graceful handling of API failures and partial operations

#### User Experience
- **Intuitive Configuration**: Simple parameters hide complex implementation details
- **Smart Defaults**: Sensible defaults for all optional parameters
- **Consistent Behavior**: Predictable create/update operations across all connectors
- **Comprehensive Logging**: Structured logs for debugging and auditing

## Implementation Architecture

The workflow system architecture consists of three key components:

1. **Node Registry** - Central registration point for all node types
2. **Transform Registry** - Maps node categories and types to their implementations
3. **Node Factories** - Creates node functions with standardized interfaces

This layered approach separates node definition from implementation details, allowing for clean extension and maintenance of the workflow system.