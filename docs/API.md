# Narada API Reference

## Overview

Narada provides a comprehensive command-line interface for music data management and playlist operations. All commands are designed for single-user, local operation with beautiful terminal output via Rich formatting.

## CLI Commands

### System Commands

#### `narada setup`
Configure music service connections and initial setup.

```bash
narada setup
```

**Purpose**: Initial configuration of Spotify, Last.fm, and MusicBrainz connections
**Interactive**: Yes - guides through OAuth and API key setup
**Output**: Configuration status and connection verification

#### `narada status`
Show system status and configuration.

```bash
narada status
```

**Purpose**: Display current configuration, database status, and service connections
**Output**: Formatted status panel with service connection status

### Data Management Commands

Narada organizes all data operations under the unified `narada data` command, providing both interactive discovery and direct access for power users.

#### `narada data menu`
Interactive menu showing all available data operations.

```bash
narada data menu
```

**Purpose**: Display categorized menu of all data operations for easy discovery
**Interactive**: Yes - shows numbered menu with operation selection
**Output**: Interactive menu with play history and liked tracks operations

#### `narada data spotify-plays-file`
Import play history from Spotify GDPR export.

```bash
narada data spotify-plays-file FILE_PATH [OPTIONS]
```

**Arguments**:
- `FILE_PATH`: Path to Spotify personal data JSON export file

**Options**:
- `--batch-size NUMBER`: Number of plays to process per batch (default: 1000)

**Example**:
```bash
narada data spotify-plays-file ~/Downloads/spotify_export.json --batch-size 500
```

**Purpose**: Import comprehensive play history from Spotify personal data exports
**Features**:
- Enhanced track resolution with 100% processing rate
- Handles obsolete track IDs and relinking
- Automatic fallback to metadata-based matching
- Complete provenance tracking

**Output**: 
- Progress bar during import
- Enhanced resolution statistics
- Import summary with resolution breakdown

#### `narada data lastfm-plays`
Import play history from Last.fm API.

```bash
narada data lastfm-plays [OPTIONS]
```

**Options**:
- `--recent NUMBER`: Number of recent plays to import (overrides default incremental)
- `--limit NUMBER`: Maximum number of items to process
- `--full`: Full sync instead of incremental (requires confirmation)
- `--confirm`: Skip confirmation prompts
- `--resolve-tracks/--no-resolve-tracks`: Resolve tracks for playlists (default: resolve)
- `--user USERNAME`: Last.fm username (defaults to LASTFM_USERNAME env)

**Examples**:
```bash
narada data lastfm-plays                          # Incremental sync
narada data lastfm-plays --recent 1000            # Recent 1000 plays
narada data lastfm-plays --full --confirm         # Full history sync
```

**Purpose**: Import play history from Last.fm API with flexible sync modes
**Output**: Progress bar and import statistics

#### `narada data spotify-likes`
Import liked tracks from Spotify API.

```bash
narada data spotify-likes [OPTIONS]
```

**Options**:
- `--limit NUMBER`: Maximum number of tracks to import (default: no limit)
- `--batch-size NUMBER`: API batch size for processing (default: 100)
- `--user USERNAME`: User ID for checkpoint tracking (default: "default")

**Example**:
```bash
narada data spotify-likes --limit 1000 --batch-size 50
```

**Purpose**: Sync liked tracks from Spotify to Narada database
**Output**: Progress bar and import statistics

#### `narada data lastfm-loves`
Export liked tracks to Last.fm as loved tracks.

```bash
narada data lastfm-loves [OPTIONS]
```

**Options**:
- `--limit NUMBER`: Maximum number of tracks to export (default: no limit)
- `--batch-size NUMBER`: Number of tracks to process per batch (default: 100)
- `--user USERNAME`: Last.fm username for tracking (default: "default")

**Example**:
```bash
narada data lastfm-loves --limit 500 --batch-size 25
```

**Purpose**: Sync liked tracks from Narada to Last.fm love status
**Output**: Progress bar and export statistics

### Playlist Management Commands

All playlist operations are unified under `narada playlist` for workflow execution and management.

#### `narada playlist list`
List available workflow definitions.

```bash
narada playlist list
```

**Purpose**: Display all available workflow definitions with descriptions
**Output**: Formatted table with workflow ID, name, description, and task count

#### `narada playlist run`
Run a specific workflow by ID.

```bash
narada playlist run [WORKFLOW_ID] [OPTIONS]
```

**Arguments**:
- `WORKFLOW_ID`: ID of workflow to execute (optional - will prompt if not provided)

**Options**:
- `--show-results/--no-results`: Show detailed result metrics (default: true)
- `--format`, `-f`: Output format (table, json) (default: table)

**Examples**:
```bash
narada playlist run discovery_mix
narada playlist run sort_by_lastfm_user_playcount --format json
narada playlist run  # Interactive selection
```

**Purpose**: Execute workflow definitions with progress tracking
**Output**: 
- Workflow execution progress
- Task completion status
- Result metrics and track counts

### Help and Completion

#### `narada --help`
Show comprehensive help with organized command panels.

```bash
narada --help
```

