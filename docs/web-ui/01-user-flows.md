# User Flows

> Primary specification document. Every feature decision starts here.
> Technical details (API, components, architecture) flow from these journeys.

Each flow is organized by **user goal**, not by page. Every flow includes:
- **Trigger** -- what prompts the user to start this journey
- **Steps** -- what the user sees and does, step by step
- **Backend calls** -- which API endpoints and use cases are involved
- **Edge cases** -- what can go wrong and how we handle it

---

## 1. Connecting Services

> **Staged approach**: In v0.3.0–v0.4.0 (local development), connectors use CLI-established credentials read from the local filesystem and environment. In v0.5.0 (deployed), full web OAuth flows replace manual credential management. Both stages share the same Settings page UI — only the auth mechanism behind **Connect** changes.

### 1.1 Connect Spotify

**Trigger**: User navigates to Settings to connect a music service.

#### v0.3.0–v0.4.0: Local credential detection

The web UI reads existing credentials established by the CLI (`narada` commands or direct env var configuration).

**Steps**:

1. Settings page (`/settings`) shows available connectors with connection status.

2. **Spotify** checks for a valid token in the `.spotify_cache` file (same file the CLI writes).
   - If found and not expired: shows **Connected** with the account name.
   - If expired: backend attempts a token refresh using the stored refresh token.
   - If missing or refresh fails: shows **Not Connected** with guidance: "Run `narada likes import-spotify` from the CLI to authenticate, or paste credentials below."

3. **Manual token input** (fallback): Settings provides a text field to paste a Spotify OAuth token directly. This is for development convenience — the CLI remains the primary auth method.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Check status | `GET /connectors` | Read `.spotify_cache` + validate | Needs implementation |
| Manual input | `POST /connectors/spotify/token` | Store token to `.spotify_cache` | Needs implementation |

**Edge cases**:
- Token expired and refresh fails: show **Disconnected** with re-auth guidance.
- `.spotify_cache` file doesn't exist: show **Not Connected** with CLI instructions.
- User is already connected: "Connect Spotify" button becomes "Reconnect Spotify" (for re-auth via CLI).

#### v0.5.0: Web OAuth flow

With `DatabaseTokenStorage` and a hosted callback URL, the Settings page handles the full OAuth redirect.

**Steps**:

1. Settings page (`/settings`) shows available connectors. Each has a **Connect** button (or **Reconnect** if previously connected).

2. User clicks **Connect Spotify**.
   - Frontend calls `GET /connectors/spotify/auth-url`.
   - Browser redirects to Spotify's OAuth consent page.

3. User grants permission on Spotify.
   - Spotify redirects to `GET /auth/spotify/callback?code=...&state=...`.
   - Backend exchanges code for tokens, stores them in `oauth_tokens` table via `DatabaseTokenStorage`.
   - Browser redirects to `/settings` with a success toast: "Spotify connected."

4. Settings page now shows Spotify as **Connected** with the account name.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `GET /connectors/spotify/auth-url` | Generate OAuth URL | Needs implementation (v0.5.0) |
| 3 | `GET /auth/spotify/callback` | Exchange code, store via `DatabaseTokenStorage` | Needs implementation (v0.5.0) |
| 4 | `GET /connectors` | List connector status | Needs implementation |

**Edge cases**:
- User denies OAuth consent: callback receives `error=access_denied`. Redirect to `/settings` with error toast: "Spotify connection cancelled."
- Token exchange fails (network error, expired code): Show error toast with retry link.

---

### 1.2 Connect Last.fm

**Trigger**: User navigates to Settings to connect Last.fm.

#### v0.3.0–v0.4.0: Environment credentials

Last.fm uses API key + username + password from environment variables. The session key is obtained at runtime via `auth.getMobileSession` and cached in memory (not persisted to disk).

**Steps**:

1. Settings page shows Last.fm connection status.

2. **Last.fm** checks for configured credentials (`LASTFM_API_KEY`, `LASTFM_USERNAME`, `LASTFM_PASSWORD` in environment).
   - If all present: backend calls `auth.getMobileSession` to validate. Shows **Connected** with the username.
   - If missing: shows **Not Connected** with guidance: "Set Last.fm credentials in your environment variables."

3. **Manual credential input** (fallback): Settings provides fields for API key, username, and password. Backend validates by attempting `auth.getMobileSession`.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Check status | `GET /connectors` | Validate env credentials | Needs implementation |
| Manual input | `POST /connectors/lastfm/credentials` | Validate + store credentials | Needs implementation |

