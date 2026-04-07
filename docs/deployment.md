# Running & Deploying Mixd

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
uv run mixd-api                       # API server (hot-reload on src/)
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
- `DATABASE_URL` — defaults to local Docker Postgres (`postgresql+psycopg://mixd:mixd@localhost:5432/mixd`)

## Deploying to Fly.io

### First-time setup

1. Install the [Fly CLI](https://fly.io/docs/flyctl/install/)

2. Create the app (already done — `fly.toml` exists):
   ```bash
   fly launch  # Only needed once; app name: mixd, region: sjc
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
git tag v0.6.2
deploy            # pushes main + tags, then watches CI in your terminal
```

The `deploy` shell function (defined in `~/.zshrc`) runs `git push origin main --tags` then streams the CI output via `gh run watch`.

**What happens:**
1. CI has already passed on `main` (lint, type check, tests with coverage, frontend build)
2. GitHub Actions `release.yml` runs:
   - **Release job** — generates changelog via git-cliff, creates GitHub Release
   - **Deploy job** — runs `flyctl deploy --remote-only` with `BUILD_HASH=$GITHUB_SHA`
3. Fly.io runs `alembic upgrade head` as the release command (before switching traffic)
4. Health check at `/api/v1/health` gates the traffic cutover

The build hash is baked into the frontend at build time (`__BUILD_HASH__` in `vite.config.ts`). It appears on the login page for deploy verification.

**Prerequisite:** A `FLY_API_TOKEN` secret must exist in GitHub (Settings → Secrets → Actions). Generate one with:
```bash
fly tokens create deploy -x 999999h
```

### Manual deploy

Deploy from your local machine without tagging:

```bash
fly deploy --build-arg BUILD_HASH=$(git rev-parse --short HEAD)
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
| Scaling | Always-on (1 machine minimum for webhook reliability) |
| HTTPS | Forced (Fly proxy terminates TLS) |
| Internal port | 8000 |
| Concurrency | Soft limit 20, hard limit 25 connections |
| Custom domain | `mixd.me` (TLS cert managed by Fly) |
| Auth | [Neon Auth](https://neon.com/docs/auth/overview) (Better Auth, EdDSA JWTs) |
| Database | PostgreSQL 17 via [Neon](https://neon.tech) (pooler endpoint, scale-to-zero) |
| CI Database | Neon branch per PR (replaces postgres container) |
| Auth Webhooks | `user.created` event at `/webhooks/neon-auth` |
| Container | Multi-stage Dockerfile (Python 3.14 + Node 22) |

## Neon Integration

### CI: Branch-per-PR

CI creates a Neon branch for each pull request, runs Alembic migrations against it, executes tests, and posts a schema diff comment if migrations changed. Branches are deleted when PRs close.

**Required GitHub config:**
- **Secret** `NEON_API_KEY` — generate from Neon Console → Account Settings → API Keys
- **Variable** `NEON_PROJECT_ID` — from Neon Console → Project Settings (currently `delicate-hill-47205215`)

### Auth Webhooks

Neon Auth sends events to `POST /webhooks/neon-auth` with EdDSA (Ed25519) signature verification. Currently subscribed to `user.created` (logs signups). Can add `user.before_create` to gate signups via the email allowlist.

**Register or update the webhook:**
```bash
curl -X PUT "https://console.neon.tech/api/v2/projects/$NEON_PROJECT_ID/branches/$NEON_BRANCH_ID/auth/webhooks" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $NEON_API_KEY" \
  -d '{"enabled": true, "webhook_url": "https://mixd.fly.dev/webhooks/neon-auth", "enabled_events": ["user.created"]}'
```

**Current values:** Project `delicate-hill-47205215`, branch `br-dry-water-ak9zsove`.

**Available events:**
| Event | Blocking | Purpose |
|---|---|---|
| `user.created` | No | Log signups |
| `user.before_create` | Yes (5s timeout) | Validate email against allowlist before account creation |
| `send.otp` | No | Custom OTP delivery (email, SMS, WhatsApp) |
| `send.magic_link` | No | Custom magic link delivery |
