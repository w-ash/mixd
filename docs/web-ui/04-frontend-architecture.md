# Frontend Architecture

> Architecture decisions and project structure for the React web UI.
> Stack choices, component catalog, and project layout reflect v0.4.2 implementation.
> Future components and hooks are noted where planned.

---

## Tech Stack

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Build | **Vite 7+** | esbuild transpilation, fast HMR, optimized production builds |
| Framework | **React 19+** | Ecosystem, component model, Tanstack Query integration |
| Language | **TypeScript 5.9+** (strict mode + `erasableSyntaxOnly`) | Type safety across API boundary |
| Styling | **Tailwind CSS v4** | Rust engine (10x perf), CSS-first `@theme` tokens, dark mode via CSS vars |
| Routing | **React Router** | Standard, file-system-like route structure |
| Server state | **Tanstack Query** | Stale-while-revalidate, background refetch, optimistic updates |
| Components | **shadcn/ui** (owned source) | Accessible Radix primitives + Tailwind styling. Source code copied into project, not a runtime dependency |
| Animation | **Motion** (React) | CSS-first for simple transitions; Motion for orchestrated sequences |
| Workflow viz | **React Flow (xyflow) v12+** | DAG rendering with pan/zoom, custom nodes, interactive editing |
| DAG layout | **ELKjs** | Layered auto-layout for workflow DAGs (superior to Dagre) |
| Workflow state | **Zustand** | React Flow canvas state (nodes, edges, viewport, undo/redo) |
| Error boundaries | **react-error-boundary** | `resetKeys` for auto-reset on navigation; lighter than hand-written class component |
| Testing | **Vitest** (unit/integration) + **Playwright** (E2E) | Native ESM, Jest-compatible API |
| Package mgr | **pnpm** | Faster installs, efficient disk usage |
| Linting + Format | **Biome 2.x** | Rust-based, single tool for lint + format (Ruff equivalent for JS/TS) |

**State management policy**: Tanstack Query handles all server state. Local UI state (modals, forms) lives in React component state. **Exception**: Zustand is used for React Flow canvas state (nodes, edges, viewport, undo/redo history) — this is React Flow's officially recommended state management pattern via `applyNodeChanges`/`applyEdgeChanges` helpers. Zustand is scoped exclusively to the workflow editor; it does not replace Tanstack Query for server state.

---

## Shared Backend Architecture

> CLI and Web UI are two **presentation layers** over a shared application core. Neither contains business logic.

Both interfaces call `execute_use_case()` from `src/application/runner.py` — the single entry point for all use case execution. They transform user input into frozen Command objects, call the runner, and render the returned Result objects.

### What Each Interface Owns

| Concern | CLI (`src/interface/cli/`) | Web (`src/interface/api/`) |
|---------|---------------------------|---------------------------|
| Input parsing | Typer args/options | FastAPI request body/params |
| Auth | N/A (local user) | Session/token (v0.3.0: none) |
| Progress display | `RichProgressProvider` | `SSEProgressProvider` |
| Output rendering | Rich tables/panels | JSON responses |
| Async bridge | `run_async()` (sync→async) | Native async (no bridge needed) |

### What Is Shared (Never Duplicated)

- `application/runner.py` → `execute_use_case()` — session/UoW lifecycle
- `application/use_cases/*` — all business logic
- `application/services/*` — orchestration services
- `domain/*` — pure business rules, entities, transforms
- `infrastructure/*` — repositories, connectors, persistence
- `domain/entities/progress.py` → `ProgressEmitter` / `ProgressSubscriber` protocols

### SSE Progress Provider (v0.3.1)

