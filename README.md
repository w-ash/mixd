# Narada

**Own your music data. Create playlists using YOUR criteria, not proprietary algorithms.**

Streaming services lock your data behind opaque algorithms. Narada gives you control: import listening history, define workflow pipelines with your own logic (e.g., "liked but unplayed 6mo"), sync across services.

## Features

- **Music Service Integration**: Connect with Spotify, Last.fm, and MusicBrainz
- **Workflow System**: Define complex playlist transformation pipelines using JSON
- **Smart Filtering**: Filter tracks by release date, artist, popularity, and more
- **Data Persistence**: Store and manage playlists in local database and Spotify


## Getting Started

### Installation

```bash
# Clone the repository
git clone https://github.com/w-ash/narada.git
cd narada

# Install dependencies with Poetry
poetry install

# Activate virtual environment
source $(poetry env info --path)/bin/activate
```

### Setup

Run the setup command to configure your music service connections:

```bash
narada setup
```

This will guide you through connecting your Spotify and Last.fm accounts.

### Getting Your Data

**Historical Data (Spotify):**

1. Request your extended streaming history from Spotify: [Understanding Your Data](https://support.spotify.com/us/article/understanding-your-data/)
   - Go to Spotify Privacy Settings → "Extended streaming history"
   - Request can take up to 30 days
   - Provides complete listening history in JSON format

2. Import once you receive the data:
   ```bash
   narada history import-spotify /path/to/Streaming_History_Audio_*.json
   ```

**Ongoing Tracking (Last.fm):**

1. Create a Last.fm account: [Last.fm Sign Up](https://www.last.fm/join)
2. Connect Spotify to Last.fm: [Spotify Settings → Apps → Last.fm](https://www.last.fm/settings/applications)
3. Let it run for a few weeks to build up play history
4. Import incrementally:
   ```bash
   narada history import-lastfm  # Gets everything since last import
   ```

**Why this workflow?**
- Spotify: Complete historical data (one-time import)
- Last.fm: Ongoing tracking (run imports regularly)
- Result: Full listening history powering your custom workflows

### Basic Commands

```bash
# Single help system showing all commands
narada --help

# Check service connection status and setup
narada status
narada setup

# Import music play history
narada history import-lastfm
narada history import-spotify /path/to/spotify_export.json

# Manage liked tracks across services
narada likes import-spotify --limit 1000
narada likes export-lastfm --batch-size 100

# Playlist workflows
narada workflow run discovery_mix
narada workflow list
```


## Workflow System

Narada uses a customizable workflow system to create and transform playlists. Workflows are defined in JSON files and processed through a directed acyclic graph (DAG) of nodes.

### Running Workflows

Narada's workflow system features organized command groups and rich visual feedback:

```bash
# Workflow execution
narada workflow               # Interactive workflow browser
narada workflow list          # List all available workflows
narada workflow run discovery_mix

# Music history import
narada history                # Interactive history menu
narada history import-lastfm
narada history import-spotify /path/to/export.json

# Liked tracks sync across services
narada likes                  # Interactive likes menu
narada likes import-spotify --limit 500
narada likes export-lastfm --batch-size 100

# Playlist data management
narada playlist list          # List stored playlists in database
narada playlist backup spotify <playlist_id>
narada playlist delete <playlist_id>
```

#### Features

- **Organized Commands**: 6 command groups for logical functionality (playlist, history, likes, track, setup, status)
- **Interactive Menus**: Run commands without arguments for guided workflows
- **Rich Progress Display**: Real-time progress tracking with visual feedback
- **Professional Output**: Beautiful tables and panels with color coding
- **Error Handling**: Clear error messages with helpful context

### Workflow Definition

Create custom workflows by defining JSON files in the definitions directory:

```json
{
  "id": "my_workflow",
  "name": "My Custom Workflow",
  "description": "Creates a personalized playlist based on my preferences",
  "version": "1.0",
  "tasks": [
    {
      "id": "source_playlist",
      "type": "source.spotify_playlist",
      "config": {
        "playlist_id": "spotify_playlist_id_here"
      }
    },
    {
      "id": "filter_recent",
      "type": "filter.by_release_date",
      "config": {
        "max_age_days": 90
      },
      "upstream": ["source_playlist"]
    },
    {
      "id": "destination",
      "type": "destination.create_spotify_playlist",
      "config": {
        "name": "My Recent Discoveries",
        "description": "Recently released tracks I might like"
      },
      "upstream": ["filter_recent"]
    }
  ]
}
```

### Available Node Types

- **Sources**: Fetch tracks from Spotify playlists
- **Filters**: Filter tracks by various criteria (date, duplicates, etc.)
- **Enrichers**: Add metadata from Last.fm and other services
- **Sorters**: Sort tracks by popularity, play count, etc.
- **Combiners**: Merge, concatenate, or interleave playlists
- **Selectors**: Limit tracks based on various criteria
- **Destinations**: Create or update playlists in Spotify or internal database

See the Workflow Guide for a complete reference of available nodes and configuration options.

## Example Workflows

### Discovery Mix

The `discovery_mix` workflow creates a playlist of new releases from multiple curated sources:

1. Fetches tracks from multiple Spotify playlists (Pollen, Serotonin, Metropolis, Stereogum)
2. Filters each source to tracks released within the last 90 days
3. Limits each source to a specified number of tracks
4. Combines all sources and sorts by popularity
5. Appends Release Radar tracks at the beginning
6. Removes duplicates and tracks already in other playlists
7. Creates a new Spotify playlist with the results

Run this workflow with:

```bash
narada workflow run discovery_mix
```

## Architecture

Narada uses **Domain-Driven Design (DDD) + Hexagonal Architecture** for reliability and performance:

- **Fast**: Optimized batch processing and async operations
- **Reliable**: Comprehensive error handling and data validation
- **Extensible**: Plugin-based workflow system and self-contained service connectors
- **Type-Safe**: Full typing support with Python 3.14+ features
- **Maintainable**: Clean architecture with strict dependency boundaries

## Development

### Project Structure

```
narada/
├── src/                 # Core application code
│   ├── domain/         # Business logic and entities
│   │   ├── entities/   # Track, Playlist, Play objects
│   │   ├── matching/   # Track matching algorithms
│   │   └── workflows/  # Domain logic for operations
│   ├── application/    # Use cases and orchestration
│   │   ├── use_cases/  # Single business operations  
│   │   ├── services/   # Multi-repository coordination
│   │   └── workflows/  # Prefect 3.0 orchestration
│   ├── infrastructure/ # External services and persistence
│   │   ├── connectors/ # Self-contained service modules (spotify/, lastfm/)
│   │   └── persistence/ # Database and repositories
│   └── interface/      # CLI and future web interfaces
├── docs/               # Documentation and guides
├── tests/              # Test suite
└── scripts/            # Utility scripts
```

### Development Commands

```bash
# Run tests
poetry run pytest

# Run tests with coverage  
poetry run pytest --cov=narada --cov-report=html

# Lint and format code
poetry run ruff check --fix .
poetry run ruff format .

# Type checking  
poetry run basedpyright src/

# Run integration tests only
poetry run pytest -m integration

# Test the CLI interface
narada --help                           # See the unified help interface
narada history import-spotify --help   # Test history import commands
narada playlist run --help             # Test playlist workflow commands
narada likes --help                    # Test likes sync commands
```

### Code Style

- Python 3.14+ with modern typing features
- Line length: 88 characters (enforced by Ruff)
- Immutable domain models using attrs
- Repository pattern for all data access
- Batch-first design for all operations
- UTC timezone for all datetime objects

### Contributing

1. Fork the repository
2. Create a feature branch
3. Follow the coding standards in CLAUDE.md
4. Add comprehensive tests
5. Run linting and type checking
6. Submit a pull request

## Documentation

Comprehensive documentation is available in the `/docs` directory:

- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System architecture and design decisions
- **[DEVELOPMENT.md](docs/DEVELOPMENT.md)** - Developer onboarding and contribution guide
- **[DATABASE.md](docs/DATABASE.md)** - Database schema and design
- **[API.md](docs/API.md)** - Complete CLI command reference
- **[workflow_guide.md](docs/workflow_guide.md)** - Workflow system documentation
- **[likes_sync_guide.md](docs/likes_sync_guide.md)** - Likes synchronization between Spotify and Last.fm
- **[ROADMAP.md](ROADMAP.md)** - Strategic roadmap and version plan
- **[BACKLOG.md](docs/BACKLOG.md)** - Detailed epics and task breakdowns
- **[CLAUDE.md](CLAUDE.md)** - Development commands and style guide

## License

This project is licensed under the MIT License - see the LICENSE file for details.