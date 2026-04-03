# Mixd CLI Reference

Command-line interface for managing your music library, running workflows, and syncing data across services.

**Global options:** `--verbose` / `-v` for debug output, `--help` on any command.

## Operating Modes

The CLI works in two modes, determined by environment variables:

### Local Mode (default)

Zero-config. Connects to a local Docker PostgreSQL instance. All data is stored under `user_id="default"`.

```bash
# Just works — no configuration needed
pnpm dev              # starts PostgreSQL + API + Vite
mixd stats            # connects to localhost:5432
```

### Remote Mode

Connects to the production Neon database. Set two environment variables (in `.env.local` or shell):

```bash
# .env.local
DATABASE_URL=postgresql://user:pass@ep-xxx.us-east-2.aws.neon.tech/mixd
MIXD_USER_ID=your-neon-auth-user-id
```

Your `MIXD_USER_ID` is the `sub` claim from your Neon Auth JWT — find it on the Account page in the web UI.

### Verifying Your Configuration

```bash
mixd whoami           # shows user_id, database host, mode, connector status
```

### Connector Authentication

In local mode (no web server), authenticate with connectors directly from the CLI:

```bash
mixd connectors auth spotify   # opens browser OAuth flow, stores token
mixd connectors auth lastfm    # uses LASTFM_USERNAME + LASTFM_PASSWORD from env
mixd connectors                # verify connection status
```

## Command Overview

```
mixd
├── whoami                           Show identity, database, mode, connectors
├── version                          Show version information
├── connectors
│   ├── (default)                    Check connector authentication status
│   └── auth
│       ├── spotify                  Authenticate with Spotify (browser OAuth)
│       └── lastfm                   Authenticate with Last.fm (password auth)
├── stats                            Library statistics and dashboard
├── workflow
│   ├── (default)                    Interactive workflow browser
│   ├── list                         List available workflow definitions
│   ├── run [WORKFLOW_ID]            Execute a specific workflow
│   ├── get WORKFLOW_ID              Show a workflow's full definition
│   ├── create [--file FILE]         Create workflow from JSON
│   ├── update WORKFLOW_ID           Update workflow definition from JSON
│   ├── delete WORKFLOW_ID           Delete a workflow
│   ├── export                       Export workflow definitions to JSON
│   ├── validate [--file FILE]       Validate a workflow definition
│   ├── nodes                        List available node types
│   ├── runs [WORKFLOW_ID]           List workflow execution runs
│   ├── versions WORKFLOW_ID         List version history of a workflow
│   └── revert WORKFLOW_ID VERSION   Revert to a previous version
├── playlist
│   ├── list                         List all local playlists
│   ├── backup CONNECTOR PLAYLIST_ID Backup a playlist from a music service
│   ├── create --name NAME           Create a new empty playlist
│   ├── update PLAYLIST_ID           Update playlist name/description
│   ├── delete PLAYLIST_ID           Delete a playlist
│   ├── links PLAYLIST_ID            List connector links for a playlist
│   ├── link PLAYLIST_ID             Link to an external connector playlist
│   ├── unlink LINK_ID               Remove a connector link
│   ├── sync LINK_ID                 Sync a linked playlist
│   └── sync-preview LINK_ID         Preview what a sync would do
├── history
│   ├── (default)                    Interactive history import menu
│   ├── import-lastfm                Import play history from Last.fm API
│   ├── import-spotify [FILE_PATH]   Import plays from Spotify JSON export
│   └── checkpoints                  Show sync checkpoint status
├── likes
│   ├── (default)                    Interactive likes menu
│   ├── import-spotify               Import liked tracks from Spotify
│   └── export-lastfm                Export liked tracks to Last.fm as loves
├── tracks
│   ├── list                         List/search tracks in your library
│   ├── show TRACK_ID                Show detailed track information
│   ├── playlists TRACK_ID           Show which playlists contain a track
│   ├── merge                        Merge duplicate tracks
│   ├── relink TRACK_ID MAPPING_ID   Move a mapping to a different track
│   ├── unlink TRACK_ID MAPPING_ID   Remove a connector mapping
│   └── set-primary TRACK_ID MAPPING_ID  Set mapping as primary source
└── reviews
    ├── list                         List pending match reviews
    └── resolve [REVIEW_ID]          Resolve a match review (accept/reject)
```

## Command Groups

### workflow

Execute and manage playlist workflows.

| Command | Description |
|---------|-------------|
| `mixd workflow` | Interactive workflow browser |
| `mixd workflow list` | List available workflow definitions |
| `mixd workflow run [WORKFLOW_ID]` | Execute a specific workflow |
| `mixd workflow get WORKFLOW_ID` | Show a workflow's full definition |
| `mixd workflow create [--file FILE]` | Create workflow from JSON (file or stdin) |
| `mixd workflow update WORKFLOW_ID [--file FILE]` | Update workflow definition |
| `mixd workflow delete WORKFLOW_ID` | Delete a workflow (templates protected) |
| `mixd workflow export --all \| --id ID` | Export definitions to JSON files |
| `mixd workflow validate [--file FILE]` | Validate a definition without saving |
| `mixd workflow nodes` | List available node types with config fields |
| `mixd workflow runs [WORKFLOW_ID]` | List execution runs (all or per-workflow) |
| `mixd workflow versions WORKFLOW_ID` | List version history |
| `mixd workflow revert WORKFLOW_ID VERSION` | Revert to a previous version |

