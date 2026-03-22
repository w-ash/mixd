# Getting Started

From clone to running your first workflow in about 10 minutes.

## Prerequisites

| Tool | Why | Install |
|------|-----|---------|
| **Python 3.14+** | Runtime | Managed by `uv` (installed automatically) |
| **uv** | Python package manager | `brew install uv` |
| **OrbStack** (recommended) or Docker Desktop | PostgreSQL for local dev + test containers | `brew install orbstack` |
| **pnpm** | Web UI package manager | `brew install pnpm` |

### API credentials (optional — needed for data import)

- **Spotify**: Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard). You'll need the Client ID, Client Secret, and set `http://localhost:8888/callback` as a redirect URI.
- **Last.fm**: Create an app at [last.fm/api/account/create](https://www.last.fm/api/account/create). You'll need the API key, shared secret, and your Last.fm username/password.

## 1. Clone and install

```bash
git clone <repository-url> && cd mixd
uv sync
```

`uv sync` installs Python 3.14, all dependencies, and the `mixd` CLI in one step.

## 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` with your API keys. The file is well-commented — fill in what you have:

- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — for Spotify features
- `LASTFM_KEY` / `LASTFM_SECRET` / `LASTFM_USERNAME` / `LASTFM_PASSWORD` — for scrobble history
- `DATABASE_URL` — leave the default for local Docker PostgreSQL

## 3. Start PostgreSQL

```bash
docker compose up -d
```

This starts PostgreSQL 17 on port 5432. Then run migrations:

```bash
uv run alembic upgrade head
```

## 4. Verify installation

```bash
# Unit tests — no Docker needed, runs in seconds
uv run pytest tests/unit/ -x -q

# Full fast suite (includes integration tests via testcontainers)
uv run pytest

# CLI sanity check
uv run mixd --help
```

## 5. Import your data

With credentials configured:

```bash
# Backup your Spotify liked tracks
mixd likes import-spotify

# Import Last.fm listening history
mixd history import-lastfm
```

Both commands show progress and report totals when complete.

## 6. Run the web UI

A single command starts everything — PostgreSQL, the API server, and Vite:

```bash
pnpm --prefix web install   # First time only
pnpm dev                     # From project root
```

This ensures Docker is running, starts the FastAPI backend on :8000 and the Vite dev server on :5173 side-by-side. Open [http://localhost:5173](http://localhost:5173).

If you only need the Vite frontend (API already running separately):

```bash
pnpm --prefix web dev
```

## 7. Run a workflow

```bash
# Interactive workflow browser
mixd workflow

# Execute a specific workflow
mixd workflow run
```

See [workflows.md](workflows.md) for how to author your own workflows.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Docker not running | `open -a OrbStack` or `open -a Docker` |
| Database connection refused | `docker compose up -d` |
| Migration errors | `uv run alembic upgrade head` |
| Database needs full reset | `docker compose down -v && docker compose up -d && uv run alembic upgrade head` |
| Test failures | `uv run pytest -x --tb=short` |
| Spotify auth issues | Verify redirect URI matches `.env` exactly |

## Next steps

- [workflows.md](workflows.md) — Write your first playlist workflow
- [likes-sync.md](likes-sync.md) — Cross-service likes synchronization
- [cli.md](cli.md) — Full CLI reference
- [../development.md](../development.md) — Development workflows, logging, and common tasks