**Edge cases**:
- Invalid credentials: `auth.getMobileSession` fails. Show error: "Last.fm authentication failed. Check your credentials."
- API key valid but password wrong: same error (Last.fm doesn't distinguish).

#### v0.5.0: Web auth flow

With database-backed credential storage, Settings handles the Last.fm web auth flow directly.

**Steps**:

1. User clicks **Connect Last.fm** on the Settings page.
   - A modal opens: "Enter your Last.fm username" with a text input.

2. User enters their username and clicks **Authenticate**.
   - Frontend calls the Last.fm auth URL endpoint.
   - Browser redirects to Last.fm's auth page.

3. User grants permission on Last.fm.
   - Last.fm redirects back with a session token.
   - Backend stores the session key in the database.
   - Browser redirects to `/settings` with success toast: "Last.fm connected."

4. Settings shows Last.fm as **Connected** with the username.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `GET /connectors/lastfm/auth-url` | Generate Last.fm auth URL | Needs implementation (v0.5.0) |
| 3 | `GET /auth/lastfm/callback` | Store session key in database | Needs implementation (v0.5.0) |

**Edge cases**:
- Last.fm auth fails: redirect with error toast.
- Username doesn't match authenticated account: backend validates and warns.

---

## 2. Browsing the Library

> **Available starting v0.3.2.** Requires `ListTracksUseCase`, `SearchTracksUseCase`, and `GetTrackDetailsUseCase`.

### 2.1 Library

**Trigger**: User clicks **Library** in the sidebar navigation.

**Steps**:

1. Library page loads with a **paginated track table**.
   - Default sort: recently added (most recent first).
   - Shows: title, artist(s), album, duration, connector icons (which services have this track).
   - Pagination: 50 tracks per page with offset controls.

2. **Search**: User types in the search bar at the top.
   - Frontend debounces (300ms), then calls `GET /tracks?q=<search>&limit=50&offset=0`.
   - Results replace the current list. Total count updates.
   - Search matches against title, artist name, and album.

3. **Filtering**: Filter bar below search with dropdowns:
   - **Connector**: All / Spotify / Last.fm / Apple Music / MusicBrainz
   - **Liked status**: All / Liked / Not Liked (with service sub-filter)
   - **Has mappings**: All / Mapped to all connectors / Missing mappings
   - Filters are additive (AND logic). Changing a filter resets to page 1.

4. **Sorting**: Click column headers to sort.
   - Sortable columns: Title (A-Z), Artist (A-Z), Album (A-Z), Duration, Release Date
   - Click again to reverse. Active sort column shows arrow indicator.

5. **Selection**: Clicking a track row navigates to the track detail page (`/library/{id}`).

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load page | `GET /tracks?limit=50&offset=0` | `ListTracksUseCase` | Needs implementation (v0.3.2) |
| Search | `GET /tracks?q=...&limit=50` | `SearchTracksUseCase` | Needs implementation (v0.3.2) |
| Filter by connector | `GET /tracks?connector=spotify` | `ListTracksUseCase` (with filter param) | Needs implementation |
| Sort | `GET /tracks?sort=title&order=asc` | `ListTracksUseCase` (with sort param) | Needs implementation |

**Edge cases**:
- Very large libraries (>50,000 tracks): Offset pagination remains functional. Consider adding "jump to page" input for deep navigation.
- Search returns no results: Show "No tracks matching '...' found." with suggestion to clear filters.
- Slow API response: Show skeleton loading state for the table body.

---

### 2.2 Track Detail

**Trigger**: User clicks a track row in the library list.

**Steps**:

1. Track detail page (`/library/{id}`) loads with:
   - **Header**: Title, Artist(s), Album, Duration, Release Date, ISRC
   - **Connector Mappings** section: table of linked services
     - Each row: Connector icon + name, External ID, Match Method, Confidence score (color-coded: green >80, yellow 50-80, red <50), Primary indicator
   - **Like Status** section: per-service like state with dates
     - "Liked on Spotify (Dec 15, 2024)" / "Not liked on Last.fm"
   - **Play History** section: summary stats
     - Total plays, Last played date, First played date
     - Mini chart (sparkline) of play frequency over time if data exists
   - **Metrics** section: connector-specific metrics
     - Last.fm: user play count, global play count, listener count


2. **Actions** available on this page:
   - **Like/Unlike**: Toggle like status per service. Calls `POST /tracks/{id}/like` or `DELETE /tracks/{id}/like` with `{ connector: "spotify" }`.
   - **Add to Playlist**: Opens a modal with playlist selector. Search + select one or more playlists. Calls `POST /playlists/{id}/tracks` for each.
   - **View Playlists**: "Appears in 3 playlists" link. Expands to show playlist names with links.
   - **Fix Mapping**: Per-connector "Edit" button on each mapping row (see Flow 2.3).

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load detail | `GET /tracks/{id}` | `GetTrackDetailsUseCase` | Needs implementation (v0.3.2) |
| Like track | `POST /tracks/{id}/like` | `SyncLikesUseCase` (single-track variant) | Needs implementation |
| Unlike track | `DELETE /tracks/{id}/like` | `SyncLikesUseCase` (single-track variant) | Needs implementation |
| Add to playlist | `POST /playlists/{id}/tracks` | `UpdateCanonicalPlaylistUseCase` | Exists |
| Which playlists | `GET /tracks/{id}/playlists` | Needs new query | Needs implementation |

**Edge cases**:
- Track has no connector mappings: Show "Not mapped to any services. This track exists only in your Narada library."
- Track has no play history: Show "No play data. Import your listening history to see plays."
- Track was deleted: 404 response. Show "Track not found" with link back to library.

---

### 2.3 Correcting a Bad Connector Mapping

**Trigger**: User notices wrong album art or metadata on a track, indicating a bad mapping. Clicks "Edit" on a connector mapping row.

**Steps**:

1. A **search modal** opens:
   - Header: "Find the correct Spotify track" (or whichever connector)
   - Pre-filled search: current track's "Artist - Title"
   - Search results show external tracks with: title, artist(s), album, duration, preview button (if available)

2. User searches, browses results, and identifies the correct track.
   - Each result has a **Select** button.

3. User clicks **Select** on the correct result.
   - Confirmation dialog: "Replace mapping for 'Track Title' on Spotify?"
     - Shows old: "Current: external_id_123 (Confidence: 45%)"
     - Shows new: "New: external_id_456 - 'Correct Album' by Artist"
   - User confirms.

4. Frontend calls `PATCH /tracks/{id}/mappings/{mapping_id}` with `{ connector_track_id: "new_id" }`.
   - Success: toast "Mapping updated." Track detail refreshes.
   - The new mapping gets confidence: 100%, match_method: "manual".

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 1 | `GET /connectors/{connector}/search?q=...` | Connector search | Needs implementation |
| 4 | `PATCH /tracks/{id}/mappings/{mapping_id}` | Manual mapping correction | Needs implementation |

**Edge cases**:
- Connector search API rate limited: Show "Search temporarily unavailable. Try again in a moment."
- User selects a track that's already mapped to another canonical track: Warning dialog "This Spotify track is already linked to 'Other Track'. Proceed anyway?" (could indicate a merge candidate).
- Connector is disconnected: "Edit" button disabled with tooltip "Reconnect Spotify to fix mappings."

---

## 3. Managing Playlists

### 3.1 Playlist List

**Trigger**: User clicks **Playlists** in the sidebar.

**Steps**:

1. Playlist list page loads showing all canonical playlists.
   - Each row: Playlist name, Track count, Description (truncated), Connector icons (linked services), Last modified date.
   - Sort by: Name (A-Z), Track count, Last modified (default).

2. **Create Playlist** button in the top-right corner.

3. Clicking a playlist row navigates to `/playlists/{id}`.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load list | `GET /playlists?limit=50&offset=0` | `ListPlaylistsUseCase` | Exists |

**Edge cases**:
- User has many playlists (>100): Paginate. Add search/filter bar.

---

### 3.2 Playlist Detail

**Trigger**: User clicks a playlist from the list.

**Steps**:

1. Playlist detail page (`/playlists/{id}`) loads:
   - **Header**: Playlist name, description, track count, total duration, connector links
   - **Tracks**: Ordered table with columns:
     - Position (#), Title, Artist(s), Album, Duration, Added At, Actions (remove button)
   - **Connector Links** summary: "Linked to Spotify: 'My Playlist'" with sync status badge

2. The track table supports:
   - **Reorder** via drag-and-drop (or up/down arrow buttons for accessibility)
   - **Remove** individual tracks (X button with confirmation)
   - **Batch operations**: Multi-select checkbox column. Selected tracks enable "Remove Selected" action.

3. **Action buttons** in the header:
   - **Add Tracks** (see Flow 3.4)
   - **Edit Details** (inline edit of name/description)
   - **Manage Links** (navigates to `/playlists/{id}/links`)
   - **Delete Playlist** (danger action with confirmation)

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load detail | `GET /playlists/{id}` | `ReadCanonicalPlaylistUseCase` | Exists |
| Load tracks | `GET /playlists/{id}/tracks?limit=50&offset=0` | `ReadCanonicalPlaylistUseCase` | Exists |
| Remove track | `DELETE /playlists/{id}/tracks/{entry_id}` | `UpdateCanonicalPlaylistUseCase` | Exists |
| Reorder | `PATCH /playlists/{id}/tracks/reorder` | `UpdateCanonicalPlaylistUseCase` | Exists |
| Edit details | `PATCH /playlists/{id}` | `UpdateCanonicalPlaylistUseCase` | Exists |
| Delete | `DELETE /playlists/{id}` | `DeleteCanonicalPlaylistUseCase` | Exists |

**Edge cases**:
- Very large playlist (>1,000 tracks): Paginate the track table. Drag-and-drop reorder only works within visible page; full reorder uses a "Move to position" input.
- Concurrent edit: If another operation (workflow, sync) modifies the playlist while user is viewing, stale data is possible. Tanstack Query's stale-while-revalidate handles this, but destructive operations should re-fetch before applying.

---

### 3.3 Creating a Playlist

**Trigger**: User clicks **Create Playlist** on the playlist list page.

**Steps**:

1. A modal (or inline form) opens:
   - **Name** (required): text input
   - **Description** (optional): textarea
   - **Create** and **Cancel** buttons

2. User fills in details and clicks **Create**.
   - Frontend calls `POST /playlists` with `{ name, description }`.
   - On success: navigates to the new playlist's detail page (`/playlists/{new_id}`).
   - The playlist starts empty. An inline prompt suggests: "Add tracks to get started."

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `POST /playlists` | `CreateCanonicalPlaylistUseCase` | Exists |

**Edge cases**:
- Duplicate name: Backend allows it (playlists identified by ID, not name). No conflict.
- Empty name: Frontend validation prevents submission. Backend also validates.

---

### 3.4 Adding Tracks to a Playlist

**Trigger**: User clicks **Add Tracks** on a playlist detail page.

**Steps**:

1. A **search modal** opens (full-screen on mobile, large modal on desktop):
   - Search input at top, pre-focused.
   - Results shown as a track list with checkboxes for multi-select.

2. User searches for tracks by title/artist.
   - Frontend calls `GET /tracks?q=...&limit=20`.
   - Results appear with: checkbox, title, artist, album, duration.
   - Already-in-playlist tracks are visually indicated (greyed checkbox, "Already added" badge).

3. User checks multiple tracks and clicks **Add Selected** (count badge on button: "Add 5 Tracks").
   - Frontend calls `POST /playlists/{id}/tracks` with `{ track_ids: [1, 2, 3, 4, 5] }`.
   - Modal closes. Playlist detail refreshes. Toast: "5 tracks added."

4. Tracks are appended to the end of the playlist by default.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `GET /tracks?q=...` | `SearchTracksUseCase` | Needs implementation (v0.3.2) |
| 3 | `POST /playlists/{id}/tracks` | `UpdateCanonicalPlaylistUseCase` | Exists |

**Edge cases**:
- User tries to add a track that's already in the playlist: Backend allows duplicates (some playlists intentionally repeat tracks). UI shows "Already added" badge but doesn't prevent re-adding.
- Adding many tracks at once (>50): Batch the request. Show progress if needed.

---

### 3.5 Reordering Tracks

**Trigger**: User wants to change track order in a playlist.

**Steps**:

1. On the playlist detail page, user enables reorder mode (or directly drags).
   - **Drag-and-drop**: grab handle on left of each row. Drag to new position.
   - **Keyboard accessible**: select a track, use Up/Down arrow keys to move it, Enter to confirm.

2. After repositioning, the new order is submitted.
   - Frontend calls `PATCH /playlists/{id}/tracks/reorder` with `{ entry_ids: [...] }` (full ordered list).
   - Optimistic update: UI immediately reflects new order. Reverts on failure.

3. For moving a single track to a specific position:
   - `PATCH /playlists/{id}/tracks/move` with `{ entry_id, new_position }`.
   - More efficient for single-track moves in large playlists.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Full reorder | `PATCH /playlists/{id}/tracks/reorder` | `UpdateCanonicalPlaylistUseCase` | Exists |
| Single move | `PATCH /playlists/{id}/tracks/move` | `UpdateCanonicalPlaylistUseCase` | Needs implementation |

**Edge cases**:
- Reorder fails (network error): Revert to previous order. Toast: "Reorder failed. Your changes were not saved."
- Large playlist reorder: Only send the full entry_ids list, not track data. Keep payload small.

---

### 3.6 Removing Tracks

**Trigger**: User wants to remove tracks from a playlist.

**Steps**:

1. **Single track**: Click the X (remove) button on a track row.
   - Confirmation: "Remove 'Track Title' from 'Playlist Name'?" with **Remove** / **Cancel**.
   - Calls `DELETE /playlists/{id}/tracks/{entry_id}`.

2. **Batch removal**: Check multiple tracks via checkboxes, click **Remove Selected**.
   - Confirmation: "Remove 5 tracks from 'Playlist Name'?" with **Remove** / **Cancel**.
   - Calls `DELETE /playlists/{id}/tracks` with `{ entry_ids: [...] }` (batch endpoint).

3. On success: tracks disappear from list with animation. Toast: "3 tracks removed."

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Single remove | `DELETE /playlists/{id}/tracks/{entry_id}` | `UpdateCanonicalPlaylistUseCase` | Exists |
| Batch remove | `DELETE /playlists/{id}/tracks` (body: entry_ids) | `UpdateCanonicalPlaylistUseCase` | Needs implementation |

**Edge cases**:
- Track was already removed (by another operation): 404 on the entry. Toast: "Track was already removed." Refresh list.

---

## 4. Importing Data & Monitoring Sync

> **Available starting v0.3.1.** Requires `SSEProgressProvider` and import API routes. Import use cases already exist.

### 4.1 Import Center

**Trigger**: User clicks **Imports** in the sidebar.

**Steps**:

1. Import center page (`/imports`) shows:
   - **Available Operations** section:
     - Each card shows: operation name, description, connector icon, last run time, status badge
     - Cards:
       - "Import Spotify Liked Songs" -- `POST /imports/spotify/likes`
       - "Import Last.fm Listening History" -- `POST /imports/lastfm/history`
       - "Upload Spotify GDPR Export" -- `POST /imports/spotify/history`
       - "Export Likes to Last.fm" -- `POST /imports/lastfm/export-likes`

   - **Sync Checkpoints** section:
     - Table showing: Service, Entity Type, Last Sync Timestamp, Cursor/Position
     - Helps user understand how far each incremental import has progressed

   - **Recent Operations** section (activity feed):
     - Last 20 operations with: name, status, started_at, duration, summary
     - Click to expand for full result details

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| List checkpoints | `GET /imports/checkpoints` | Checkpoint query | Needs implementation |
| Recent operations | `GET /operations?limit=20` | Operation history query | Needs implementation |

---

### 4.2 Last.fm History Import with Live Progress

**Trigger**: User clicks "Import Last.fm Listening History" card.

**Steps**:

1. A configuration panel slides open (or modal):
   - **Date range** (optional): "From" date picker, "To" date picker
   - Defaults: "From" = last checkpoint date (shown), "To" = today
   - Helper text: "Incremental import -- only new plays since last import will be fetched."
   - **Start Import** button

2. User clicks **Start Import**.
   - Frontend calls `POST /imports/lastfm/history` with `{ from_date?, to_date? }`.
   - Returns `{ operation_id: "..." }`.

3. The card transitions to a **live progress view**:
   - Operation title: "Importing Last.fm listening history"
   - Progress bar: indeterminate initially, then determinate once page count is known
   - Stats: "Page 12 of ~47 | 2,340 plays fetched | 1,890 new, 450 duplicates"
   - Live message from SSE: "Fetching page 12... Resolving tracks..."
   - **Cancel** button

4. SSE stream (`GET /operations/{id}/progress`) delivers events:
   ```
   event: progress
   data: {"operation_id":"...","status":"RUNNING","current":2340,"total":9400,"message":"Fetching page 12 of ~47"}
   ```

5. On completion:
   - Summary card: "Import complete. 9,400 plays processed. 8,100 new plays imported. 1,300 duplicates skipped. 15 tracks could not be resolved."
   - Checkpoint updated (shown in Sync Checkpoints section).
   - **View Play History** button.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `POST /imports/lastfm/history` | `ImportPlayHistoryUseCase` | Exists |
| 4 | `GET /operations/{id}/progress` | SSE stream | Needs implementation |
| 5 | `GET /imports/checkpoints` | Checkpoint query | Needs implementation |

**Edge cases**:
- Last.fm API rate limited (429): Backend handles retries transparently. Progress message shows "Rate limited, waiting..." SSE events continue.
- Import takes very long (>10 min for large histories): Progress continues. Browser tab shows operation name in title. Notification on completion if tab is backgrounded.
- User cancels: graceful halt; already-imported plays are kept, checkpoint updated to last successful page (see Cross-Cutting Concerns: Operation Cancellation).
- Network interruption during import: Backend continues (import runs server-side). Frontend reconnects SSE. On reconnect, catches up via `Last-Event-ID`.

---

### 4.3 Uploading Spotify GDPR Export

**Trigger**: User clicks "Upload Spotify GDPR Export" card.

**Steps**:

1. Upload panel opens:
   - Instructions: "Upload the JSON files from your Spotify data export (Settings > Privacy > Download your data)."
   - **File drop zone**: "Drag and drop files here, or click to browse"
   - Accepts multiple `.json` files simultaneously
   - Shows file list with names and sizes after selection

2. User selects/drops files and clicks **Upload & Import**.
   - Frontend sends `POST /imports/spotify/history` as `multipart/form-data` with all files.
   - Returns `{ operation_id: "..." }`.

3. Progress view identical to Last.fm import (SSE stream).
   - Message: "Processing file 2 of 3: StreamingHistory_music_1.json"

4. On completion: summary with play counts, file-level breakdown.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `POST /imports/spotify/history` | `ImportPlayHistoryUseCase` (file variant) | Exists |

**Edge cases**:
- Wrong file format: Backend validates JSON structure. Returns `400` with "Invalid file format. Expected Spotify streaming history JSON."
- Very large files (>100MB total): Show upload progress bar before import progress begins.
- Partial file set: User uploads only some history files. Backend processes what's given. Duplicates handled on re-upload.

---

### 4.4 Export Likes to Last.fm

**Trigger**: User clicks "Export Likes to Last.fm" card.

**Steps**:

1. Confirmation panel:
   - "This will love tracks on Last.fm that are liked in Narada but not yet loved on Last.fm."
   - Shows count: "142 tracks to export" (pre-calculated via `GET /imports/lastfm/export-likes/preview`).
   - **Start Export** button.

2. User clicks **Start Export**.
   - Calls `POST /imports/lastfm/export-likes`.
   - Returns `{ operation_id }`. Progress view shows.

3. Progress: "Loving track 45 of 142 on Last.fm..."

4. Completion: "142 tracks loved on Last.fm. 3 failed (track not found on Last.fm)."

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 1 | `GET /imports/lastfm/export-likes/preview` | Preview count | Needs implementation |
| 2 | `POST /imports/lastfm/export-likes` | `SyncLikesUseCase` | Exists |

**Edge cases**:
- Last.fm rate limiting: Backend retries with exponential backoff. Progress shows "Rate limited, retrying..."
- Track not found on Last.fm: Logged in summary. Non-blocking -- other tracks continue.

---

### 4.5 Sync Checkpoint Visibility

**Trigger**: User wants to understand import state (part of Import Center, Flow 4.1).

**Steps**:

1. Sync Checkpoints table on the Import Center page shows:
   | Service | Type | Last Sync | Status |
   |---------|------|-----------|--------|
   | Last.fm | Play History | 2026-02-28 14:30 UTC | Up to date |
   | Spotify | Liked Songs | 2026-02-15 09:00 UTC | 14 days ago |
   | Spotify | Play History (GDPR) | 2026-01-01 00:00 UTC | File import |

2. Each row has a staleness indicator:
   - **Green** "Up to date": synced within configured freshness threshold
   - **Yellow** "X days ago": approaching staleness
   - **Red** "Stale": exceeds freshness threshold
   - Clicking a stale row navigates to the corresponding Import Center operation card to re-run.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load checkpoints | `GET /imports/checkpoints` | Checkpoint query | Needs implementation |

---

## 5. Managing Connector Links

> **Available starting v0.4.4.** Requires connector playlist linking use cases and playlist links API routes.

### 5.1 Viewing Linked External Playlists

**Trigger**: User clicks **Manage Links** on a playlist detail page, or navigates to `/playlists/{id}/links`.

**Steps**:

1. Links page shows:
   - **Linked Playlists** table:
     | Connector | External Playlist | Sync Direction | Last Synced | Actions |
     |-----------|-----------------|----------------|-------------|---------|
     | Spotify | "My Chill Mix" | Narada Master | 2 hours ago | Sync / Edit / Unlink |
     | Apple Music | "Chill Vibes" | Manual | Never | Push / Pull / Edit / Unlink |

   - **Link New** button

2. **Sync direction badges** are color-coded:
   - **Narada Master** (blue): Narada pushes changes to connector
   - **Connector Master** (green): Connector changes pulled into Narada
   - **Manual** (grey): User triggers push/pull explicitly

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load links | `GET /playlists/{id}/links` | Query connector playlist mappings | Needs implementation |

---

### 5.2 Linking to an External Playlist

**Trigger**: User clicks **Link New** on the playlist links page.

**Steps**:

1. A modal opens with connector selection:
   - "Link to an external playlist"
   - Connector tabs: Spotify / Apple Music (only connected connectors shown)

2. User selects a connector (e.g., Spotify).

3. **Browse/search picker** appears (improvement over current spec's raw ID input):
   - Two modes:
     - **Browse**: Shows user's Spotify playlists in a scrollable list. Calls `GET /connectors/spotify/playlists`.
     - **Search**: Search Spotify playlists by name. Calls `GET /connectors/spotify/playlists?q=...`.
   - Each result: playlist name, track count, owner, thumbnail

4. User selects a playlist and chooses **sync direction**:
   - Radio buttons: Narada Master / Connector Master / Manual
   - Helper text explains each option:
     - Narada Master: "Changes you make in Narada will be pushed to Spotify."
     - Connector Master: "Changes on Spotify will be pulled into Narada."
     - Manual: "You control when to push or pull. No automatic sync."

5. User clicks **Link**.
   - Frontend calls `POST /playlists/{id}/links` with `{ connector, connector_playlist_id, sync_direction }`.
   - Success: link appears in the table. Toast: "Linked to Spotify playlist 'My Chill Mix'."

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 3 | `GET /connectors/{connector}/playlists` | Browse user's playlists | Needs implementation |
| 3 | `GET /connectors/{connector}/playlists?q=...` | Search playlists | Needs implementation |
| 5 | `POST /playlists/{id}/links` | `CreateConnectorPlaylistUseCase` | Exists |

**Edge cases**:
- Spotify playlist already linked to another canonical playlist: Warning "This Spotify playlist is already linked to 'Other Playlist'. Link anyway?" (creates a second link).
- Connector not connected: Tab is disabled with "Connect Spotify first" message.
- User's Spotify has many playlists (>200): Paginate the browse list.

---

### 5.3 Sync Direction Configuration

**Trigger**: User clicks **Edit** on a connector link row.

**Steps**:

1. Edit modal shows current sync direction with radio buttons.
2. User changes direction and clicks **Save**.
   - Calls `PATCH /playlists/{id}/links/{link_id}` with `{ sync_direction }`.
3. Updated direction reflected in the table.

**Apple Music warning**: When user sets sync direction to **Narada Master** for an Apple Music link:
- Warning banner: "Apple Music doesn't support individual track reorder or removal. Narada Master mode will replace the entire playlist on each sync."
- User must acknowledge before saving.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Update direction | `PATCH /playlists/{id}/links/{link_id}` | `UpdateConnectorPlaylistUseCase` | Exists |

---

### 5.4 Manual Push/Pull

**Trigger**: User clicks **Push** or **Pull** on a Manual-direction link, or **Sync** on a Narada/Connector Master link.

**Steps**:

1. Confirmation dialog:
   - **Push**: "Push Narada playlist 'X' to Spotify? This will update the Spotify playlist to match."
   - **Pull**: "Pull Spotify playlist 'X' into Narada? This will update the Narada playlist to match Spotify."

2. User confirms.
   - Calls `POST /playlists/{id}/links/{link_id}/sync` with `{ direction: "push" | "pull" }`.
   - Returns `{ operation_id }`.

3. Progress view shows sync progress (SSE).
   - "Syncing... Calculating diff... Adding 5 tracks, removing 2, reordering 3..."

4. Completion summary:
   - "Sync complete. 5 tracks added, 2 removed, 3 reordered on Spotify."
   - Leverages existing `SummaryMetricCollection` for operation result display.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `POST /playlists/{id}/links/{link_id}/sync` | `UpdateConnectorPlaylistUseCase` | Exists |
| 3 | `GET /operations/{id}/progress` | SSE stream | Needs implementation |

**Edge cases**:
- Sync conflicts (tracks modified on both sides since last sync): Backend resolves based on sync direction. Narada Master = Narada wins. Connector Master = connector wins. Summary shows conflict count.
- Connector API failure mid-sync: Partial sync committed. Summary shows "3 of 5 tracks synced. 2 failed." Retry option.

---

## 6. Workflows

> **Available starting v0.4.0** (persistence + visualization), **v0.4.1** (execution + run history), **v0.4.2** (run-first UX + run output), **v0.4.3** (visual editor + preview). Requires workflow persistence (`workflows` table), CRUD use cases, workflow API routes, and React Flow integration.

### 6.1 Workflow List

**Trigger**: User clicks **Workflows** in the sidebar.

**Steps**:

1. Workflow list page shows all defined workflows (user-created and templates).
   - Each row: Name, Description (truncated), Task Count, Node Type badges (colored category dots), Last Run status badge, Last Run date, Track count output (if last run succeeded), Template badge, Actions

2. **Template rows** are visually distinct:
   - Template badge ("Template") shown next to name
   - "Use Template" action clones the template into a new editable user workflow
   - Templates cannot be edited or deleted directly

3. **Status badges** (from last run, v0.4.1+):
   - **Never Run** (grey)
   - **Running** (blue, animated pulse)
   - **Completed** (green) with "42 tracks" output count
   - **Failed** (red) with error preview tooltip

4. **Action buttons** per row:
   - **Run** (v0.4.1+): Execute the workflow (with confirmation dialog)
   - **Inline Run status** (v0.4.2+): Per-row `[▶]` Run button. During execution: row shows "Running..." with spinner replacing Run button. On completion: last-run column live-updates via query invalidation. Only one workflow runs at a time — other Run buttons disabled while executing. Each row uses its own `useWorkflowExecution(workflowId)` hook instance.
   - **View**: Navigate to workflow detail
   - **Edit** (v0.4.3+): Open visual editor
   - **Delete** (danger, with confirmation -- not available for templates)
   - **Use Template** (templates only): Clone into new user workflow

5. **Create Workflow** button in top-right.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load list | `GET /workflows?include_templates=true` | `ListWorkflowsUseCase` | ✅ Implemented (v0.4.0) |
| Clone template | `POST /workflows` (with template's definition) | `CreateWorkflowUseCase` | ✅ Implemented (v0.4.0) |

**Edge cases**:
- No workflows and no templates: "No workflows yet. Create your first workflow." [Create Workflow]
- No user workflows but templates exist: Templates shown with prominent "Use Template" CTA.

---

### 6.2 Running a Workflow

**Trigger**: User clicks **Run** on a workflow in the list, or **Run** on the workflow detail page.

**Steps**:

1. **Pre-flight validation** (before confirmation dialog):
   - Backend checks required connectors: "Does this workflow need Spotify? Is it connected?"
   - If prerequisites unmet: Error toast: "This workflow requires Spotify. Connect it in Settings." with link to `/settings`.
   - If prerequisites met: Confirmation dialog opens.

2. Confirmation dialog:
   - "Run 'Weekly Obsessions'?"
   - Shows last run summary for context (if exists): "Last run: Mar 1, 42 tracks output"
   - **Run** / **Cancel**

3. User clicks **Run**.
   - Calls `POST /workflows/{id}/run`.
   - Returns `{ operation_id, run_id }`.

4. **Per-node live status visualization** (the "live pipeline" experience):
   - **From detail page (v0.4.2+)**: PipelineStrip animates inline — no page navigation on Run.
     - Each node dot transitions through states: **Pending** (grey) → **Running** (blue pulse + track count annotation) → **Completed** (green checkmark) → **Failed** (red X)
     - Progress bar below strip shows overall completion percentage
     - Current step description: "Running: Filter by metric (120 → 24 tracks)"
   - **From list page (v0.4.2+)**: Row shows "Running..." state, Run button becomes spinner. On completion, last-run column live-updates.
   - **From WorkflowRunDetail (historical inspection)**: Full React Flow DAG with per-node execution overlay (existing behavior from v0.4.1).
     - Each node in the DAG animates through states: **Pending** (grey, dashed border) → **Running** (blue, pulsing border) → **Completed** (green, solid border, track count badge) → **Failed** (red, solid border, error icon)
     - Current running node has a glow effect (using the `--shadow-glow` design token)
   - ~~Edges animate on completion: flowing particle animation shows data flow direction~~ *(deferred to v0.4.5 polish)*

5. SSE stream (`GET /operations/{id}/progress`) delivers two event types:
   - `progress` events (existing): overall operation progress
   - `node_status` events (new): per-node status updates
   ```
   event: node_status
   data: {"node_id": "filter_step", "node_type": "filter.by_metric", "status": "RUNNING", "input_track_count": 120}

   event: node_status
   data: {"node_id": "filter_step", "node_type": "filter.by_metric", "status": "COMPLETED", "output_track_count": 42, "duration_ms": 1200}
   ```

6. On completion:
   - All nodes green (or red for failures).
   - **Result summary**: "Pipeline complete. 42 tracks output to 'Weekly Obsessions - 2026-03-01' on Spotify."
   - Link to the output playlist (if destination created/updated one).

7. **Inline completion (v0.4.2+)**:
   - **From detail page**: Last Run card updates with new run data. Summary card shows output track count + link to full run detail. Tanstack Query invalidation refreshes Recent Runs table.
   - **From list page**: Row's last-run column live-updates. Run button re-enables.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 1 | `POST /workflows/{id}/run` | Pre-flight + execution | ✅ Implemented (v0.4.1) |
| 4-5 | `GET /operations/{id}/progress` | SSE with `node_status` events | ✅ Implemented (v0.4.1) |

**Edge cases**:
- Workflow already running: Backend returns `409 Conflict`. Toast: "This workflow is already running."
- A node fails mid-pipeline: Pipeline halts at failed node. Completed nodes keep their results. Error message shown on the failed node. Overall status: Failed.
- Connector not connected: Pre-flight check catches this before execution starts (see step 1).

---

### 6.3 Workflow Detail (Run-First Layout — v0.4.2)

**Trigger**: User clicks **View** on a workflow, or navigates to `/workflows/{id}`.

**Steps**:

1. Workflow detail page shows a **run-first layout** (v0.4.2 restructure — full DAG moved to editor and run inspection):
   - **Header**: Name, description, template badge (if template), created/modified dates, `[Edit]` button → `/workflows/:id/edit` (v0.4.3), `[▶ Run]` button
   - **Pipeline Strip** (v0.4.2, replaces full DAG as the default view on this page — all DAG components are preserved for `WorkflowRunDetail` and the v0.4.3 editor):
     - Compact horizontal visualization — category-colored dots with human-readable labels connected by arrows, left-to-right
     - Labels derived from task config (e.g., `source.playlist` → playlist name, `filter.by_metric` → metric + threshold)
     - Branching workflows: show primary chain with `+N branches` indicator
     - Animates during inline execution: pending (grey) → running (blue pulse + track count) → completed (green checkmark) → failed (red X)
   - **Last Run Card** (v0.4.2):
     - Status badge, duration, track count, output playlist link
     - "⚠ Definition changed since this run" indicator when `run.definition_version < workflow.definition_version`
     - Standalone reusable component (designed for future scheduling UI integration)
   - **Recent Runs** table (v0.4.1):
     | Run | Started | Duration | Status | Output | Actions |
     |-----|---------|----------|--------|--------|---------|
     | #3 | Mar 1, 10:00 | 45s | Completed | 42 tracks | View |
     | #2 | Feb 22, 10:00 | 38s | Completed | 39 tracks | View |
     | #1 | Feb 15, 10:00 | 1m 12s | Failed | - | View |
     - "View" navigates to `WorkflowRunDetail` page (`/workflows/:id/runs/:runId`) which retains the full DAG

2. **Run Detail page** (`/workflows/:id/runs/:runId`) — the deep inspection view:
   - **Header**: Run # and workflow name, "Run Again" button (runs current definition, not snapshot), back link "← Hidden Gems" (workflow name)
   - "⚠ Definition changed since this run" indicator when `run.definition_version < workflow.definition_version`
   - **Full DAG** re-renders from `definition_snapshot` (not current definition) with per-node execution overlay
   - Per-node execution overlay: input/output track counts, execution time, status color
   - **Per-node inspection** (click a node): side panel shows:
     - Track count delta: "Filter removed 78 of 120 tracks" or "Source loaded 120 tracks"
     - Execution time: "3.4s"
     - Sample output tracks: first 10 track titles with artist names
     - Error details for failed nodes (error message, stack trace preview)
   - **Run output display** (v0.4.2):
     - **Output tracks table** below the DAG: rank, title, artist, relevant metric values (e.g., play_count column if sorted by play count)
     - **Per-node details**: expand a node in the execution details list to see removed/kept tracks with reasons. E.g., "Filter by metric: removed 78 of 120 tracks" → expand → table of removed tracks with metric values
     - **Destination node details**: shows playlist diff — "Added 5 tracks, Removed 3 tracks" → expand → lists with per-track reasons ("not in source playlists", "filtered by play_count < 5")
   - **Execution timeline**: ~~horizontal bar chart showing duration per node (Temporal-inspired)~~ *(deferred to v0.4.5 polish)*
     - ~~Color-coded bars matching node category colors~~
     - ~~Total duration annotation~~

3. **Action buttons**: Run (v0.4.1), Edit (v0.4.3), Delete
   - For templates: "Use Template" instead of Edit, no Delete

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load workflow | `GET /workflows/{id}` | `GetWorkflowUseCase` | ✅ Implemented (v0.4.0) |
| Load history | `GET /workflows/{id}/runs` | `GetWorkflowRunsUseCase` | ✅ Implemented (v0.4.1) |
| Load run detail | `GET /workflows/{id}/runs/{run_id}` | `GetWorkflowRunsUseCase` | ✅ Implemented (v0.4.1) |

---

### 6.4 Creating/Editing a Workflow (Visual Editor -- v0.4.3)

> **DAG reuse**: The editor canvas reuses the existing v0.4.0 React Flow infrastructure — same 7 custom node components, `BaseWorkflowNode`, ELKjs layout, and `WorkflowCanvas` — upgraded from read-only to interactive mode (drag, connect, delete, undo/redo). This is not a rebuild.

**Trigger**: User clicks **Create Workflow**, **Edit** on an existing workflow, or **Use Template** on a template.

**Steps**:

1. Editor page opens with **three-panel layout**:
   - **Left: Node Palette** -- draggable node types organized by category
   - **Center: React Flow Canvas** -- interactive graph editor
   - **Right: Node Configuration Panel** -- form-based config for selected node
   - **Top: Editor Toolbar** -- Save, Preview, Run, Undo, Redo, Auto-Layout, Zoom to Fit, Delete Selected

2. **Building a workflow** (typical flow):
   a. User enters **Name** and **Description** in the header fields.
   b. User browses the **Node Palette** by category (Source, Enricher, Filter, Sorter, Selector, Combiner, Destination).
   c. User **drags** a node type from the palette onto the canvas. A new node appears at the drop position.
   d. User **connects** nodes by dragging from one node's output handle to another node's input handle. Edges appear showing data flow.
   e. User **clicks** a node to select it. The **Configuration Panel** appears on the right with the node's config form.
   f. User fills in config values (e.g., `metric_name: "lastfm_play_count"`, `min_value: 8`). Changes apply immediately.
   g. Repeat steps b-f for each node in the pipeline.
   h. User clicks **Auto-Layout** to clean up node arrangement (ELKjs layered algorithm).

3. **Node Palette** details:
   - Accordion sections by category: Source (3), Enricher (3), Filter (9), Sorter (8), Selector (2), Combiner (4), Destination (2)
   - Search/filter bar at top of palette
   - Each entry: category-colored icon, type name (e.g., "Filter by Metric"), brief description
   - Drag from palette to canvas with ghost preview at cursor
   - Data sourced from `GET /workflows/nodes` endpoint

4. **Node Configuration Panel** details:
   - Header: category badge, type name, description
   - Dynamic form fields generated from node's config schema:
     - Required fields marked with indicator
     - Text inputs, number inputs, select dropdowns, boolean toggles, date pickers
     - Inline validation (e.g., "metric_name is required", "min_value must be a number")
   - Changes update the Zustand store immediately (reflected on canvas node labels)

5. **Edge validation** (automatic):
   - Cannot connect a node to itself (self-loop prevention)
   - Cannot create duplicate edges between the same pair of nodes
   - Source nodes have no input handles, destination nodes have no output handles
   - Combiners accept multiple input edges
   - Cycle detection prevents circular dependencies

6. **Undo/Redo** (full history):
   - Every action (add node, remove node, connect edge, move node, config change) is recorded
   - Ctrl+Z to undo, Ctrl+Shift+Z to redo
   - Toolbar shows undo/redo buttons with disabled state when stack is empty

7. **Saving**:
   - User clicks **Save** (or Ctrl+S).
   - Editor serializes React Flow state (nodes + edges + config) -> `WorkflowDef` JSON.
   - Calls `POST /workflows` (new) or `PATCH /workflows/{id}` (edit).
   - Backend validates the definition (valid node types, valid DAG, required config fields).
   - On validation error: toast with error details, problematic nodes highlighted red on canvas.
   - Unsaved changes indicator: dot on Save button, browser `beforeunload` warning on navigation.

8. **Preview/Dry-run** (v0.4.3):
   - User clicks **Preview** in the toolbar.
   - Calls `POST /workflows/{id}/preview` (saved) or `POST /workflows/preview` (unsaved).
   - Backend executes the workflow but skips destination writes.
   - Preview panel slides in showing:
     - Output track count and first 20 track titles
     - Per-node summary: track count at each stage through the pipeline
     - "Preview mode -- no playlists were created or modified" banner
   - SSE progress shown during preview execution.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load node types | `GET /workflows/nodes` | Node registry introspection | ✅ Implemented (v0.4.0) |
| Create | `POST /workflows` | `CreateWorkflowUseCase` | ✅ Implemented (v0.4.0) |
| Update | `PATCH /workflows/{id}` | `UpdateWorkflowUseCase` | ✅ Implemented (v0.4.0) |
| Validate | `POST /workflows/validate` | Validate definition | ✅ Implemented (v0.4.0) |
| Preview | `POST /workflows/{id}/preview` | Dry-run execution | Needs implementation (v0.4.3) |

**Edge cases**:
- Dropped node on invalid position (off canvas): Node snaps to nearest valid position.
- Edge to incompatible node type: Connection rejected silently (handle doesn't snap).
- Very large workflow (20+ nodes): Auto-layout keeps it organized. Minimap helps navigation.
- Workflow from template has nodes with service-specific config (e.g., Spotify playlist ID): User must update config values before saving. Validation catches unconfigured required fields.
- Browser refresh with unsaved changes: `beforeunload` warning prompts user to save or discard.

---

### 6.5 LLM-Assisted Workflow Creation (v0.8.0 Sketch)

> Forward-looking sketch for v0.8.0.

**Concept**:
- Chat interface: "Describe the playlist you want to create"
- User: "I want my top 20 most-played tracks from the last month that I've liked"
- LLM generates a workflow definition
- Preview shows the generated DAG and a dry-run result
- User can tweak in the visual editor or iterate via chat
- "Looks good, save it" -> persists the workflow

---

## 7. Dashboard & Data Quality

> **Dashboard available starting v0.3.3.** Requires stats aggregation use cases. Unmatched tracks review (7.2) requires `GetUnmappedTracksUseCase` from v0.6.0.

### 7.1 Dashboard Overview

**Trigger**: User navigates to `/` (home page / dashboard).

**Steps**:

1. Dashboard shows **stat cards** at the top:
   - Total Tracks (with breakdown by connector)
   - Total Plays (all time)
   - Liked Tracks (with per-service counts)
   - Playlists (canonical count)
   - Workflows (count, last run status)

2. **Connector Health** section:
   - Status per connector: Connected/Disconnected, Last API call status, Token expiry
   - Connectors needing attention (expired token, disconnected) link to Settings

3. **Data Freshness** alerts:
   - "Spotify liked songs last synced 14 days ago" (links to Import Center)
   - "Last.fm play history up to date"
   - Staleness thresholds configurable in settings

4. **Recent Activity** feed:
   - Last 10 operations with status and timestamp
   - Click to see details

**Backend calls**:
| Section | Endpoint | Use Case | Status |
|---------|----------|----------|--------|
| Stats | `GET /stats/dashboard` | `GetTrackStatsUseCase` | Needs implementation (v0.3.3) |
| Connector health | `GET /connectors` | Connector status | Needs implementation |
| Freshness | `GET /imports/checkpoints` | Checkpoint query | Needs implementation |
| Recent activity | `GET /operations?limit=10` | Operation history | Needs implementation |

---

### 7.2 Reviewing Unmatched Tracks

**Trigger**: Dashboard shows "47 tracks without Spotify mappings" or user navigates to a data quality view.

**Steps**:

1. **Unmatched tracks view** (could be a filter on the library, or a dedicated section):
   - Table: Track title, Artist, Source (where this track came from), Missing connectors
   - Sorted by: most recently imported first

2. For each unmatched track, user can:
   - **Search and Map**: Opens the mapping correction flow (Flow 2.3) for the relevant connector
   - **Dismiss**: Mark as "intentionally unmapped" (some tracks legitimately don't exist on all services)
   - **Bulk Resolve**: Select multiple tracks and trigger batch enrichment/matching

3. **Batch resolve** for unmatched tracks:
   - Select tracks → "Re-match Selected"
   - Calls enrichment/matching use case for the selected tracks
   - Progress view shows matching progress
   - Summary: "15 of 47 tracks matched. 32 remain unmatched."

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| List unmatched | `GET /tracks?unmapped_for=spotify&limit=50` | `GetUnmappedTracksUseCase` | Needs implementation (v0.6.0) |
| Batch re-match | `POST /tracks/rematch` | `MatchAndIdentifyTracksUseCase` | Exists |

**Edge cases**:
- Track genuinely doesn't exist on a service (regional restrictions, removed catalog): Dismissed tracks don't appear in future unmatched lists.
- Very many unmatched tracks (>1,000): Paginate. Suggest batch resolve rather than individual mapping.

---

## Cross-Cutting Concerns

### Operation Cancellation

All long-running operations (imports, workflow runs, syncs) support cancellation:

1. **Cancel button** visible during any active operation.
2. Calls `POST /operations/{id}/cancel`.
3. Backend sets operation status to `CANCELLED`.
4. SSE stream delivers final event with `status: "CANCELLED"`.
5. Progress view shows: "Operation cancelled. Partial results preserved."

Backend calls:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Cancel | `POST /operations/{id}/cancel` | Cancel operation | Needs implementation |

### Persistent Operation Awareness

Active operations are visible globally:
- **Sidebar indicator**: Small badge/dot on "Imports" nav item when operations are running.
- **Background operations toast**: When user navigates away from a progress view, a persistent toast shows "Import running... 45%" with a link to return.
- **Tab title**: "Narada (Importing...)" while an operation is active.

### Error Handling Patterns

All API errors follow a consistent format:
```json
{
  "error": {
    "code": "PLAYLIST_NOT_FOUND",
    "message": "Playlist with ID 42 not found",
    "details": { "playlist_id": 42 }
  }
}
```

HTTP status codes:
- `400` -- Invalid request (bad input, validation failure)
- `404` -- Resource not found
- `409` -- Conflict (operation already running, duplicate link)
- `422` -- Validation error (invalid workflow definition, bad JSON)
- `429` -- Rate limited (forwarded from connector APIs)
- `500` -- Internal server error
- `503` -- Service unavailable (connector not connected)

### Result Summaries

Import and sync operations return structured result summaries leveraging the existing `SummaryMetricCollection` system:
- Track counts: imported, skipped (duplicate), failed, total
- Timing: duration, API calls made
- Service-specific details: rate limit encounters, retry counts

These summaries are displayed consistently across all operation completion views.