**`workflow run` options:**
- `--show-results` / `--no-results` — show detailed result metrics (default: show)
- `--format` / `-f` `table|json` — output format (default: table)
- `--quiet` / `-q` — minimal output

```bash
mixd workflow                              # interactive browser
mixd workflow list
mixd workflow run discovery_mix --format json
mixd workflow runs                         # list all recent runs
mixd workflow runs discovery_mix           # runs for a specific workflow
mixd workflow versions 5                   # version history for workflow 5
mixd workflow revert 5 2                   # revert workflow 5 to version 2
```

### playlist

Manage stored playlists, connector links, and sync operations.

| Command | Description |
|---------|-------------|
| `mixd playlist list` | List all playlists in local database |
| `mixd playlist backup CONNECTOR PLAYLIST_ID` | Backup a playlist from a music service |
| `mixd playlist create --name NAME` | Create a new empty playlist |
| `mixd playlist update PLAYLIST_ID` | Update playlist name and/or description |
| `mixd playlist delete PLAYLIST_ID` | Delete a playlist from local database |
| `mixd playlist links PLAYLIST_ID` | List connector links for a playlist |
| `mixd playlist link PLAYLIST_ID` | Link to an external connector playlist |
| `mixd playlist unlink LINK_ID` | Remove a connector link |
| `mixd playlist sync LINK_ID` | Sync a linked playlist with its connector |
| `mixd playlist sync-preview LINK_ID` | Preview what a sync would change |

**`playlist link` options:** `--connector` / `-c` (required), `--playlist-id` (required), `--direction` / `-d` `push|pull` (default: push)
**`playlist sync` options:** `--direction-override` `push|pull`, `--confirm` (skip confirmation)

```bash
mixd playlist list
mixd playlist backup spotify 37i9dQZF1DX0XUsuxWHRQd
mixd playlist create --name "My Playlist" --description "Best tracks"
mixd playlist links abc-123-uuid                                        # list linked connectors
mixd playlist link abc-123-uuid --connector spotify --playlist-id 37i... --direction push
mixd playlist sync-preview def-456-uuid                                 # preview before sync
mixd playlist sync def-456-uuid --confirm                               # sync with auto-confirm
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

Track management — search, inspect, merge, and manage connector mappings.

| Command | Description |
|---------|-------------|
| `mixd tracks list` | List/search tracks with filtering |
| `mixd tracks show TRACK_ID` | Show detailed track info (likes, plays, playlists) |
| `mixd tracks playlists TRACK_ID` | Show which playlists contain a track |
| `mixd tracks merge` | Merge two duplicate tracks |
| `mixd tracks relink TRACK_ID MAPPING_ID` | Move a mapping to a different track |
| `mixd tracks unlink TRACK_ID MAPPING_ID` | Remove a connector mapping from a track |
| `mixd tracks set-primary TRACK_ID MAPPING_ID` | Set a mapping as the primary source |

**`tracks list` options:**
- `--query` / `-q` — search by title/artist
- `--liked` / `--not-liked` — filter by liked status
- `--connector` / `-c` — filter by connector (e.g., `spotify`)
- `--sort` / `-s` — `title_asc`, `title_desc`, `recent` (default: `title_asc`)
- `--limit` / `-l` — number of tracks (default: 50)
- `--offset` / `-o` — skip tracks (default: 0)
- `--format` / `-f` — `table` or `json` (default: `table`)

**`tracks relink` options:** `--new-track-id` (required) — UUID of the target track

```bash
mixd tracks list --query "Radiohead" --liked --limit 20
mixd tracks show abc-123-uuid
mixd tracks playlists abc-123-uuid
mixd tracks merge --winner-id 10 --loser-id 25 --force
mixd tracks relink abc-123 def-456 --new-track-id ghi-789   # move mapping to another track
mixd tracks unlink abc-123 def-456                           # remove a bad mapping
mixd tracks set-primary abc-123 def-456                      # prefer this connector's metadata
```

### reviews

Manage pending track match reviews — accept or reject proposed matches.

| Command | Description |
|---------|-------------|
| `mixd reviews list` | List pending match reviews with confidence scores |
| `mixd reviews resolve REVIEW_ID --action accept\|reject` | Resolve a single review |
| `mixd reviews resolve --interactive` | Step through pending reviews one by one |

```bash
mixd reviews list                                            # see what needs attention
mixd reviews list --format json                              # machine-readable
mixd reviews resolve abc-123 --action accept                 # accept a match
mixd reviews resolve --interactive                           # guided walkthrough
```

### System Commands

| Command | Description |
|---------|-------------|
| `mixd whoami` | Show user identity, database, mode, connector status |
| `mixd version` | Show version information |
| `mixd connectors` | Check music service connector status |
| `mixd connectors auth spotify` | Authenticate with Spotify via browser OAuth |
| `mixd connectors auth lastfm` | Authenticate with Last.fm via credentials |
| `mixd stats` | Library statistics and dashboard |
| `mixd stats --health` | Run data integrity checks |
| `mixd stats --matching` | Show match method health report |

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
```

### Authentication

```bash
mixd connectors                       # verify service connections
mixd connectors auth spotify          # re-authenticate Spotify
mixd connectors auth lastfm           # re-authenticate Last.fm
```

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
