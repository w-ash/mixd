# v0.3.0 Manual Testing Guide

Step-by-step verification of the Web UI Foundation + Playlists milestone.
Covers the FastAPI backend, React frontend, and their integration.

## Prerequisites

```bash
# Backend
poetry install                  # Installs fastapi, uvicorn, etc.
poetry run alembic upgrade head # Ensure DB schema is current

# Frontend
pnpm --prefix web install       # Install node dependencies
```

## 1. Backend API (curl)

Start the API server:

```bash
poetry run narada-api
# → Uvicorn running on http://0.0.0.0:8000, reload enabled
```

### 1a. Health Check

```bash
curl http://localhost:8000/api/v1/health
```

Expected: `{"status":"ok","version":"0.3.0"}`

### 1b. OpenAPI Schema

```bash
curl -s http://localhost:8000/api/openapi.json | python -m json.tool | head -20
```

Expected: JSON with `"title": "Narada"`, `"version": "0.3.0"`, and a `paths` object listing all endpoints.

Also browse the interactive docs: http://localhost:8000/api/docs

### 1c. Playlist CRUD Cycle

**Create a playlist:**

```bash
curl -s -X POST http://localhost:8000/api/v1/playlists \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Playlist", "description": "Manual testing"}' | python -m json.tool
```

Expected: 201 response with `id`, `name`, `description`, `track_count: 0`, `entries: []`.
Note the `id` value for subsequent requests.

**List playlists:**

```bash
curl -s http://localhost:8000/api/v1/playlists | python -m json.tool
```

Expected: `{"data": [...], "total": 1, "limit": 50, "offset": 0}` with the created playlist in `data`.

