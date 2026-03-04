# Narada

**Own your music data. Create playlists using YOUR criteria, not proprietary algorithms.**

Streaming services lock your data behind opaque algorithms. Narada gives you control: import listening history from Spotify and Last.fm, define workflow pipelines with your own logic (e.g., "liked tracks unplayed for 6 months"), and sync across services.

## What It Does

- **Cross-Service Playlists**: Build playlists using data from Spotify, Last.fm, and MusicBrainz together
- **Workflow Pipelines**: Declarative JSON workflows — source tracks, filter, sort, enrich, and push to Spotify
- **Listening History**: Import Spotify GDPR exports and ongoing Last.fm scrobbles into a unified database
- **Web UI + CLI**: Browse playlists and manage connectors in the browser, or use the CLI for power operations

### Example Workflows

- **"Current Obsessions"** — liked tracks with 8+ plays in the last 30 days, top 20
- **"Hidden Gems"** — liked tracks with 3+ plays but untouched for 6 months
- **"Discovery Mix"** — interleave recent plays with old favorites, random 40

## Getting Started

### Prerequisites

- Python 3.14+, [Poetry](https://python-poetry.org/)
- Node.js 20+, [pnpm](https://pnpm.io/) (for the web UI)

### Installation

```bash
git clone https://github.com/w-ash/narada.git
cd narada

# Backend
poetry install

# Frontend
pnpm --prefix web install
```

### Configuration

Create a `.env` file with your API credentials:

```bash
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
LASTFM_KEY=your_lastfm_api_key
LASTFM_USERNAME=your_lastfm_username
```

Connect Spotify (opens browser for OAuth):
```bash
narada setup
```

### Running

```bash
# Web UI — backend + frontend
narada-api                        # FastAPI on :8000
pnpm --prefix web dev             # Vite dev server on :5173, proxies /api → :8000

# CLI
narada --help                     # All commands
narada workflow                   # Interactive workflow browser
narada history import-lastfm      # Import listening history
narada likes import-spotify       # Backup liked tracks
```

## Architecture

**Domain-Driven Design + Clean Architecture.** Two presentation layers (CLI + Web) over a shared application core.

```
narada/
├── src/
│   ├── domain/              Pure business logic (matching, transforms, entities)
│   ├── application/         Use cases, workflows (Prefect 3.0), services
│   ├── infrastructure/      Spotify/Last.fm/MusicBrainz connectors, SQLAlchemy repos
│   └── interface/
│       ├── cli/             Typer + Rich
│       └── api/             FastAPI (REST + SSE)
├── web/                     React 19 + Vite 7 + Tailwind v4 + Tanstack Query
├── tests/                   1235 pytest tests + 70 Vitest tests
└── docs/                    Architecture, API reference, workflow guide
```

**Stack**: Python 3.14, SQLite + SQLAlchemy 2.0 async, Prefect 3.0, attrs, httpx, FastAPI, React 19, Vite 7, Tailwind CSS v4, shadcn/ui, Tanstack Query, Orval, Biome

## Development

```bash
# Tests
poetry run pytest                    # Backend fast tests (~32s, 1235 tests)
poetry run pytest -m ""              # All tests including slow
pnpm --prefix web test               # Frontend component tests (70 tests)

# Code quality
poetry run ruff check . --fix        # Lint
poetry run ruff format .             # Format
poetry run basedpyright src/         # Type check (0 errors)
pnpm --prefix web check              # Biome lint + TypeScript

# Database
poetry run alembic upgrade head      # Migrate
```

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layer responsibilities and dependency rules |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Developer onboarding and full command reference |
| [DATABASE.md](docs/DATABASE.md) | Schema, relationships, and migration patterns |
| [Workflow Guide](docs/GUIDE_WORKFLOWS.md) | Node catalog and workflow authoring |
| [Web UI Specs](docs/web-ui/README.md) | User flows, API contracts, frontend architecture |
| [ROADMAP.md](ROADMAP.md) | Version plan and technology decisions |
| [BACKLOG.md](docs/BACKLOG.md) | Detailed epics and task breakdowns |
| [CLAUDE.md](CLAUDE.md) | AI-assisted development patterns and conventions |

## License

This project is licensed under the MIT License — see the LICENSE file for details.
