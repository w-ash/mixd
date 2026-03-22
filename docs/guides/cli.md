# Mixd CLI Reference

Command-line interface for managing your music library, running workflows, and syncing data across services.

**Global options:** `--verbose` / `-v` for debug output, `--help` on any command.

## Command Overview

```
mixd
├── version                          Show version information
├── connectors                       Check music service connector status
├── stats                            Library statistics and dashboard
├── workflow
│   ├── list                         List available workflow definitions
│   └── run [WORKFLOW_ID]            Execute a specific workflow
├── playlist
│   ├── list                         List all local playlists
│   ├── backup CONNECTOR PLAYLIST_ID Backup a playlist from a music service
│   ├── create --name NAME           Create a new empty playlist
│   ├── update PLAYLIST_ID           Update playlist name/description
│   └── delete PLAYLIST_ID           Delete a playlist
├── history
│   ├── import-lastfm                Import play history from Last.fm API
│   ├── import-spotify [FILE_PATH]   Import plays from Spotify JSON export
│   └── checkpoints                  Show sync checkpoint status
├── likes
│   ├── import-spotify               Import liked tracks from Spotify
│   └── export-lastfm                Export liked tracks to Last.fm as loves
└── tracks
    ├── list                         List/search tracks in your library
    ├── show TRACK_ID                Show detailed track information
    ├── playlists TRACK_ID           Show which playlists contain a track
    └── merge                        Merge duplicate tracks
```

## Command Groups

### workflow

Execute and manage playlist workflows.

| Command | Description |
|---------|-------------|
| `mixd workflow list` | List available workflow definitions |
| `mixd workflow run [WORKFLOW_ID]` | Execute a specific workflow |

**`workflow run` options:**
- `--show-results` / `--no-results` — show detailed result metrics (default: show)
- `--format` / `-f` `table|json` — output format (default: table)
- `--quiet` / `-q` — minimal output

```bash
mixd workflow list
mixd workflow run discovery_mix
mixd workflow run discovery_mix --format json
mixd workflow run                          # interactive selection
```

### playlist

Manage stored playlists and data operations.

| Command | Description |
|---------|-------------|
| `mixd playlist list` | List all playlists in local database |
| `mixd playlist backup CONNECTOR PLAYLIST_ID` | Backup a playlist from a music service |
| `mixd playlist create --name NAME` | Create a new empty playlist |
| `mixd playlist update PLAYLIST_ID` | Update playlist name and/or description |
| `mixd playlist delete PLAYLIST_ID` | Delete a playlist from local database |

**`playlist create` options:** `--name` / `-n` (required), `--description` / `-d`
**`playlist update` options:** `--name` / `-n`, `--description` / `-d`
**`playlist delete` options:** `--force` / `-f` — skip confirmation prompt

```bash
mixd playlist list
mixd playlist backup spotify 37i9dQZF1DX0XUsuxWHRQd
mixd playlist create --name "My Playlist" --description "Best tracks"
mixd playlist update 5 --name "New Name"
mixd playlist delete 3 --force
```

### history

Import and manage your music play history.

| Command | Description |
|---------|-------------|
| `mixd history import-lastfm` | Import play history from Last.fm API with smart daily chunking |
| `mixd history import-spotify [FILE_PATH]` | Import play history from Spotify JSON export file(s) |
| `mixd history checkpoints` | Show sync checkpoint status for all services |

**`history import-lastfm` options:**
- `--from-date` — start date (YYYY-MM-DD), establishes import window on first run
- `--to-date` — end date (YYYY-MM-DD), defaults to now

**`history import-spotify` options:**
- `FILE_PATH` (optional) — path to Spotify JSON export. Without it, processes all `Streaming_History_Audio_*.json` in `data/imports/`
- `--batch-size` / `-b` — tracks per processing batch

```bash
mixd history import-lastfm                                          # incremental from last checkpoint
mixd history import-lastfm --from-date 2025-01-01 --to-date 2025-06-30
mixd history import-spotify                                         # all files in data/imports/
mixd history import-spotify ~/Downloads/spotify_export.json
mixd history checkpoints
```

### likes

Import and export liked tracks across music services.

| Command | Description |
|---------|-------------|
| `mixd likes import-spotify` | Import liked tracks from Spotify into local library |
| `mixd likes export-lastfm` | Export liked tracks to Last.fm as loved tracks |

