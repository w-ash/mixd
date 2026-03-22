# Mixd

**Personal music metadata hub — own your data, put it to work on your terms, and share it without exploitation.**

Streaming services lock your listening history, likes, and playlists behind proprietary algorithms you can't customize. Mixd puts you in control: import your data, sync it across services, build smart playlists using criteria *you* define, and share your taste with friends — without your data being scraped, sold, or fed to someone else's algorithm.

## What You Can Do

### Own Your Data

Import your music data into a unified PostgreSQL database that *you* host and control.

- **Listening history** — Last.fm scrobbles (ongoing via API) and Spotify play history (GDPR export)
- **Liked tracks** — import saves from Spotify, export as loves to Last.fm
- **Playlists** — backup any Spotify playlist locally with full track metadata
- **Track identity** — automatic cross-service matching via Spotify IDs, ISRCs, MusicBrainz, and fuzzy metadata. Manual review for gray-zone matches.

### Sync Across Services

Push and pull data between Spotify, Last.fm, and MusicBrainz — operations no single platform offers.

- **Likes sync** — import liked tracks from Spotify, export them as Last.fm loves (incremental with checkpoint tracking)
- **Playlist linking** — link a canonical playlist to Spotify and sync changes in either direction
- **Enrichment** — pull play counts from Last.fm, explicit flags from Spotify, and recording IDs from MusicBrainz
- **Play history dedup** — cross-service deduplication merges Spotify and Last.fm play records for the same listening event

### Build Smart Playlists

Create declarative workflow pipelines from composable building blocks. Source tracks, enrich with cross-service data, filter and sort by your criteria, and push results to Spotify.

| Category | Nodes |
|----------|-------|
| **Source** | Liked tracks, play history, any linked playlist (Spotify or canonical) |
| **Enrich** | Play counts (Last.fm), explicit flags (Spotify), play history from your database |
| **Filter** | By play count, release date, liked status, duration, explicit content, play recency, artists — or exclude tracks in other playlists |
| **Sort** | By any metric, release date, play frequency, date added, first/last played, or weighted shuffle |
| **Select** | Top N, last N, random N, or a percentage |
| **Combine** | Merge, concatenate, interleave, or intersect multiple track sources |
| **Destination** | Create or update playlists (locally and on Spotify) with template naming |

Workflows run via CLI or the web UI's visual editor with live per-node progress.

### Web Interface

A full-featured web UI for browsing and managing your music library.

- **Dashboard** — library stats, connector health, data quality signals
- **Track library** — trigram search, keyset pagination, detailed track info with cross-service mappings
- **Playlist management** — CRUD, connector linking, push/pull sync
- **Visual workflow editor** — drag-and-drop DAG builder with node palette, config panel, undo/redo, dry-run preview
- **Live execution** — watch workflows run node-by-node with real-time SSE progress, then inspect per-track decisions in run history
- **Track operations** — provenance tracking, duplicate merge, manual relink/unlink, match review for gray-zone mappings

## How It Works

Two interfaces (CLI + Web) over a shared application core, built on Clean Architecture with domain-driven design.

```
CLI (Typer + Rich)  ─┐
                     ├→ Use Cases → Domain Logic ← Connectors (Spotify, Last.fm, MusicBrainz)
Web (React + FastAPI)┘                           ← PostgreSQL/SQLAlchemy (async)
```

Workflows are declarative pipelines: **Source → Enrich → Filter → Sort → Select → Destination**. Tracks flow through nodes that compose freely. The pipeline engine (Prefect 3.0) handles orchestration, retry, and progress tracking.

**Stack**: Python 3.14, PostgreSQL + SQLAlchemy 2.0 async (psycopg3), Prefect 3.0, attrs, httpx, FastAPI, React 19, Vite 8, Biome, Tailwind CSS v4, Tanstack Query

## Quick Start

```bash
git clone https://github.com/w-ash/mixd.git && cd mixd
cp .env.example .env          # Add your Spotify and Last.fm API credentials
docker compose up -d           # Start PostgreSQL
uv sync                        # Install Python dependencies
uv run alembic upgrade head    # Run database migrations
mixd connectors              # Verify service connections
```

```bash
# Import your data
mixd likes import-spotify         # Backup liked tracks
mixd history import-lastfm        # Import listening history

# Run a workflow
mixd workflow                     # Interactive workflow browser

# Launch the web UI
pnpm dev                            # Starts PostgreSQL + API + Vite dev server
```

Full setup: [docs/development.md](docs/development.md) — CLI reference: [docs/guides/cli.md](docs/guides/cli.md)

## Documentation

- **Using mixd?** → [docs/guides/](docs/guides/) — workflows, likes sync, CLI reference
- **Contributing?** → [docs/development.md](docs/development.md) then [docs/architecture/](docs/architecture/)
- **Roadmap & backlog** → [docs/backlog/](docs/backlog/)
- **Full index** → [docs/README.md](docs/README.md)

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.