- `OperationBoundEmitter` implements `ProgressEmitter` protocol (same interface the CLI's `RichProgressProvider` uses)
- `OperationRegistry` manages per-operation SSE queues with `asyncio.Queue` for event fan-out
- Background task lifecycle: `_launch_background()` accepts a coroutine *factory* (not a pre-created coroutine) to prevent leaked unawaited coroutine warnings in tests
- `_active_operations` set tracks logically running imports separately from `_background_tasks` — the 429 concurrency limit checks active operations, not draining tasks
- Frontend connects via `useOperationProgress` hook, receives typed events (`started`, `progress`, `complete`, `error`)

---

## Visual Identity

> Dark editorial music — a dim-lit record shop meets a design magazine. The user is a curator: someone who discovers, organizes, and shares music with taste.

### Aesthetic Direction: Dark Editorial — for DJs and Tastemakers

- **Dark-mode primary** — the default experience, light mode is the alternative
- Magazine-inspired layouts with generous whitespace, but in a low-light register
- Album art as a first-class visual element — glows against dark surfaces
- Data presented with editorial confidence — bold headings, clear hierarchy
- Feels like a tool built by someone who cares about music, not a generic dashboard

### Typography

Distinctive, not generic. Never use Inter, Roboto, Open Sans, or system fonts for display.

| Role | Font | Usage |
|------|------|-------|
| Display/headings | **Space Grotesk** | Geometric, warm, distinctive. Weight contrast: 200 vs 700+. Size jumps 3x+ between hierarchy levels. |
| Body/reading | **Newsreader** | Editorial serif for longer text, track descriptions, metadata. Brings warmth sans-serif alone can't. |
| Mono/technical | **JetBrains Mono** | Workflow JSON, ISRCs, technical data |

Load from Google Fonts. Never fall back to system fonts for display text.

### Color Strategy

Dark surfaces first — warm-tinted, not cold gray. Think vinyl sleeve, not spreadsheet.

| Token | Value | Purpose |
|-------|-------|---------|
| `--color-surface` | `oklch(0.13 0.01 60)` | Warm near-black (default background) |
| `--color-surface-elevated` | `oklch(0.18 0.01 60)` | Cards, modals, popovers |
| `--color-surface-sunken` | `oklch(0.10 0.01 60)` | Inset areas, code blocks |
| `--color-border` | `oklch(0.25 0.01 60)` | Subtle warm dividers |
| `--color-text` | `oklch(0.93 0.005 80)` | Primary text (warm white) |
| `--color-text-muted` | `oklch(0.60 0.01 60)` | Secondary text, labels |
| `--color-text-faint` | `oklch(0.55 0.01 60)` | Tertiary text, descriptions (WCAG 4.1:1 on bg) |
| `--color-primary` | `oklch(0.75 0.15 85)` | **Warm gold** — pops against dark, evokes vinyl warmth |
| `--color-secondary` | `oklch(0.70 0.12 25)` | **Soft coral** — active states, emphasis |
| `--color-destructive` | `oklch(0.60 0.20 25)` | Destructive actions |
| `--color-success` | `oklch(0.72 0.17 155)` | Completion, connected status |

Status badge colors (semantic, not connector-specific):

| Token | Value | Usage |
|-------|-------|-------|
| `--color-status-connected` | `oklch(0.72 0.17 155)` | Authenticated connector |
| `--color-status-expired` | `oklch(0.70 0.15 70)` | Token needs refresh |
| `--color-status-available` | `oklch(0.65 0.12 230)` | Public API (MusicBrainz) |

Connector identity colors (used sparingly for service identification only):

| Connector | Token | Value |
|-----------|-------|-------|
| Spotify | `--color-spotify` | `oklch(0.72 0.22 155)` |
| Last.fm | `--color-lastfm` | `oklch(0.55 0.22 25)` |
| Apple Music | `--color-apple` | `oklch(0.65 0.25 350)` |

Album art is a color source — let it breathe and glow against the dark canvas. Light mode alternative uses warm cream `oklch(0.97 0.005 80)`, not pure white.

**Principle**: Dominant accent + restrained palette. Sharp gold on dark > timid pastels everywhere.

### Motion & Animation

CSS-first. Use Motion library (React) only for complex orchestrations.

| Trigger | Treatment | Duration |
|---------|-----------|----------|
| Page load | Staggered reveal with `animation-delay` — content fades up in sequence | 300ms per element |
| Hover (cards) | Subtle scale (1.02), warm glow transition | 150ms |
| Interactive states | Color transitions on buttons, links | 150ms |
| Layout shifts | Content area changes, sidebar collapse | 300ms |
| Progress bars | Smooth animation, not jumpy steps | continuous |

One well-orchestrated page load creates more delight than scattered micro-interactions.

### Backgrounds & Depth

- **Background grain**: SVG feTurbulence noise overlay on `body::after` at `opacity: 0.035`, `mix-blend-mode: overlay`. Breaks flat digital surfaces.
- Dark gradient meshes on dashboard/hero areas — subtle warmth, not flat black
- Album art with gentle blur for contextual backgrounds on detail pages (dark overlay for readability)

**Container hierarchy** (implemented):

| Level | Usage | Visual Treatment |
|-------|-------|------------------|
| **Inset** | Sections within cards, code blocks, metadata grids | `bg-surface-sunken` + no border + left-accent `border-l-2 border-primary/30` |
| **Flat** | Default content areas, table containers | `bg-surface` + `border-border-muted` (lighter border) |
| **Elevated** | Primary cards (ConnectorCard, OperationCard, modals) | `bg-surface-elevated` + `shadow-elevated` + warm glow on hover (`shadow-glow`, `border-primary/20`) |

**Shadow tokens** (`theme.css`):
- `--shadow-elevated`: `0 2px 12px oklch(0.08 0.01 60 / 0.5)` — warm ambient
- `--shadow-glow`: `0 0 20px oklch(0.75 0.15 85 / 0.06)` — gold hover glow

**Borders**: Asymmetric — prefer left-accent bars (`border-l-2 border-primary/40`) and bottom rules over full border boxes. PageHeader has `border-b border-border-muted`. Section headers use left accent for scannability.

---

## Project Structure

```
web/
├── public/                          Static assets
├── src/
│   ├── api/
│   │   ├── client.ts                Custom fetch mutator (hand-written, see Codegen section)
│   │   ├── client.test.ts           Client unit tests
│   │   ├── query-client.ts          createQueryClient() factory (retry policy, stale time)
│   │   ├── sse-client.ts            SSE transport adapter (fetch + eventsource-parser)
│   │   └── generated/               Orval output — tag-split (do not edit)
│   │       ├── playlists/
│   │       │   ├── playlists.ts     Tanstack Query hooks for playlist endpoints
│   │       │   └── playlists.msw.ts MSW mock handlers + faker response factories
│   │       ├── imports/
│   │       │   ├── imports.ts       Tanstack Query hooks for import endpoints
│   │       │   └── imports.msw.ts
│   │       ├── operations/
│   │       │   ├── operations.ts    Tanstack Query hooks for operations endpoints
│   │       │   └── operations.msw.ts
│   │       ├── connectors/
│   │       │   ├── connectors.ts
│   │       │   └── connectors.msw.ts
│   │       ├── health/
│   │       │   ├── health.ts
│   │       │   └── health.msw.ts
│   │       └── model/               Per-type TypeScript interfaces (one file each)
│   │           ├── index.ts         Barrel re-export
│   │           ├── playlistSummarySchema.ts
│   │           ├── operationStartedResponse.ts
│   │           ├── checkpointStatusSchema.ts
│   │           ├── trackSummarySchema.ts
│   │           └── ...              (generated per OpenAPI schema)
│   ├── components/
│   │   ├── ui/                      shadcn/ui primitives (owned, vendored source)
│   │   │   ├── badge.tsx
│   │   │   ├── button.tsx
│   │   │   ├── card.tsx
│   │   │   ├── dialog.tsx
│   │   │   ├── input.tsx
│   │   │   ├── skeleton.tsx
│   │   │   ├── sonner.tsx           Toast notifications (dark-only Sonner wrapper)
│   │   │   └── table.tsx
│   │   ├── layout/                  App shell components
│   │   │   ├── PageErrorFallback.tsx  Error boundary fallback (matches EmptyState styling)
│   │   │   ├── PageErrorFallback.test.tsx
│   │   │   ├── PageHeader.tsx       Title + description + action slot
│   │   │   ├── PageLayout.tsx       Sidebar + ErrorBoundary-wrapped Outlet
│   │   │   └── Sidebar.tsx          Nav links with active state
│   │   └── shared/                  Reusable composite components
│   │       ├── RunStatusBadge.tsx   Workflow run status badge (PENDING/RUNNING/COMPLETED/FAILED)
│   │       ├── ConnectorCard.tsx    Settings page connector status card
│   │       ├── ConnectorCard.test.tsx
│   │       ├── ConnectorIcon.tsx    Colored dot + label per service
│   │       ├── ConnectorIcon.test.tsx
│   │       ├── CreatePlaylistModal.tsx  Dialog with form + mutation
│   │       ├── CreatePlaylistModal.test.tsx
│   │       ├── EmptyState.tsx       Icon + heading + description + action slot
│   │       ├── EmptyState.test.tsx
│   │       ├── FileUpload.tsx       Drag-and-drop file input (Spotify GDPR upload)
│   │       ├── FileUpload.test.tsx
│   │       ├── OperationProgress.tsx  Progress bar + metrics for running operations
│   │       ├── OperationProgress.test.tsx
│   │       ├── PipelineStrip.tsx   Compact horizontal workflow visualization (v0.4.2)
│   │       ├── PipelineStrip.test.tsx
│   │       ├── LastRunCard.tsx     Last run summary with version indicator (v0.4.2)
│   │       ├── LastRunCard.test.tsx
│   │       ├── TablePagination.tsx  Page controls for paginated list views
│   │       └── TablePagination.test.tsx
│   │   └── workflow/                Workflow DAG components (v0.4.0+, used in 3 contexts below)
│   │       ├── nodes/              Custom React Flow node components per category
│   │       │   ├── SourceNode.tsx
│   │       │   ├── EnricherNode.tsx
│   │       │   ├── FilterNode.tsx
│   │       │   ├── SorterNode.tsx
│   │       │   ├── SelectorNode.tsx
│   │       │   ├── CombinerNode.tsx
│   │       │   └── DestinationNode.tsx
│   │       ├── BaseWorkflowNode.tsx Shared node shell: category color, icon, label, config summary, execution status (v0.4.1)
│   │       ├── WorkflowCanvas.tsx   React Flow wrapper with viewer/editor modes
│   │       ├── NodePalette.tsx      Draggable node type sidebar (v0.4.3)
│   │       ├── NodeConfigPanel.tsx  Dynamic config form for selected node (v0.4.3)
│   │       ├── EditorToolbar.tsx    Save, preview, run, undo/redo actions (v0.4.3)
│   │       └── ExecutionOverlay.tsx Per-node status overlay during execution (v0.4.1)
│   │       # Component reuse: nodes/, BaseWorkflowNode, WorkflowCanvas, ExecutionOverlay
│   │       # serve three contexts — NOT rebuilt per milestone:
│   │       #   1. WorkflowRunDetail (v0.4.1+): read-only DAG from definition_snapshot + execution overlay
│   │       #   2. WorkflowDetail (v0.4.0→v0.4.1): read-only DAG (replaced by PipelineStrip in v0.4.2,
│   │       #      but components retained for contexts 1 and 3)
│   │       #   3. WorkflowEditor (v0.4.3): interactive mode — same components with drag/connect/delete
│   ├── stores/
│   │   ├── useWorkflowStore.ts     Zustand store for React Flow canvas state
│   │   └── editor-store.ts        Zustand store for workflow editor state (v0.4.3)
│   ├── hooks/
│   │   ├── usePagination.ts         URL-state pagination (page param ↔ offset/limit)
│   │   ├── usePagination.test.tsx
│   │   ├── useSSEConnection.ts      Shared SSE lifecycle hook (transport layer)
│   │   ├── useSSEConnection.test.ts
│   │   ├── useNodeStatuses.ts       Shared node status Map with snake→camel mapping
│   │   ├── useNodeStatuses.test.ts
│   │   ├── useOperationProgress.ts  SSE subscription for real-time operation progress
│   │   ├── useOperationProgress.test.ts
│   │   ├── useWorkflowExecution.ts  Workflow execution lifecycle (run trigger, SSE node status, state)
│   │   ├── useWorkflowExecution.test.ts
│   │   ├── useWorkflowPreview.ts    Workflow preview/dry-run lifecycle (v0.4.3)
│   │   └── useWorkflowPreview.test.ts
│   ├── lib/
│   │   ├── utils.ts                 shadcn cn() utility
│   │   ├── sse-types.ts             Shared SSE types (NodeStatus, NodeExecutionStatus)
│   │   ├── workflow-config.ts       Workflow node config metadata
│   │   ├── workflow-layout.ts       ELKjs auto-layout utilities
│   │   ├── workflow-diff.ts         Workflow definition diffing utilities
│   │   └── workflow-diff.test.ts
│   ├── pages/                       Route-level page components
│   │   ├── Dashboard.tsx            Landing page (stats overview)
│   │   ├── Dashboard.test.tsx
│   │   ├── Imports.tsx              Import operations with real-time progress
│   │   ├── Imports.test.tsx
│   │   ├── Library.tsx              Track browsing, search, pagination (v0.3.2)
│   │   ├── Library.test.tsx
│   │   ├── TrackDetail.tsx          Track metadata, mappings, likes, play history (v0.3.2)
│   │   ├── TrackDetail.test.tsx
│   │   ├── Playlists.tsx            List view with table + pagination
│   │   ├── Playlists.test.tsx
│   │   ├── PlaylistDetail.tsx       Track table + edit/delete dialogs
│   │   ├── PlaylistDetail.test.tsx
│   │   ├── Settings.tsx             Connector cards grid
│   │   ├── Settings.test.tsx
│   │   ├── Workflows.tsx            Workflow list with per-row run buttons (v0.4.0, v0.4.2)
│   │   ├── Workflows.test.tsx
│   │   ├── WorkflowDetail.tsx       Run-first layout: pipeline strip + last run card (v0.4.2)
│   │   ├── WorkflowDetail.test.tsx
│   │   ├── WorkflowRunDetail.tsx    Historical run view with DAG from definition_snapshot (v0.4.1)
│   │   └── WorkflowRunDetail.test.tsx
│   ├── test/                        Test infrastructure
│   │   ├── setup.ts                 MSW server bootstrap + jest-dom matchers
│   │   └── test-utils.tsx           renderWithProviders + re-exports
│   ├── App.tsx                      Router + Toaster (React.lazy page splitting, route definitions)
│   ├── main.tsx                     Entry point (renders App with providers)
│   └── theme.css                    Tailwind v4 @theme tokens
├── index.html
├── biome.json                       Biome 2.x lint + format config
├── components.json                  shadcn/ui CLI config
├── openapi.json                     OpenAPI spec (input for Orval codegen)
├── orval.config.ts                  Orval codegen configuration
├── vite.config.ts
├── vitest.config.ts                 Vitest test runner config
├── tsconfig.json
└── package.json
```

> **Test coverage**: 28 test files, ~213 tests. Every page and shared component has a co-located `.test.tsx`/`.test.ts` file. Run with `pnpm --prefix web test`.
>
> **Future pages** (not yet implemented): `WorkflowEditor.tsx` (v0.4.3), `PlaylistLinks.tsx` (v0.4.4). All other pages (Dashboard, Library, TrackDetail, Playlists, Workflows, WorkflowDetail, WorkflowRunDetail, Imports, Settings) are implemented.
>
> **Future shared components**: `TrackRow.tsx`, `AlbumArt.tsx`, `SearchModal.tsx`. These will be built when their corresponding pages are implemented.

---

## Key Decisions

### API Client Generation (Orval)

- **Orval v8** generates TypeScript types, Tanstack Query hooks, and MSW mock handlers from `web/openapi.json`
- Uses `tags-split` mode: output splits by FastAPI route tag into `generated/playlists/`, `generated/connectors/`, etc.
- Generated output lives in `api/generated/` — never hand-edit. Regenerate with `pnpm generate`
- Custom `api/client.ts` provides the `customFetch` mutator (error handling, API error envelope parsing)
- **Build-time contract safety**: TypeScript compilation fails if backend schema changes break frontend types — catches API drift before runtime

**Critical: `customFetch` envelope contract**

Orval v8 generates discriminated union response types:

```typescript
type Response = { data: T; status: 200; headers: Headers }
             | { data: HTTPValidationError; status: 422; headers: Headers };
```

Components narrow on `.status` to access `.data`:
```typescript
const playlists = data?.status === 200 ? data.data : undefined;
```

The `customFetch` mutator **must** return `{ data: body, status, headers }` — not raw body. Returning just the parsed JSON silently breaks every discriminated union access.

### SSE Integration

**Transport layer** (`api/sse-client.ts`):
- Uses native `fetch()` + `eventsource-parser` (not `EventSource` or `@microsoft/fetch-event-source`)
- Full control over `AbortSignal` — `reader.cancel()` registered on signal abort so async iterables terminate naturally
- Returns `AsyncIterable<SSEEvent>` — consumers use `for await...of` with no custom abort plumbing
- Separated from React hooks so tests can mock the module without fighting jsdom's incomplete Web Streams API

**Shared SSE hook infrastructure** (`hooks/useSSEConnection.ts`, `hooks/useNodeStatuses.ts`, `lib/sse-types.ts`):
- `useSSEConnection(operationId, { onEvent, onStreamEnd })` — owns the full SSE lifecycle (AbortController, `connectToSSE`, `for await` loop, AbortError suppression, malformed-JSON skip). Consumer hooks only handle event semantics via callback.
- `onEvent` stored in a ref so the effect only depends on `operationId` — callers don't need to memoize callbacks
- `useNodeStatuses()` — encapsulates the `node_status` Map update logic and snake_case→camelCase mapping shared by execution and preview hooks
- `NodeStatus` and `NodeExecutionStatus` types live in `lib/sse-types.ts`, re-exported from `useWorkflowExecution.ts` to preserve import paths

**Consumer hooks** (compose the shared infrastructure):
- `useOperationProgress(operationId, { invalidateKeys })` — composes `useSSEConnection`, handles `started`/`progress`/`complete`/`error`/`sub_operation_*` events, invalidates Tanstack Query keys on completion
- `useWorkflowExecution(workflowId)` — composes `useSSEConnection` + `useNodeStatuses`, manages execution lifecycle (mutation → SSE → node status → query invalidation)
- `useWorkflowPreview()` — composes `useSSEConnection` + `useNodeStatuses`, manages preview lifecycle for saved and unsaved workflows (v0.4.3)

### Component Strategy

**Foundation: shadcn/ui** — source code copied into `web/src/components/ui/`, not a runtime dependency. Built on Radix UI primitives (accessible, keyboard-navigable, screen-reader-friendly). Styled with Tailwind v4 `@theme` tokens. Every component has `data-slot` attributes for targeted styling. Customize freely — shadcn/ui is a starting point, not a constraint.

**Component layers**:
- **`ui/`**: shadcn/ui primitives, customized to dark editorial aesthetic (Button, Card, Table, Dialog, Command, etc.)
- **`shared/`**: Narada-specific composites reusable across pages (e.g., `TrackRow` in Library, Playlist Detail, Search Modal; `AlbumArt` with glow effect)
- **`pages/`**: Route-level, compose ui + shared. Own data fetching via Tanstack Query hooks.
- Keep components small. Extract when reused, not preemptively.

### Error Boundaries

`PageLayout` wraps `<Outlet />` with `react-error-boundary`'s `<ErrorBoundary>`:
- **Sidebar stays outside** the boundary — a page crash never takes down navigation
- **`resetKeys={[pathname]}`** auto-resets the boundary when the user navigates via sidebar (no manual state clearing)
- **`PageErrorFallback`** renders with `role="alert"` and a "Try again" button that calls `resetErrorBoundary`
- Visual style matches `EmptyState` (same container classes) — no new design primitives

### Design Tokens (Tailwind v4 `@theme`)

```css
/* theme.css — dark-mode primary, editorial music aesthetic */
@theme {
  /* Typography — distinctive, not generic */
  --font-display: "Space Grotesk", system-ui, sans-serif;
  --font-body: "Newsreader", Georgia, serif;
  --font-mono: "JetBrains Mono", monospace;

  /* Dark palette (default) — warm, not cold */
  --color-surface: oklch(0.13 0.01 60);           /* warm near-black */
  --color-surface-elevated: oklch(0.18 0.01 60);   /* cards, modals */
  --color-surface-sunken: oklch(0.10 0.01 60);     /* inset areas */
  --color-border: oklch(0.25 0.01 60);             /* subtle warm dividers */
  --color-text: oklch(0.93 0.005 80);              /* warm white */
  --color-text-muted: oklch(0.60 0.01 60);         /* secondary text */
  --color-text-faint: oklch(0.55 0.01 60);         /* tertiary text, descriptions */

  /* Accents — sharp against dark */
  --color-primary: oklch(0.75 0.15 85);            /* warm gold */
  --color-secondary: oklch(0.70 0.12 25);          /* soft coral */
  --color-success: oklch(0.72 0.17 155);           /* green */
  --color-destructive: oklch(0.60 0.20 25);        /* warm red */

  /* Status badges */
  --color-status-connected: oklch(0.72 0.17 155);
  --color-status-expired: oklch(0.70 0.15 70);
  --color-status-available: oklch(0.65 0.12 230);

  /* Connector identity */
  --color-spotify: oklch(0.72 0.22 155);
  --color-lastfm: oklch(0.55 0.22 25);
  --color-apple: oklch(0.65 0.25 350);

  /* Light mode overrides (via .light or prefers-color-scheme) */
  /* --color-surface: oklch(0.97 0.005 80);  warm cream */
  /* --color-text: oklch(0.20 0.01 60);      warm dark text */
}
```

Dark mode is the default — CSS variables are the single source of truth. Light mode overrides via class or media query, no runtime theme switching logic.

### State Management

| State Type | Solution |
|-----------|---------|
| Server state (tracks, playlists, etc.) | Tanstack Query (cache, refetch, optimistic updates) |
| URL state (filters, pagination, search) | React Router search params |
| Local UI state (modal open, form values) | React `useState` / `useReducer` |
| Operation progress | `useOperationProgress` hook (composes `useSSEConnection`) |
| Workflow execution | `useWorkflowExecution` hook (composes `useSSEConnection` + `useNodeStatuses`) — used on detail page (inline execution) and list page (per-row instances) (v0.4.2) |
| Workflow preview | `useWorkflowPreview` hook (composes `useSSEConnection` + `useNodeStatuses`) — dry-run execution for editor (v0.4.3) |
| Workflow canvas (editor) | Zustand `useWorkflowStore` — React Flow nodes, edges, viewport, undo/redo (v0.4.3) |

No global state store. If cross-page state emerges, evaluate React Context before reaching for a library.

### Accessibility (WCAG 2.2 AA)

- Semantic HTML throughout (`nav`, `main`, `article`, `header`)
- ARIA labels on all interactive elements
- Keyboard navigation: full Tab order, Esc closes modals, Enter submits
- Focus indicators: 2px outline, 3:1 contrast ratio
- Color contrast: 4.5:1 normal text, 3:1 large text and UI components
- No color-only information (icons + color for status badges)
- Skip links for main content
- `aria-live` regions for dynamic progress updates
- Touch targets: 44x44px minimum

### Testing Strategy

| Layer | Tool | Coverage Target | Focus |
|-------|------|----------------|-------|
| Component unit | Vitest + React Testing Library | 60% | Primitives, form validation, conditional rendering |
| API integration | Vitest + MSW (Mock Service Worker) | Hooks, error states, loading states | Tanstack Query hook behavior |
| Accessibility | @axe-core/react + manual | All pages | Automated a11y scanning + keyboard testing |
| E2E | Playwright (Chromium, desktop) | Critical paths | Playlist CRUD, track search, workflow execution, import flow |

### Build & Deployment

- Vite builds to `web/dist/`
- **Development**: Vite dev server proxies `/api` → `localhost:8000` (FastAPI)
- **Production**: FastAPI serves the SPA as a single deployable artifact:
  - `/assets/*` mounted via `StaticFiles` for hashed JS/CSS bundles
  - `/{path}` catch-all returns `index.html` for client-side routing
  - Paths starting with `api/` are excluded from the catch-all (return proper 404, not `index.html`)
  - Implementation: `src/interface/api/app.py` → `_mount_static()`
- Dockerfile (v0.5.0): add `pnpm install && vite build` stage, copy `dist/` to runtime image
- No separate frontend hosting — single deployable artifact
