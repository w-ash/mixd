# Deployment Guide

## Prerequisites

- [Fly.io CLI](https://fly.io/docs/flyctl/install/) (`flyctl`)
- [Docker](https://docs.docker.com/get-docker/) (for local builds)
- [Neon](https://neon.tech) account (free tier — managed PostgreSQL)
- Spotify and Last.fm API credentials

## Local Docker Development

Run the full stack in containers (alternative to `pnpm dev`):

```bash
docker compose --profile full up --build
```

This starts PostgreSQL + the narada app container. The app runs migrations on startup, then serves at `http://localhost:8000`.

Default `docker compose up` still starts only PostgreSQL for the standard `pnpm dev` workflow.

## Neon Database Setup

1. Create a Neon project at [console.neon.tech](https://console.neon.tech)
2. Copy the **pooler** connection string (hostname contains `-pooler`) — it looks like:
   ```
   postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require
   ```
3. Narada auto-normalizes the URL scheme — no manual conversion needed

**Important**: Use the pooler endpoint (built-in PgBouncer) for connection multiplexing. Neon's pooler does not support PostgreSQL startup parameters (`-c` options in connection strings), so narada uses `SET` commands post-connect instead.

## Fly.io Initial Setup

```bash
# Create the app (skip initial deploy)
fly launch --no-deploy

# Set secrets (DATABASE_URL from Neon, API credentials from .env)
fly secrets set \
  DATABASE_URL="postgresql://user:pass@ep-xxx.us-east-1.aws.neon.tech/narada?sslmode=require" \
  SPOTIFY_CLIENT_ID="..." \
  SPOTIFY_CLIENT_SECRET="..." \
  LASTFM_KEY="..." \
  LASTFM_SECRET="..." \
  LASTFM_USERNAME="..." \
  CORS_ORIGINS='["https://narada.fly.dev"]'

# Deploy
fly deploy
```

Migrations run automatically via `release_command` before the new version receives traffic.

## Deploying Updates

```bash
fly deploy
```

This builds the Docker image, runs `alembic upgrade head` in a temporary VM, then swaps traffic to the new version.

## Workflow Migration

If you have workflows in an old SQLite database:

```bash
# Export from old SQLite instance
python scripts/export_sqlite_workflows.py path/to/narada.db exported_workflows/

# Import into the new instance (run locally with DATABASE_URL pointing to Neon)
for f in exported_workflows/*.json; do
  narada workflow create --file "$f"
done
```

Alternatively, if the old instance is still running: `narada workflow export --all`

Templates auto-seed on startup — only user-created workflows need migration.

## Backups

Neon provides automatic point-in-time recovery on all plans (including free tier). Manual snapshots can be created from the Neon console.

## Monitoring

```bash
# Live logs
fly logs

# Health check
curl https://narada.fly.dev/api/v1/health
```

The health endpoint returns `200` with database status when connected, `503` when degraded.

## Known Limitations

- **Spotify auth**: The deployed instance cannot authenticate with Spotify until v0.5.4 (OAuth web flow). The current CLI-based OAuth requires a local browser.
- **Ephemeral storage**: Container filesystem is ephemeral. Import files and workflow run logs don't persist across deploys. The database is the only durable store.
- **Cold start ~7.5s**: Fly.io machine wake (~1s) + Python imports (~5s) + Neon wake + template seed (~1.5s). First request after idle period may take 8-10s. Subsequent requests are fast.
- **Version display**: Container shows `0.0.0-dev` because it uses a deps-only venv (no package metadata). Cosmetic only.

## Cost

- **Fly.io**: shared-cpu-1x, 512MB RAM, scale-to-zero. Near $0/month when idle, ~$2/month active.
- **Neon**: Free tier — 0.5GB storage, scale-to-zero, automatic backups.
- **Total**: $0–2/month for a hobby project.