**Purpose**: Display all available commands with descriptions organized by category
**Output**: Rich-formatted help panels

#### `narada completion --install`
Install shell completion for bash/zsh/fish.

```bash
narada completion --install
```

**Purpose**: Enable tab completion for commands and workflow names
**Supports**: bash, zsh, fish shells

## Workflow System

### Workflow Definition Format

Workflows are defined in JSON format with the following structure:

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

### Available Node Types

#### Source Nodes
- `source.spotify_playlist` - Fetch playlist from Spotify
  - Config: `playlist_id` (required)

#### Enricher Nodes
- `enricher.resolve_lastfm` - Resolve tracks to Last.fm and fetch play counts
  - Config: `username` (optional), `batch_size` (optional), `concurrency` (optional)

#### Filter Nodes
- `filter.deduplicate` - Remove duplicate tracks
- `filter.by_release_date` - Filter by release date
  - Config: `max_age_days`, `min_age_days`
- `filter.by_tracks` - Exclude tracks present in another source
  - Config: `exclusion_source` (task ID)
- `filter.by_artists` - Exclude tracks by artist presence
  - Config: `exclusion_source` (task ID), `exclude_all_artists` (boolean)
- `filter.by_metric` - Filter by metric value range
  - Config: `metric_name`, `min_value`, `max_value`, `include_missing`

#### Sorter Nodes
- `sorter.by_metric` - Sort by any metric
  - Config: `metric_name`, `reverse` (boolean)

#### Selector Nodes
- `selector.limit_tracks` - Limit number of tracks
  - Config: `count`, `method` (first, last, random)

#### Combiner Nodes
- `combiner.merge_playlists` - Merge multiple playlists
  - Config: `sources` (array of task IDs)
- `combiner.concatenate_playlists` - Join playlists in order
  - Config: `order` (array of task IDs)
- `combiner.interleave_playlists` - Interleave tracks from multiple playlists
  - Config: `sources` (array of task IDs)

#### Destination Nodes
- `destination.create_internal_playlist` - Create internal playlist
  - Config: `name`, `description` (optional)
- `destination.create_spotify_playlist` - Create new Spotify playlist
  - Config: `name`, `description` (optional)
- `destination.update_spotify_playlist` - Update existing Spotify playlist
  - Config: `playlist_id`, `append` (boolean)
- `destination.update_playlist` - Advanced playlist updates
  - Config: `playlist_id`, `operation_type`, `conflict_resolution`, `dry_run`, `preserve_order`, `track_matching_strategy`, `enable_spotify_sync`

### Example Workflow

```json
{
  "id": "discovery_mix",
  "name": "New Release Discovery Mix",
  "description": "Create a playlist of recent tracks sorted by play count",
  "version": "1.0",
  "tasks": [
    {
      "id": "source",
      "type": "source.spotify_playlist",
      "config": {
        "playlist_id": "37i9dQZEVXcDXjmJJAvgkA"
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
      "type": "destination.create_spotify_playlist",
      "config": {
        "name": "Discovery Mix (90 days)",
        "description": "Recent releases sorted by play count"
      },
      "upstream": ["limit"]
    }
  ]
}
```

## Configuration

### Environment Variables

Narada uses environment variables for configuration, typically stored in `.env` file:

```bash
# Spotify Configuration
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://localhost:8888/callback

# Last.fm Configuration
LASTFM_API_KEY=your_api_key
LASTFM_API_SECRET=your_api_secret
LASTFM_USERNAME=your_username

# MusicBrainz Configuration
MUSICBRAINZ_USER_AGENT=YourApp/1.0 (your-email@example.com)

# Database Configuration
DATABASE_URL=sqlite:///./data/narada.db

# Logging Configuration
LOG_LEVEL=INFO
LOG_FORMAT=json
```

### Configuration Files

#### `.env`
Environment variables for sensitive configuration

#### `alembic.ini`
Database migration configuration

#### `pyproject.toml`
Project dependencies and build configuration

## Internal API Patterns

### Repository Pattern

All data access follows the repository pattern with async interfaces:

```python
# Repository interface
class TrackRepository(Protocol):
    async def get_by_id(self, track_id: int) -> Track | None:
        ...
    
    async def get_by_spotify_ids(self, spotify_ids: list[str]) -> list[Track]:
        ...
    
    async def save_batch(self, tracks: list[Track]) -> list[Track]:
        ...
    
    async def update_batch(self, tracks: list[Track]) -> list[Track]:
        ...

# Usage in use cases
class ImportPlayHistoryUseCase:
    def __init__(self, repository: TrackRepository):
        self.repository = repository
    
    async def execute(self, tracks: list[Track]) -> ImportResult:
        existing = await self.repository.get_by_spotify_ids([t.spotify_id for t in tracks])
        # ... business logic
```

### Use Case Pattern

Business logic is organized into use cases with command objects:

```python
# Command object
@dataclass
class ImportPlayHistoryCommand:
    tracks: list[Track]
    batch_size: int = 100
    update_existing: bool = True
    
    def validate(self) -> None:
        if not self.tracks:
            raise ValueError("No tracks to import")
        if self.batch_size <= 0:
            raise ValueError("Batch size must be positive")

# Use case
class ImportPlayHistoryUseCase:
    async def execute(self, command: ImportPlayHistoryCommand) -> ImportResult:
        command.validate()
        # ... implementation
```

