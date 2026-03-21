# Running & Deploying Narada

## Local Development

### Quick start

```bash
pnpm dev
```

This single command:
1. Starts PostgreSQL via Docker Compose
2. Runs Alembic migrations (`alembic upgrade head`)
3. Starts the FastAPI server on port 8000 (with hot-reload)
4. Starts the Vite dev server on port 5173 (with HMR)
5. Opens the browser once the API health check passes

### Individual commands

When you need finer control:

```bash
docker compose up -d                    # Start PostgreSQL only
uv run alembic upgrade head             # Run migrations
uv run narada-api                       # API server (hot-reload on src/)
pnpm --prefix web dev                   # Vite dev server (HMR)
```

### Docker Compose profiles

```bash
docker compose up -d                        # Default: PostgreSQL only (for pnpm dev)
docker compose --profile full up --build    # Full stack: PostgreSQL + app container
```

The `full` profile builds the production Dockerfile and runs the app in a container — useful for testing the production build locally before deploying.

### Environment configuration

Copy `.env.example` to `.env` and fill in credentials:

```bash
cp .env.example .env
```

Required for full functionality:
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
- `LASTFM_KEY` / `LASTFM_SECRET` — [Last.fm API](https://www.last.fm/api/account/create)
- `DATABASE_URL` — defaults to local Docker Postgres (`postgresql+psycopg://narada:narada@localhost:5432/narada`)

## Deploying to Fly.io

### First-time setup

1. Install the [Fly CLI](https://fly.io/docs/flyctl/install/)

2. Create the app (already done — `fly.toml` exists):
   ```bash
   fly launch  # Only needed once; app name: narada, region: sjc
   ```

3. Create a [Neon](https://neon.tech) PostgreSQL project and copy the **pooler** connection string from the dashboard.

4. Set required secrets:
   ```bash
   fly secrets set \
     DATABASE_URL=postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require \
     SPOTIFY_CLIENT_ID=... \
     SPOTIFY_CLIENT_SECRET=... \
     SPOTIFY_REDIRECT_URI=https://your-app.fly.dev/auth/spotify/callback \
     LASTFM_KEY=... \
     LASTFM_SECRET=...
   ```

5. Deploy:
   ```bash
   fly deploy
   ```

### Automated deploys (CI/CD)

Pushing a version tag triggers the full release pipeline:

```bash
git tag v0.6.0
git push origin v0.6.0
```

**What happens:**
1. CI has already passed on `main` (lint, type check, tests with coverage, frontend build)
2. GitHub Actions `release.yml` runs:
   - **Release job** — generates changelog via git-cliff, creates GitHub Release
   - **Deploy job** — runs `flyctl deploy --remote-only` on Fly's remote builders
3. Fly.io runs `alembic upgrade head` as the release command (before switching traffic)
4. Health check at `/api/v1/health` gates the traffic cutover

**Prerequisite:** A `FLY_API_TOKEN` secret must exist in GitHub (Settings → Secrets → Actions). Generate one with:
```bash
fly tokens create deploy -x 999999h
```

### Manual deploy

Deploy from your local machine without tagging:

```bash
fly deploy
```

This builds the Dockerfile on Fly's remote builders, runs migrations, and switches traffic.

### Secrets management

```bash
fly secrets list                    # View configured secrets
fly secrets set KEY=value           # Set or update a secret
fly secrets unset KEY               # Remove a secret
```

### Monitoring

```bash
fly logs                            # Tail production logs
fly status                          # Machine state and health
fly ssh console                     # SSH into the running machine
```

The health check (`GET /api/v1/health`) runs every 30s with a 30s grace period on startup.

## Infrastructure Details

| Setting | Value |
|---------|-------|
| Hosting | [Fly.io](https://fly.io) |
| Region | `sjc` (San Jose) |
| VM | `shared-cpu-1x`, 512MB |
| Scaling | Auto-stop to zero, auto-start on request |
| HTTPS | Forced (Fly proxy terminates TLS) |
| Internal port | 8000 |
| Concurrency | Soft limit 20, hard limit 25 connections |
| Database | PostgreSQL 17 via [Neon](https://neon.tech) (pooler endpoint, scale-to-zero) |
| Container | Multi-stage Dockerfile (Python 3.14 + Node 22) |
