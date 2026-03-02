# User Flows

> Primary specification document. Every feature decision starts here.
> Technical details (API, components, architecture) flow from these journeys.

Each flow is organized by **user goal**, not by page. Every flow includes:
- **Trigger** -- what prompts the user to start this journey
- **Steps** -- what the user sees and does, step by step
- **Backend calls** -- which API endpoints and use cases are involved
- **Edge cases** -- what can go wrong and how we handle it

---

## 1. First-Time Setup

### 1.1 Connect Spotify

**Trigger**: User opens Narada for the first time (or navigates to Settings with no connectors linked).

**Steps**:

1. Dashboard shows an **onboarding card**: "Connect your music services to get started."
   - Two prominent buttons: **Connect Spotify**, **Connect Last.fm**
   - Below: muted text "You can also connect services later from Settings."

2. User clicks **Connect Spotify**.
   - Frontend calls `GET /connectors/spotify/auth-url`.
   - Browser redirects to Spotify's OAuth consent page.

3. User grants permission on Spotify.
   - Spotify redirects to `GET /auth/spotify/callback?code=...&state=...`.
   - Backend exchanges code for tokens, stores them.
   - Browser redirects to `/settings` with a success toast: "Spotify connected."

4. Settings page now shows Spotify as **Connected** with the account name.
   - The onboarding card on Dashboard updates: Spotify shows a checkmark. Last.fm still shows "Connect."

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `GET /connectors/spotify/auth-url` | Generate OAuth URL | Needs implementation |
| 3 | `GET /auth/spotify/callback` | Exchange code for tokens | Needs implementation |
| 4 | `GET /connectors` | List connector status | Needs implementation |

**Edge cases**:
- User denies OAuth consent: callback receives `error=access_denied`. Redirect to `/settings` with error toast: "Spotify connection cancelled."
- Token exchange fails (network error, expired code): Show error toast with retry link.
- User is already connected: `GET /connectors` shows connected state. "Connect Spotify" button becomes "Reconnect Spotify" (for re-auth).

---

### 1.2 Connect Last.fm

**Trigger**: User clicks **Connect Last.fm** from onboarding card or Settings.

**Steps**:

1. User clicks **Connect Last.fm**.
   - A modal opens: "Enter your Last.fm username" with a text input.
   - Below: "We'll authenticate using Last.fm's web auth flow."

2. User enters their username and clicks **Authenticate**.
   - Frontend calls the Last.fm auth URL endpoint.
   - Browser redirects to Last.fm's auth page.

3. User grants permission on Last.fm.
   - Last.fm redirects back with a session token.
   - Backend stores the session key.
   - Browser redirects to `/settings` with success toast: "Last.fm connected."

4. Settings shows Last.fm as **Connected** with the username.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `GET /connectors/lastfm/auth-url` | Generate Last.fm auth URL | Needs implementation |
| 3 | `GET /auth/lastfm/callback` | Store session key | Needs implementation |

**Edge cases**:
- Last.fm auth fails: redirect with error toast.
- Username doesn't match authenticated account: backend validates and warns.

---

### 1.3 First Data Import with Progress

**Trigger**: After connecting at least one service, the onboarding flow suggests importing data.

**Steps**:

1. After first connector is linked, the onboarding card transitions:
   "Spotify connected! Import your data to start building your library."
   - **Import Liked Songs** (prominent)
   - **Import Listening History** (secondary)
   - "Or explore the app first" (dismissive link)

2. User clicks **Import Liked Songs**.
   - Frontend calls `POST /imports/spotify/likes`.
   - Returns `{ operation_id: "..." }` immediately.
   - UI transitions to a **progress view** (inline, not a new page).

3. Progress view shows:
   - Operation name: "Importing Spotify liked songs"
   - Progress bar (determinate once total is known, indeterminate initially)
   - Current count: "347 / 1,204 tracks imported"
   - Live message updates from SSE stream