**Get playlist by ID** (replace `1` with your playlist's ID):

```bash
curl -s http://localhost:8000/api/v1/playlists/1 | python -m json.tool
```

Expected: Full playlist detail with `entries: []`.

**Update playlist:**

```bash
curl -s -X PATCH http://localhost:8000/api/v1/playlists/1 \
  -H "Content-Type: application/json" \
  -d '{"name": "Renamed Playlist"}' | python -m json.tool
```

Expected: Updated playlist with new name, original description preserved.

**Get tracks (empty):**

```bash
curl -s http://localhost:8000/api/v1/playlists/1/tracks | python -m json.tool
```

Expected: `{"data": [], "total": 0, "limit": 50, "offset": 0}`

**Delete playlist:**

```bash
curl -s -o /dev/null -w "%{http_code}" -X DELETE http://localhost:8000/api/v1/playlists/1
```

Expected: `204`

**Verify deletion:**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/playlists/1
```

Expected: `404`

### 1d. Error Handling

**Missing required field:**

```bash
curl -s -X POST http://localhost:8000/api/v1/playlists \
  -H "Content-Type: application/json" \
  -d '{}' | python -m json.tool
```

Expected: 422 with validation error details.

**Empty name:**

```bash
curl -s -X POST http://localhost:8000/api/v1/playlists \
  -H "Content-Type: application/json" \
  -d '{"name": ""}' | python -m json.tool
```

Expected: 400 with `"code": "VALIDATION_ERROR"`.

**Nonexistent playlist:**

```bash
curl -s http://localhost:8000/api/v1/playlists/99999 | python -m json.tool
```

Expected: 404 with `"code": "NOT_FOUND"`.

### 1e. Connector Status

```bash
curl -s http://localhost:8000/api/v1/connectors | python -m json.tool
```

Expected: Array with two entries (`spotify` and `lastfm`). `connected` reflects your local auth state — `true` if you have a valid `.spotify_cache` file or Last.fm credentials configured.

### 1f. Pagination

```bash
# Create a few playlists first, then:
curl -s "http://localhost:8000/api/v1/playlists?limit=2&offset=0" | python -m json.tool
curl -s "http://localhost:8000/api/v1/playlists?limit=2&offset=2" | python -m json.tool
```

Expected: `total` stays the same across pages, `data` length respects `limit`, `offset` advances correctly.


## 2. Frontend (Development Mode)

Keep the API server running (port 8000). In a separate terminal:

```bash
pnpm --prefix web dev
# → Vite dev server at http://localhost:5173
```

Open http://localhost:5173 in a browser.

### 2a. Visual Checks

| What to check | Expected |
|----------------|----------|
| Background color | Warm near-black, not cold gray |
| Sidebar | Left sidebar with "narada" branding in warm gold |
| Fonts | Headings in Space Grotesk (geometric sans), body text in Newsreader (editorial serif) |
| Nav items | Dashboard, Playlists, Settings — each clickable |
| Active nav state | Current page highlighted with gold accent + elevated background |

### 2b. Dashboard

- URL: `/`
- Shows "narada" heading and "Personal music metadata hub" subtitle
- Sidebar "Dashboard" item is active

### 2c. Playlists — Empty State

- Navigate to `/playlists` via sidebar
- Should show empty state: music icon, "No playlists yet" heading, "Create your first playlist..." description
- "New Playlist" button visible in both page header and empty state

### 2d. Create Playlist

1. Click "New Playlist" button
2. Dialog appears with "Create Playlist" title
3. Enter name: "My Test Playlist"
4. Enter description: "Testing the web UI"
5. Click "Create"
6. Dialog closes, playlist list refreshes
7. New playlist appears in table with name, 0 tracks, no connectors

**Also test validation:**
- Try submitting with empty name — button should be disabled
- Press Escape to close dialog without creating

### 2e. Playlist List View

After creating 2-3 playlists:

| Column | Expected |
|--------|----------|
| Name | Clickable link, shows description below if present |
| Tracks | "0" (right-aligned, tabular numerals) |
| Connectors | Empty (no linked services yet) |
| Updated | Date in "Mon DD, YYYY" format |

### 2f. Playlist Detail

1. Click a playlist name in the list
2. URL changes to `/playlists/{id}`
3. Page header shows playlist name and description
4. Badge shows "0 tracks"
5. Track table shows empty state: "This playlist is empty"

**Edit playlist:**
1. Click "Edit" button
2. Dialog pre-fills current name and description
3. Change the name, click "Save"
4. Header updates with new name
5. Navigate back to list — name is updated there too

**Delete playlist:**
1. Click "Delete" button
2. Confirmation dialog appears with warning text
3. Click "Delete" to confirm
4. Redirects to `/playlists`
5. Deleted playlist no longer in list

### 2g. Settings Page

1. Navigate to `/settings` via sidebar
2. Shows "Settings" heading and "Manage your connected music services" description
3. Grid of connector cards:
   - **Spotify**: Shows "Connected" (green badge) or "Disconnected" (gray badge) based on `.spotify_cache`
   - **Last.fm**: Shows "Connected" with username or "Disconnected" based on env config
4. Disconnected cards show "Run the CLI to authenticate this service"

### 2h. Navigation & Routing

| Action | Expected |
|--------|----------|
| Click each sidebar item | Page changes, URL updates, sidebar active state moves |
| Browser back/forward | Navigates correctly between visited pages |
| Type `/playlists/999` directly | Shows "Playlist not found" error state |
| Type `/nonexistent` directly | Redirects to `/` (Dashboard) |
| Refresh on `/playlists` | Page loads correctly (no 404) |

### 2i. Loading States

To see skeleton loading states, throttle your network in DevTools (Network tab → "Slow 3G"):

- `/playlists` shows pulsing skeleton rows before table loads
- `/playlists/{id}` shows skeleton blocks before content loads
- `/settings` shows skeleton connector cards before data loads


## 3. Production-Like Serving (Single Server)

Stop both dev servers. Build and serve from one process:

```bash
pnpm --prefix web build
poetry run narada-api
```

Open http://localhost:8000 (not 5173).

| URL | Expected |
|-----|----------|
| `http://localhost:8000/` | React app loads (Dashboard) |
| `http://localhost:8000/playlists` | Playlists page (SPA routing) |
| `http://localhost:8000/playlists/1` | Playlist detail (if exists) or not-found state |
| `http://localhost:8000/settings` | Settings page |
| `http://localhost:8000/api/v1/health` | JSON health response (API still works) |
| `http://localhost:8000/api/docs` | Swagger UI |
| `http://localhost:8000/api/v1/nonexistent` | 404 JSON error (not index.html) |

Key thing to verify: SPA client-side routes (`/playlists`, `/settings`) return `index.html` so React Router handles them, but API routes (`/api/*`) still return proper JSON responses.


## 4. Automated Tests (Verification)

```bash
# Backend: all API integration tests
poetry run pytest tests/integration/api/ -v

# Backend: full test suite
poetry run pytest

# Frontend: TypeScript compiles cleanly
pnpm --prefix web build

# Lint
poetry run ruff check src/interface/api/
```

Expected:
- 33 API integration tests pass
- 1219+ total backend tests pass
- Frontend build succeeds with 0 type errors
- 0 lint errors


## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: fastapi` | Run `poetry install` — fastapi/uvicorn were added to deps |
| Frontend shows blank page | Check browser console for errors; ensure API is running on :8000 |
| Playlists page shows error | API server not running, or database not migrated (`alembic upgrade head`) |
| Connector shows "Disconnected" unexpectedly | Spotify: check `.spotify_cache` exists and token isn't expired. Last.fm: check `LASTFM_API_KEY` and `LASTFM_USERNAME` env vars |
| `pnpm generate` fails | API server must be running if using live URL, or use `./openapi.json` (local file) |
| TypeScript errors in generated code | Run `pnpm generate` to regenerate from latest OpenAPI spec |
