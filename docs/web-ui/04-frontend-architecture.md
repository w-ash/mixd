# Frontend Architecture

> Skeletal architecture decisions for the React web UI.
> Stack choices and key decisions are stable; component catalog deferred to implementation.

---

## Tech Stack

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Build | **Vite 6+** | esbuild transpilation, fast HMR, optimized production builds |
| Framework | **React 19+** | Ecosystem, component model, Tanstack Query integration |
| Language | **TypeScript 5.7+** (strict mode) | Type safety across API boundary |
| Styling | **Tailwind CSS v4** | Rust engine (10x perf), CSS-first `@theme` tokens, dark mode via CSS vars |
| Routing | **React Router** | Standard, file-system-like route structure |
| Server state | **Tanstack Query** | Stale-while-revalidate, background refetch, optimistic updates |
| Components | **shadcn/ui** (owned source) | Accessible Radix primitives + Tailwind styling. Source code copied into project, not a runtime dependency |
| Animation | **Motion** (React) | CSS-first for simple transitions; Motion for orchestrated sequences |
| Workflow viz | **React Flow** | DAG rendering with pan/zoom, node customization |
| Testing | **Vitest** (unit/integration) + **Playwright** (E2E) | Native ESM, Jest-compatible API |
| Package mgr | **pnpm** | Faster installs, efficient disk usage |
| Linting | **ESLint** (flat config) + **Prettier** | Standard code quality tooling |

**No Redux / Zustand**: Tanstack Query handles all server state. Local UI state (modals, forms) lives in React component state. No global state management library needed at this scale.

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

### SSE Progress Provider (New for v0.3.1)

- Implements `ProgressSubscriber` protocol (same interface as `RichProgressProvider`)
- Serializes `ProgressEvent` objects to SSE `data:` frames
- Registered with `AsyncProgressManager.subscribe()` — same pub/sub mechanism the CLI uses
- Frontend connects via `useSSE` hook, receives the same progress events the CLI renders as Rich progress bars

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
| `--color-primary` | `oklch(0.75 0.15 85)` | **Warm gold** — pops against dark, evokes vinyl warmth |
| `--color-secondary` | `oklch(0.70 0.12 25)` | **Soft coral** — active states, emphasis |
| `--color-destructive` | `oklch(0.60 0.20 25)` | Destructive actions |
| `--color-success` | `oklch(0.72 0.17 155)` | Completion, connected status |

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

- Dark gradient meshes on dashboard/hero areas — subtle warmth, not flat black
- Subtle noise texture overlay for analog/vinyl feel
- Card elevation via warm-tinted ambient glow, not generic gray `box-shadow`
- Album art with gentle blur for contextual backgrounds on detail pages (dark overlay for readability)
- Borders: subtle warm-tinted dividers, not harsh lines

---

## Project Structure