4. Frontend connects to `GET /operations/{operation_id}/progress` (SSE).
   - Events update the progress bar in real time.
   - On `COMPLETED`: progress view shows summary.
     - "1,204 tracks imported. 15 already existed. 3 failed."
     - **View Library** button.

5. User clicks **View Library** and sees their tracks populated.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `POST /imports/spotify/likes` | `SyncLikesUseCase` | Exists |
| 4 | `GET /operations/{id}/progress` | SSE progress stream | Needs implementation |
| 4 | `GET /operations/{id}` | Snapshot fallback | Needs implementation |

**Edge cases**:
- Import already running: backend returns `409 Conflict` with existing `operation_id`. Frontend shows the existing progress.
- SSE connection drops: frontend reconnects with `Last-Event-ID` header. If SSE unavailable, falls back to polling `GET /operations/{id}` every 2 seconds.
- Import fails midway: progress shows `FAILED` status with error message and count of successfully imported tracks. "1,024 of 1,204 imported. Retry?" button.
- User navigates away during import: import continues in background. A persistent **activity indicator** in the sidebar shows active operations. User can return to see progress.

---

### 1.4 Empty-State-to-Populated Transition

**Trigger**: Every page has an empty state that guides the user toward populating it.

**Empty states by page**:

| Page | Empty State Message | Action |
|------|-------------------|--------|
| Dashboard (`/`) | "Welcome to Narada. Connect a music service to get started." | Connect Spotify / Connect Last.fm |
| Library (`/library`) | "No tracks yet. Import your liked songs or listening history." | Import Liked Songs / Import History |
| Playlists (`/playlists`) | "No playlists yet. Create one or import from a connected service." | Create Playlist / Import from Spotify |
| Workflows (`/workflows`) | "No workflows yet. Workflows let you build smart playlists using your own rules." | Browse Templates / Create Workflow |
| Imports (`/imports`) | "No import history. Connect a service to start importing." | Go to Settings |

