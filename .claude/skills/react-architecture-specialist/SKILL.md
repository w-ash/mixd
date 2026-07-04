---
name: react-architecture-specialist
description: Use this skill when you need React + TypeScript patterns, component architecture, Tanstack Query design, or performance optimization guidance for mixd's web UI (v0.3.0+).
---

# React Architecture — mixd web UI

> Related skills: `frontend-design` (visual identity/tokens), `api-contracts` (REST + SSE conventions). The always-loaded edit-time constraints live in `.claude/rules/web-frontend-patterns.md` — this skill adds the deeper architecture context; don't restate the rule.

## Stack (verify versions in `web/package.json` before citing)

- React 19 · TypeScript strict · Vite 8 (Rolldown) · Tailwind CSS v4 (`@theme` tokens) · pnpm
- Tanstack Query v5 for all server state; Zustand for navigation-surviving stores (`web/src/stores/`); React Router search params for URL state
- Vitest + React Testing Library + MSW; Playwright E2E (Chromium, in the CI-pinned Docker image)

## Component hierarchy

`pages/` (route-level, owns data fetching) → `shared/` (composites reused across 2+ pages) → `ui/` (shadcn/ui Radix primitives, owned source — customize freely). Keep business logic out of components entirely; the backend owns business rules, components own presentation and interaction.

## API layer contract

- **Orval codegen**: `web/openapi.json` → `pnpm --prefix web sync-api` (export + generate) or `pnpm --prefix web generate` (Orval only) → `web/src/api/generated/` — tags-split query/mutation hooks, MSW handlers, model types. Never hand-edit generated files.
- **`customFetch`** (`web/src/api/client.ts`): Orval mutator; wraps native fetch, parses JSON, returns `{ data, status, headers }`, throws typed `ApiError` (`status`, `code`, `message`, `details`) on non-2xx.
- **`createQueryClient`** (`web/src/api/query-client.ts`): retry only on `status >= 500` (via `instanceof ApiError`), `staleTime` 30s default, `gcTime` 5min default. Don't override per-query without a reason.

## Query conventions

- Query keys mirror the REST resource: `['playlist', id]`, list keys invalidated alongside detail keys after mutations (`invalidateQueries` for both `['playlist', id]` and `['playlists']`).
- Never fetch in `useEffect`; never hand-roll loading state — data views render through `shared/QueryStates` (see the rule).
- SSE progress: `useSSE` + Tanstack Query reconcile; on stream end, re-fetch via REST (the v0.8.8 stream-end reconcile pattern).

## Error boundary architecture

- `react-error-boundary` wraps `<Outlet />` inside `PageLayout`; the sidebar stays **outside** the boundary so navigation survives any page crash.
- `resetKeys={[pathname]}` auto-clears on route change; `PageErrorFallback` matches `EmptyState` styling and uses `role="alert"`.

## Performance

Measure first. `React.memo`/`useMemo`/`useCallback` only for measured hot paths (the workflow-run node chain and snapshot-poll reconciliation are the precedents — see `useNodeStatuses`' single-Map-allocation merge and second-resolution setState snapping from v0.7.8.19). Don't memo by default; stable Tanstack Query `structuralSharing` already keeps unchanged references equal.

## Review checklist

1. Data fetching through generated Orval hooks + `QueryStates` — no `useEffect` fetches, no hand-rolled ladders
2. Mutations invalidate both detail and list query keys
3. Explicit prop types; no `any`, no `@ts-ignore`
4. State in the right tier: server=TQ, URL=router params, local=`useState`, navigation-surviving=existing Zustand store
5. Shared UI extracted only at 2+ page reuse; check `shared/` and `ui/` before creating anything new
6. Tailwind `@theme` tokens only — no hardcoded colors (severity states use `status-*` tokens, v0.8.13)
7. Mobile: `useIsMobile()` single `lg:` breakpoint; dialogs via `ResponsiveDialog`