```
web/
├── public/                          Static assets
├── src/
│   ├── api/                         API client layer (generated via OpenAPI codegen)
│   │   ├── generated/               Auto-generated from /openapi.json (do not edit)
│   │   │   ├── client.ts            Base fetch client with typed endpoints
│   │   │   ├── hooks.ts             TanStack Query useQuery/useMutation hooks
│   │   │   ├── model.ts             Request/response TypeScript types
│   │   │   └── msw.ts               MSW mock handlers (for testing)
│   │   ├── operations.ts            Custom SSE hooks (not auto-generated)
│   │   └── overrides.ts             Query option overrides (staleTime, retry, etc.)
│   ├── components/
│   │   ├── ui/                      shadcn/ui primitives (owned source)
│   │   │   ├── Button.tsx
│   │   │   ├── Card.tsx
│   │   │   ├── Table.tsx
│   │   │   ├── Input.tsx
│   │   │   ├── Dialog.tsx
│   │   │   ├── Command.tsx
│   │   │   ├── Toast.tsx
│   │   │   ├── Badge.tsx
│   │   │   ├── Skeleton.tsx
│   │   │   └── Progress.tsx
│   │   ├── layout/                  App shell components
│   │   │   ├── Sidebar.tsx
│   │   │   ├── Header.tsx
│   │   │   └── PageLayout.tsx
│   │   └── shared/                  Shared composite components
│   │       ├── TrackRow.tsx
│   │       ├── AlbumArt.tsx
│   │       ├── ConnectorIcon.tsx
│   │       ├── EmptyState.tsx
│   │       ├── OperationProgress.tsx
│   │       └── SearchModal.tsx
│   ├── pages/                       Route-level page components
│   │   ├── Dashboard.tsx
│   │   ├── Library.tsx
│   │   ├── TrackDetail.tsx
│   │   ├── Playlists.tsx
│   │   ├── PlaylistDetail.tsx
│   │   ├── PlaylistLinks.tsx
│   │   ├── Workflows.tsx
│   │   ├── WorkflowDetail.tsx
│   │   ├── WorkflowEditor.tsx
│   │   ├── Imports.tsx
│   │   └── Settings.tsx
│   ├── hooks/                       Shared custom hooks
│   │   ├── useSSE.ts                SSE connection with reconnection
│   │   ├── useOperation.ts          Operation progress tracking
│   │   └── useDebounce.ts           Search input debouncing
│   ├── types/                       Shared TypeScript types
│   │   └── domain.ts                Frontend-only types (UI state, component props)
│   ├── App.tsx                      Router + layout setup
│   ├── main.tsx                     Entry point
│   └── theme.css                    Tailwind v4 @theme tokens
├── index.html
├── components.json                 shadcn/ui CLI config
├── vite.config.ts
├── tsconfig.json
└── package.json
```

---

## Key Decisions

### API Client Generation (OpenAPI Codegen)

- Generate TypeScript types **and** TanStack Query hooks from FastAPI's `/openapi.json` at build time
- Use **Orval** or **openapi-ts** with the TanStack Query plugin — these are dev-time build tools, not heavyweight runtime generators
- Generated output lives in `api/generated/` (types, hooks, and MSW mock handlers) — never hand-edit
- Custom hooks (SSE, query option overrides) live alongside in `api/` as hand-written files
- Error handling: interceptor on the generated client converts API error envelope to typed `ApiError` objects
- **Build-time contract safety**: TypeScript compilation fails if backend schema changes break frontend types — catches API drift before runtime

### SSE Integration

- Custom `useSSE` hook wraps `@microsoft/fetch-event-source` (not native `EventSource`) with:
  - Automatic reconnection with `Last-Event-ID` header
  - POST support and custom headers (native `EventSource` only supports GET, no auth headers)
  - Fallback to polling `GET /operations/{id}` if SSE unavailable
  - Event parsing into typed `ProgressEvent` objects
  - Connection state management (connecting, connected, reconnecting, closed)
- `useOperation` hook composes `useSSE` + Tanstack Query for operation tracking

### Component Strategy

**Foundation: shadcn/ui** — source code copied into `web/src/components/ui/`, not a runtime dependency. Built on Radix UI primitives (accessible, keyboard-navigable, screen-reader-friendly). Styled with Tailwind v4 `@theme` tokens. Every component has `data-slot` attributes for targeted styling. Customize freely — shadcn/ui is a starting point, not a constraint.

**Component layers**:
- **`ui/`**: shadcn/ui primitives, customized to dark editorial aesthetic (Button, Card, Table, Dialog, Command, etc.)
- **`shared/`**: Narada-specific composites reusable across pages (e.g., `TrackRow` in Library, Playlist Detail, Search Modal; `AlbumArt` with glow effect)
- **`pages/`**: Route-level, compose ui + shared. Own data fetching via Tanstack Query hooks.
- Keep components small. Extract when reused, not preemptively.

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

  /* Accents — sharp against dark */
  --color-primary: oklch(0.75 0.15 85);            /* warm gold */
  --color-secondary: oklch(0.70 0.12 25);          /* soft coral */
  --color-success: oklch(0.72 0.17 155);           /* green */
  --color-destructive: oklch(0.60 0.20 25);        /* warm red */

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
| Operation progress | `useSSE` hook + Tanstack Query |

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
- FastAPI serves static files via `StaticFiles` mount
- Dockerfile (v0.5.0): add `pnpm install && vite build` stage, copy `dist/` to runtime image
- API proxy in Vite dev config for local development (`/api` → `localhost:8000`)
- No separate frontend hosting -- single deployable artifact