### Progress Tracking

Long-running operations support progress tracking:

```python
from src.application.utilities.progress import ProgressProvider

async def long_running_operation(progress: ProgressProvider):
    total = 1000
    progress.start(total=total, description="Processing tracks")
    
    for i, item in enumerate(items):
        # ... process item
        progress.update(advance=1)
    
    progress.finish()
```

### Error Handling

External API calls use resilient operation patterns:

```python
from src.infrastructure.connectors.resilience import resilient_operation

@resilient_operation("spotify_api")
async def get_playlist_tracks(playlist_id: str) -> list[Track]:
    # API call with automatic retry and error handling
    response = await spotify_client.get_playlist_tracks(playlist_id)
    return [Track.from_spotify(track) for track in response['items']]
```

## External Service Integration

### Spotify API

#### Authentication
- OAuth 2.0 with automatic token refresh
- Scopes: `playlist-read-private`, `playlist-modify-private`, `user-library-read`

#### Rate Limiting
- Built-in exponential backoff
- Respects Spotify's rate limits (100 requests per minute)
- Batch operations for efficiency

#### Common Operations
```python
# Get playlist tracks
tracks = await spotify_connector.get_playlist_tracks(playlist_id)

# Create playlist
playlist = await spotify_connector.create_playlist(name, description)

# Update playlist
await spotify_connector.update_playlist(playlist_id, track_ids)
```

### Last.fm API

#### Authentication
- API key authentication
- Session key for scrobbling operations

#### Rate Limiting
- 5 requests per second limit
- Automatic retry with backoff

#### Common Operations
```python
# Get user play count
play_count = await lastfm_connector.get_user_play_count(track, username)

# Love track
await lastfm_connector.love_track(track, username)

# Get track info
track_info = await lastfm_connector.get_track_info(artist, title)
```

### MusicBrainz API

#### Configuration
- User agent identification required
- Rate limiting: 1 request per second

#### Common Operations
```python
# Get recording by ISRC
recording = await musicbrainz_connector.get_recording_by_isrc(isrc)

# Batch ISRC lookup
recordings = await musicbrainz_connector.get_recordings_by_isrcs(isrcs)
```

## Output Formats

### Table Format (Default)
Rich-formatted tables with color coding and alignment:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                Operation Results                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Total Tracks:     1,234                                                         │
│ Successfully Processed: 1,200                                                   │
│ Failed:           34                                                            │
│ Duration:         2m 34s                                                        │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### JSON Format
Structured output for programmatic consumption:

```json
{
  "operation": "import_play_history",
  "status": "completed",
  "results": {
    "total_tracks": 1234,
    "successfully_processed": 1200,
    "failed": 34,
    "duration_seconds": 154
  },
  "timestamp": "2023-07-16T10:30:00Z"
}
```

## Error Handling

### Common Error Types

#### Configuration Errors
- Missing API keys or credentials
- Invalid configuration values
- Network connectivity issues

#### API Errors
- Rate limit exceeded
- Invalid track/playlist IDs
- Service unavailable

#### Data Errors
- Invalid track metadata
- Duplicate entries
- Missing required fields

### Error Messages

Errors are displayed with context and suggested actions:

```
❌ Spotify API Error

Problem: Playlist not found (404)
Playlist ID: 1234567890abcdef
Suggestion: Check that the playlist exists and is accessible to your account

Details: The playlist may be private or deleted. Verify the playlist ID and permissions.
```

## Performance Considerations

### Batch Operations
- Default batch size: 100 items
- Configurable based on operation type
- Automatic batching for large datasets

### Caching
- Database-level caching for frequently accessed data
- API response caching with TTL
- Connection pooling for database operations

### Rate Limiting
- Service-specific rate limits enforced
- Exponential backoff for retry operations
- Progress tracking for long-running operations

## Troubleshooting

### Common Issues

#### Database Connection
```bash
# Check database status
poetry run alembic current

# Reset database
rm data/narada.db
poetry run alembic upgrade head
```

#### API Authentication
```bash
# Verify configuration
narada status

# Reconfigure services
narada setup
```

#### Workflow Execution
```bash
# List available workflows
narada wf list

# Run with verbose output
narada wf run workflow_id --show-results
```

### Debug Mode

Enable debug logging for troubleshooting:

```bash
export LOG_LEVEL=DEBUG
narada import-spotify-likes
```

### Support Information

For technical support, include:
- Command executed
- Error message
- System configuration (`narada status`)
- Log output (if available)
- Environment details (OS, Python version)

## Related Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System architecture and design decisions
- **[DEVELOPMENT.md](DEVELOPMENT.md)** - Developer onboarding and contribution guide
- **[DATABASE.md](DATABASE.md)** - Database schema and design reference
- **[workflow_guide.md](workflow_guide.md)** - Detailed workflow system documentation
- **[likes_sync_guide.md](likes_sync_guide.md)** - Comprehensive guide to likes synchronization