**`likes import-spotify` options:**
- `--limit` / `-l` — tracks per API request batch
- `--max-imports` / `-m` — maximum total tracks to import

**`likes export-lastfm` options:**
- `--batch-size` / `-b` — tracks per API request batch
- `--max-exports` / `-m` — maximum total tracks to export
- `--date` — override checkpoint date (ISO format: `2025-08-01`)

```bash
mixd likes import-spotify
mixd likes import-spotify --max-imports 500
mixd likes export-lastfm
mixd likes export-lastfm --batch-size 25 --max-exports 100
mixd likes export-lastfm --date 2025-08-01
```

### tracks

Track management operations including merging duplicates.

| Command | Description |
|---------|-------------|
| `mixd tracks list` | List/search tracks with filtering |
| `mixd tracks show TRACK_ID` | Show detailed track info (likes, plays, playlists) |
| `mixd tracks playlists TRACK_ID` | Show which playlists contain a track |
| `mixd tracks merge` | Merge two duplicate tracks |

**`tracks list` options:**
- `--query` / `-q` — search by title/artist
- `--liked` / `--not-liked` — filter by liked status
- `--connector` / `-c` — filter by connector (e.g., `spotify`)
- `--sort` / `-s` — `title_asc`, `title_desc`, `recent` (default: `title_asc`)
- `--limit` / `-l` — number of tracks (default: 50)
- `--offset` / `-o` — skip tracks (default: 0)
- `--format` / `-f` — `table` or `json` (default: `table`)

**`tracks merge` options:** `--winner-id` (required), `--loser-id` (required), `--force`

```bash
mixd tracks list --query "Radiohead" --liked --limit 20
mixd tracks show 42
mixd tracks playlists 42
mixd tracks merge --winner-id 10 --loser-id 25 --force
```

### System Commands

| Command | Description |
|---------|-------------|
| `mixd version` | Show version information |
| `mixd connectors` | Check music service connector status |
| `mixd stats` | Library statistics and dashboard |

## Output Formats

### Table Format (Default)

Rich-formatted tables with color coding:

```
┌─────────────────────────────────────────────────────────────┐
│                       Operation Results                      │
├─────────────────────────────────────────────────────────────┤
│ Total Tracks:            1,234                               │
│ Successfully Processed:  1,200                               │
│ Failed:                  34                                   │
│ Duration:                2m 34s                               │
└─────────────────────────────────────────────────────────────┘
```

### JSON Format

Use `--format json` for structured output:

```json
{
  "operation": "workflow_run",
  "status": "completed",
  "results": {
    "total_tracks": 1234,
    "successfully_processed": 1200,
    "failed": 34,
    "duration_seconds": 154
  }
}
```

## Error Handling

Errors are displayed with context and suggested actions:

```
❌ Spotify API Error

Problem: Playlist not found (404)
Playlist ID: 37i9dQZF1DX0XUsuxWHRQd
Suggestion: Check that the playlist exists and is accessible to your account
```

**Common error types:**
- **Configuration** — missing API keys, invalid credentials, network issues
- **API** — rate limit exceeded, invalid IDs, service unavailable
- **Data** — invalid metadata, duplicate entries, missing required fields

## Troubleshooting

### Database Issues

```bash
uv run alembic current              # check migration status
uv run alembic upgrade head         # apply pending migrations

# full reset (destroys all data)
rm data/db/mixd.db
uv run alembic upgrade head
```

### Authentication

```bash
mixd connectors                       # verify service connections
```

Re-run OAuth flow by deleting the cached token and running a command that requires authentication (e.g., `mixd likes import-spotify`).

### Verbose Mode

```bash
mixd -v workflow run discovery_mix    # debug output for any command
```

## Related Documentation

- **[Workflow Guide](workflows.md)** — workflow authoring and node catalog
- **[Likes Sync Guide](likes-sync.md)** — cross-service likes synchronization
- **[Architecture](../architecture/README.md)** — system design and patterns
- **[Database](../architecture/database.md)** — schema reference
- **[Development](../development.md)** — developer setup and recipes
- **[REST API Reference](../web-ui/03-api-contracts.md)** — FastAPI endpoints and contracts