**Behavior**: Empty states disappear permanently once the page has data. They never re-appear even if all data is deleted (that's a different state -- "all items deleted" shows "No playlists. Create one?" without the onboarding framing).

---

## 2. Browsing the Library

### 2.1 Track List

**Trigger**: User clicks **Library** in the sidebar navigation.

**Steps**:

1. Library page loads with a **paginated track list**.
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
     - Spotify: popularity score

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
   - **Track list**: Ordered table with columns:
     - Position (#), Title, Artist(s), Album, Duration, Added At, Actions (remove button)
   - **Connector Links** summary: "Linked to Spotify: 'My Playlist'" with sync status badge

2. Track list supports:
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
- Very large playlist (>1,000 tracks): Paginate track list. Drag-and-drop reorder only works within visible page; full reorder uses a "Move to position" input.
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

2. **Contextual import entry points** also exist on other pages:
   - Library (empty state): "Import Liked Songs"
   - Track detail (no play history): "Import Listening History"
   - Dashboard (stale data indicator): "Re-sync"

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
- User cancels: `POST /operations/{id}/cancel`. Backend stops fetching new pages. Already-imported plays are kept. Checkpoint updated to last successful page.
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
   - Clicking a stale row offers a one-click "Re-sync" button.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load checkpoints | `GET /imports/checkpoints` | Checkpoint query | Needs implementation |

---

## 5. Managing Connector Links

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

### 6.1 Workflow List

**Trigger**: User clicks **Workflows** in the sidebar.

**Steps**:

1. Workflow list page shows all defined workflows.
   - Each row: Name, Description (truncated), Last Run status badge, Last Run date, Track count output (if last run succeeded), Actions

2. **Status badges**:
   - **Never Run** (grey)
   - **Running** (blue, animated)
   - **Completed** (green) with "42 tracks"
   - **Failed** (red) with error preview

3. **Action buttons** per row:
   - **Run**: Execute the workflow (with confirmation)
   - **View**: Navigate to workflow detail
   - **Edit** (v0.4.0: JSON editor, v0.7.0: visual editor)
   - **Delete** (danger, with confirmation)

4. **Create Workflow** button in top-right.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load list | `GET /workflows` | List workflows | Needs implementation |

---

### 6.2 Running a Workflow

**Trigger**: User clicks **Run** on a workflow in the list, or **Run** on the workflow detail page.

**Steps**:

1. Confirmation dialog:
   - "Run 'Weekly Obsessions'?"
   - Optional: Show last run summary for context
   - **Run** / **Cancel**

2. User clicks **Run**.
   - Calls `POST /workflows/{id}/run`.
   - Returns `{ operation_id }`.

3. **Per-stage progress visualization**:
   - The workflow detail page (or an expanded progress section) shows the DAG.
   - Each node in the DAG shows its status:
     - **Pending** (grey): not yet started
     - **Running** (blue pulse): currently executing
     - **Completed** (green): finished, shows track count
     - **Failed** (red): error occurred
   - Current stage highlighted. Edges animate to show data flow direction.
   - Progress bar below the DAG shows overall completion.
   - Message area: "Enriching tracks with Last.fm data... (45/120 tracks)"

4. On completion:
   - All nodes green (or red for failures).
   - **Result summary**: "Pipeline complete. 42 tracks output to 'Weekly Obsessions - 2026-03-01' on Spotify."
   - Link to the output playlist.

**Backend calls**:
| Step | Endpoint | Use Case | Status |
|------|----------|----------|--------|
| 2 | `POST /workflows/{id}/run` | Workflow execution | Needs implementation |
| 3 | `GET /operations/{id}/progress` | SSE with per-stage metadata | Needs implementation |

**Edge cases**:
- Workflow already running: Backend returns `409 Conflict`. Toast: "This workflow is already running."
- A node fails mid-pipeline: Pipeline halts at failed node. Completed nodes keep their results. Error message shown on the failed node. Overall status: Failed.
- Connector not connected (workflow requires Spotify but not linked): Pre-flight check before run. Error: "This workflow requires Spotify. Connect it in Settings."

---

### 6.3 Workflow Detail (React Flow Visualization)

**Trigger**: User clicks **View** on a workflow, or navigates to `/workflows/{id}`.

**Steps**:

1. Workflow detail page shows:
   - **Header**: Name, description, version, created/modified dates
   - **DAG Visualization** (React Flow):
     - Nodes arranged left-to-right showing the pipeline
     - Node types color-coded: source (blue), enricher (purple), filter (orange), sorter (yellow), selector (teal), combiner (pink), destination (green)
     - Each node shows: type label, key config (e.g., "limit: 20"), last run output count
     - Edges show data flow between nodes
     - Pan, zoom, and minimap for large workflows

   - **Execution History** table below:
     | Run | Started | Duration | Status | Output | Actions |
     |-----|---------|----------|--------|--------|---------|
     | #3 | Mar 1, 10:00 | 45s | Completed | 42 tracks | View Details |
     | #2 | Feb 22, 10:00 | 38s | Completed | 39 tracks | View Details |
     | #1 | Feb 15, 10:00 | 1m 12s | Failed | - | View Details |

2. **Run Details** expand to show per-node execution data:
   - Input/output track counts per node
   - Execution time per node
   - Error details for failed nodes
   - Summary metrics from `SummaryMetricCollection`

3. **Action buttons**: Run, Edit, Delete

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Load workflow | `GET /workflows/{id}` | Get workflow definition | Needs implementation |
| Load history | `GET /workflows/{id}/runs` | List runs | Needs implementation |
| Load run detail | `GET /workflows/{id}/runs/{run_id}` | Get run detail | Needs implementation |

---

### 6.4 Creating/Editing a Workflow (JSON Editor -- v0.4.0)

**Trigger**: User clicks **Create Workflow** or **Edit** on an existing workflow.

**Steps**:

1. Editor page shows:
   - **Name** and **Description** fields at top
   - **JSON Editor** (Monaco editor or CodeMirror):
     - Syntax highlighting for JSON
     - Inline validation against workflow schema
     - Auto-complete for node types (source.playlist, filter.by_metric, etc.)
   - **Node Reference** sidebar: collapsible list of all available node types with their config schemas
   - **Preview** button: dry-run the workflow to see what it would produce without writing to destination

2. User writes/edits the workflow JSON following the structure defined in [GUIDE_WORKFLOWS.md](../GUIDE_WORKFLOWS.md):
   ```json
   {
     "id": "my_workflow",
     "name": "My Custom Workflow",
     "tasks": [
       { "id": "src", "type": "source.playlist", "config": {...} },
       { "id": "filter", "type": "filter.by_metric", "config": {...}, "upstream": ["src"] }
     ]
   }
   ```

3. User clicks **Save**.
   - Calls `POST /workflows` (new) or `PATCH /workflows/{id}` (edit).
   - Backend validates the workflow definition (valid JSON, known node types, valid DAG structure, required config fields).
   - On validation error: inline error messages highlighting the problematic task.

4. **Preview/Dry-run**:
   - User clicks **Preview**.
   - Calls `POST /workflows/{id}/preview` (or `POST /workflows/preview` for unsaved).
   - Backend executes the workflow but skips destination writes.
   - Returns: preview of output tracklist (track names, count) and per-node execution summary.
   - Displayed in a side panel.

**Backend calls**:
| Action | Endpoint | Use Case | Status |
|--------|----------|----------|--------|
| Create | `POST /workflows` | Create workflow | Needs implementation |
| Update | `PATCH /workflows/{id}` | Update workflow | Needs implementation |
| Validate | `POST /workflows/validate` | Validate definition | Needs implementation |
| Preview | `POST /workflows/{id}/preview` | Dry-run execution | Needs implementation |

**Edge cases**:
- Invalid JSON: Editor highlights syntax errors before submission.
- Unknown node type: Validation returns "Unknown node type 'filter.foo'. Available: filter.by_metric, filter.by_release_date, ..."
- Circular dependency: Validation detects DAG cycle and returns error.
- Preview with external API calls: Enricher nodes still call external APIs during preview (to show realistic output). Only destination writes are skipped.

---

### 6.5 Visual Workflow Builder (v0.7.0 Sketch)

> This is a forward-looking sketch. v0.7.0 replaces the JSON editor with a visual builder.

**Concept**:
- Left sidebar: **Node Palette** with draggable node types organized by category
- Center canvas: React Flow editor where users drag nodes, connect edges, configure
- Right sidebar: **Node Configuration Panel** -- form-based config for the selected node
- Top toolbar: Save, Run, Preview, Undo/Redo
- Validation runs continuously as the user builds

---

### 6.6 LLM-Assisted Workflow Creation (v0.8.0 Sketch)

> Forward-looking sketch for v0.8.0.

**Concept**:
- Chat interface: "Describe the playlist you want to create"
- User: "I want my top 20 most-played tracks from the last month that I've liked"
- LLM generates a workflow definition
- Preview shows the generated DAG and a dry-run result
- User can tweak in the visual editor or iterate via chat
- "Looks good, save it" → persists the workflow

---

## 7. Dashboard & Data Quality

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
   - Quick actions: Reconnect, Re-sync

3. **Data Freshness** alerts:
   - "Spotify liked songs last synced 14 days ago" (with re-sync button)
   - "Last.fm play history up to date"
   - Staleness thresholds configurable in settings

4. **Recent Activity** feed:
   - Last 10 operations with status and timestamp
   - Click to see details

5. **Quick Actions** section:
   - "Import Liked Songs", "Import History", "Run Workflow"
   - Most relevant action highlighted based on data state

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
