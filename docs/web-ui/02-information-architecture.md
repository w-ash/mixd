# Information Architecture

> Skeletal structure derived from [01-user-flows.md](01-user-flows.md).
> Defines page hierarchy, URLs, navigation, and empty states.

---

## Primary Navigation (Sidebar)

```
Narada
├── Dashboard        /                    Stats, health, quick actions
├── Library          /library             Track browsing & search
├── Playlists        /playlists           List, detail, edit, links
├── Workflows        /workflows           List, run, edit, visualize
├── Imports          /imports             Trigger & monitor data operations
└── Settings         /settings            Connector auth, preferences
```

Dashboard and Stats are merged into a single page at `/`. The dashboard IS the stats overview.

---

## Complete Route Map

| Route | Page | Primary Flow Reference | Notes |
|-------|------|----------------------|-------|
| `/` | Dashboard | Flow 7.1 | Stats, connector health, freshness alerts, recent activity |
| `/library` | Track List | Flow 2.1 | Paginated, searchable, filterable |
| `/library/:id` | Track Detail | Flow 2.2, 2.3 | Metadata, mappings, likes, play history, actions |
| `/playlists` | Playlist List | Flow 3.1 | All canonical playlists |
| `/playlists/new` | Create Playlist | Flow 3.3 | Modal or dedicated page |
| `/playlists/:id` | Playlist Detail | Flow 3.2, 3.4, 3.5, 3.6 | Track list, add/remove/reorder |
| `/playlists/:id/edit` | Edit Playlist | Flow 3.2 | Inline or modal for name/description |
| `/playlists/:id/links` | Connector Links | Flow 5.1, 5.2, 5.3, 5.4 | Link management, sync direction, push/pull |
| `/workflows` | Workflow List | Flow 6.1 | All workflows with last run status |
| `/workflows/new` | Create Workflow | Flow 6.4 | JSON editor (v0.4.0), visual builder (v0.7.0) |
| `/workflows/:id` | Workflow Detail | Flow 6.3 | React Flow DAG, execution history |
| `/workflows/:id/edit` | Edit Workflow | Flow 6.4 | JSON editor (v0.4.0), visual builder (v0.7.0) |
| `/imports` | Import Center | Flow 4.1 | Available operations, checkpoints, activity feed |
| `/settings` | Settings | Flow 1.1, 1.2 | Connector auth, reconnect, preferences |

---

## Empty States

Every page has an empty state for first-time users. These differ from "all deleted" states.

| Page | First-Time Empty State | All-Deleted Empty State |
|------|----------------------|------------------------|
| Dashboard `/` | "Welcome to Narada. Connect a music service to get started." [Connect Spotify] [Connect Last.fm] | N/A (dashboard always shows something) |
| Library `/library` | "No tracks yet. Import your liked songs or listening history." [Import Liked Songs] [Import History] | "No tracks. Import data to populate your library." [Import] |
| Playlists `/playlists` | "No playlists yet. Create one or import from a connected service." [Create Playlist] | "No playlists. Create one?" [Create Playlist] |
| Workflows `/workflows` | "No workflows yet. Workflows let you build smart playlists using your own rules." [Browse Templates] [Create Workflow] | "No workflows. Create one?" [Create Workflow] |
| Imports `/imports` | "No import history. Connect a service to start importing." [Go to Settings] | Shows available operations even with no history |
| Playlist Detail (no tracks) | "This playlist is empty. Add tracks to get started." [Add Tracks] | Same |
| Track Detail (no mappings) | "Not mapped to any services. This track exists only in your Narada library." | Same |
| Track Detail (no plays) | "No play data. Import your listening history to see plays." [Import History] | Same |

---

## Contextual Import Entry Points

Import actions appear not just on `/imports` but also in context:

| Location | Condition | Action Shown |
|----------|-----------|-------------|
| Dashboard | Spotify connected, no liked songs imported | "Import Liked Songs" card |
| Dashboard | Stale checkpoint (>7 days) | "Re-sync" badge with one-click action |
| Library (empty) | No tracks | "Import Liked Songs" / "Import History" |
| Track Detail | No play history for track | "Import Listening History" link |
| Playlist Links | No linked playlists | "Link to External Playlist" prompt |

---

## Responsive Behavior

| Breakpoint | Layout |
|-----------|--------|
| Desktop (>=1024px) | Sidebar navigation + main content area. Full table views. |
| Tablet (768-1023px) | Collapsible sidebar (hamburger). Tables remain but with fewer visible columns. |
| Mobile (<768px) | Bottom navigation bar (5 items). Card-based layouts replace tables. Modals become full-screen sheets. |

Touch targets: minimum 44x44px per WCAG 2.2 AA.

---

## Page-to-Flow Cross-Reference

| Flow | Pages Involved |
|------|---------------|
| 1. First-Time Setup | Dashboard, Settings |
| 2. Browsing the Library | Library, Track Detail |
| 3. Managing Playlists | Playlists, Playlist Detail, Create Playlist |
| 4. Importing Data | Imports, Dashboard (contextual) |
| 5. Managing Connector Links | Playlist Detail, Links sub-page |
| 6. Workflows | Workflows, Workflow Detail, Create/Edit Workflow |
| 7. Dashboard & Data Quality | Dashboard, Library (filtered for unmatched) |

---

## Global UI Elements

- **Sidebar indicator**: Badge on "Imports" when operations are running
- **Active operation toast**: Persistent toast for background operations with progress % and link
- **Tab title**: Dynamic -- "Narada (Importing...)" during active operations, otherwise "Narada - Page Name"
- **Breadcrumbs**: Shown on detail pages (e.g., Playlists > My Playlist > Links)
