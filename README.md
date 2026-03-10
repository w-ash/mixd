# Narada

**Personal music metadata hub — own your data, build playlists with your own rules.**

Streaming services lock your listening history, likes, and playlists behind proprietary algorithms you can't customize. Narada puts you in control: import your data, sync it across services, and build smart playlists using criteria *you* define.

## What You Can Do

### Own Your Data

Import your music data into a unified local database that *you* control.

- **Listening history** — Last.fm scrobbles (ongoing via API) and Spotify play history (GDPR export)
- **Liked tracks** — import saves from Spotify, export as loves to Last.fm
- **Playlists** — backup any Spotify playlist locally with full track metadata
- **Track identity** — automatic cross-service matching via Spotify IDs, ISRCs, MusicBrainz, and fuzzy metadata

### Sync Across Services

Push and pull data between Spotify, Last.fm, and MusicBrainz — operations no single platform offers.

- **Likes sync** — import liked tracks from Spotify, export them as Last.fm loves (with checkpoint tracking for incremental updates)
- **Playlist linking** — link a canonical playlist to Spotify and sync changes in either direction
- **Enrichment** — pull play counts from Last.fm, explicit flags from Spotify, and recording IDs from MusicBrainz to enrich your local library

### Build Smart Playlists

Create declarative workflow pipelines from composable building blocks. Source tracks, enrich with cross-service data, filter and sort by your criteria, and push results to Spotify.

**Available nodes:**

| Category | What they do |
|----------|-------------|
| **Source** | Pull tracks from any linked playlist (Spotify, local, or canonical) |
| **Enrich** | Add play counts (Last.fm), explicit flags (Spotify), listening history from your database |
| **Filter** | By play count, release date, liked status, duration, explicit content, play recency — or exclude tracks in other playlists |
| **Sort** | By any metric, release date, play frequency, date added, first/last played, or weighted shuffle |
| **Select** | Top N, last N, random N, or a percentage |
| **Combine** | Merge, concatenate, interleave, or intersect multiple sources |
| **Destination** | Create or update playlists (locally and on Spotify) with template naming |

Workflows run via CLI or the web UI's visual editor with live per-node progress.

### Web Interface

A full-featured web UI for browsing and managing your music library.

- **Dashboard** — library stats, connector health, data quality signals
- **Track library** — search, filter, paginate, view detailed track info with cross-service mappings
- **Playlist management** — CRUD, connector linking, push/pull sync
- **Visual workflow editor** — drag-and-drop DAG builder with node palette, config panel, undo/redo, dry-run preview
- **Live execution** — watch workflows run node-by-node with real-time progress, then inspect per-track decisions in run history

## How It Works

Two interfaces (CLI + Web) over a shared application core, built on Clean Architecture with domain-driven design.

```
CLI (Typer + Rich)  ─┐
                     ├→ Use Cases → Domain Logic ← Connectors (Spotify, Last.fm, MusicBrainz)
Web (React + FastAPI)┘                           ← SQLite/SQLAlchemy (async)
```

Workflows are declarative pipelines: **Source → Enrich → Filter → Sort → Select → Destination**. Tracks flow through nodes that compose freely. The pipeline engine (Prefect 3.0) handles orchestration, retry, and progress tracking.

**Stack**: Python 3.14, SQLite + SQLAlchemy 2.0 async, Prefect 3.0, attrs, httpx, FastAPI, React 19, Vite 7, Tailwind CSS v4, Tanstack Query

## Quick Start

```bash
git clone https://github.com/w-ash/narada.git && cd narada
poetry install
cp .env.example .env   # Add your Spotify and Last.fm API credentials
poetry run alembic upgrade head
narada connectors      # Verify service connections
```

```bash
# Import your data
narada likes import-spotify         # Backup liked tracks
narada history import-lastfm        # Import listening history

# Run a workflow
narada workflow                     # Interactive workflow browser

# Launch the web UI
narada-api                          # FastAPI on :8000
pnpm --prefix web install && pnpm --prefix web dev   # Vite on :5173
```

Full setup: [docs/development.md](docs/development.md) — CLI reference: [docs/guides/cli.md](docs/guides/cli.md)

## Documentation

- **Using narada?** → [docs/guides/](docs/guides/) — workflows, likes sync, CLI reference
- **Contributing?** → [docs/development.md](docs/development.md) then [docs/architecture/](docs/architecture/)
- **Full index** → [docs/README.md](docs/README.md)

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.
