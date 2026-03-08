# Information Architecture

> Skeletal structure derived from [01-user-flows.md](01-user-flows.md).
> Defines page hierarchy, URLs, navigation, and empty states.

---

## Primary Navigation (Sidebar)

```
Narada
├── Dashboard        /                    Stats, health, activity feed
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
| `/playlists` | Playlist List | Flow 3.1 | All canonical playlists. Create uses modal dialog, not a route. |
| `/playlists/:id` | Playlist Detail | Flow 3.2, 3.3, 3.4, 3.5, 3.6 | Track list, add/remove/reorder. Edit name/description uses inline dialog. |
| `/playlists/:id/links` | Connector Links | Flow 5.1, 5.2, 5.3, 5.4 | Link management, sync direction, push/pull |
| `/workflows` | Workflow List | Flow 6.1 | All workflows with last run status |
| `/workflows/new` | Create Workflow | Flow 6.4 | Visual drag-and-drop editor (v0.4.3) |
| `/workflows/:id` | Workflow Detail | Flow 6.2, 6.3 | Pipeline strip + last run card (v0.4.2), execution history (v0.4.1) |
| `/workflows/:id/edit` | Edit Workflow | Flow 6.4 | Visual drag-and-drop editor (v0.4.3) |
| `/workflows/:id/runs/:runId` | Run Detail | Flow 6.3 | Full DAG from definition_snapshot, run output, per-node details (v0.4.1, enhanced v0.4.2) |
| `/imports` | Import Center | Flow 4.1 | Available operations, checkpoints, activity feed |
| `/settings` | Settings | Flow 1.1, 1.2 | Connector auth, reconnect, preferences |

> **Implementation note**: Playlist creation and editing use modal dialogs on the list/detail page rather than dedicated routes (`/playlists/new`, `/playlists/:id/edit`). This keeps the user in context and avoids unnecessary navigation for lightweight CRUD forms.

---

## Empty States

Every page has an empty state. Text guides users to the canonical location for the relevant action — no duplicate action buttons.

| Page | Empty State |
|------|------------|
| Dashboard `/` | "Connect services in Settings to get started." |
| Library `/library` | "No tracks yet. Import data from the Import Center." |
| Playlists `/playlists` | "No playlists yet. Create your first playlist to start curating your music collection." [New Playlist] |
| Workflows `/workflows` | "No workflows yet. Create your first workflow or start from a template." [Create Workflow] [Browse Templates] |
| Imports `/imports` | "No import history. Connect a service in Settings first." Available operations cards still shown. |
| Playlist Detail (no tracks) | "This playlist is empty. Add tracks by linking a connector playlist or using workflows." |
| Track Detail (no mappings) | "Not mapped to any services. This track exists only in your Narada library." |
| Track Detail (no plays) | "No play data. Import listening history from the Import Center." |

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
| 1. Connecting Services | Settings |
| 2. Browsing the Library | Library, Track Detail |
| 3. Managing Playlists | Playlists, Playlist Detail, Create Playlist |
| 4. Importing Data | Imports |
| 5. Managing Connector Links | Playlist Detail, Links sub-page |
| 6. Workflows | Workflows, Workflow Detail, Create/Edit Workflow |
| 7. Dashboard & Data Quality | Dashboard, Library (filtered for unmatched) |

---

## Global UI Elements

- **Sidebar indicator**: Badge on "Imports" when operations are running
- **Active operation toast**: Persistent toast for background operations with progress % and link
- **Tab title**: Dynamic -- "Narada (Importing...)" during active operations, otherwise "Narada - Page Name"
- **Breadcrumbs**: Shown on detail pages (e.g., Playlists > My Playlist > Links)
