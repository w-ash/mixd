---
name: vitest-strategy-architect
description: Use this skill when you need Vitest component testing strategy, React Testing Library patterns, Tanstack Query mocking with MSW, or Playwright E2E test design for mixd's web UI (v0.3.0+).
---

# Frontend Test Strategy — mixd web UI

> Related skill: `api-contracts` (REST + SSE conventions). E2E editing specifics (incl. the visual-audit harness) auto-load from `.claude/rules/web-e2e-patterns.md` when touching `web/e2e/**` — don't restate them here.

## Test pyramid (60/35/5)

- **Component unit (60%)** — `src/**/*.test.tsx`, RTL, MSW-mocked API, <100ms each. Rendering + interactions.
- **Integration (35%)** — same naming convention; real Tanstack Query against MSW, flows across components, <1s each.
- **E2E (5%)** — `web/e2e/*.spec.ts`, Playwright, Chromium desktop only, critical flows only. **Run in the CI-pinned Docker image** — native macOS false-fails (procedure + current image tag in `web/e2e/README.md`).

Philosophy: test user behavior via accessible queries (`getByRole`/`getByLabelText`), never class names or implementation details. Prefer integration over isolated unit tests.

## Mixd test infrastructure

**Setup** (`web/src/test/setup.ts`):
- Bootstraps MSW server with the auto-generated Orval handlers (`web/src/api/generated/**/*.msw.ts`) — every test starts with default mock responses.
- `beforeAll(server.listen)` / `afterEach(server.resetHandlers)` / `afterAll(server.close)`; wired via `vitest.config.ts` `setupFiles`.

**`renderWithProviders(ui, options?)`** (`web/src/test/test-utils.tsx`):
- Wraps in a test QueryClient (`retry: false`, `gcTime: 0` — no cache bleed between tests) + `MemoryRouter` (configurable `initialEntries`).
- Use for anything with hooks, routing, or queries; plain `render()` only for pure presentational components.

**Per-test MSW overrides**:

```tsx
import { http, HttpResponse } from 'msw'
import { server } from '#/test/setup'

server.use(http.get('*/api/v1/playlists/:id', ({ params }) =>
  HttpResponse.json({ id: Number(params.id), name: 'Test Playlist' })))
```

- The `*/` origin glob is required — it matches through the Vite proxy.
- Simulate errors by overriding the handler to return `HttpResponse.json(..., { status: 500 })` — never mock `global.fetch`.

**Path alias**: `#/` → `web/src/` in all test imports.

**Async**: `await screen.findBy...` or `await waitFor(...)` for anything post-fetch; a bare `getBy` on async content is the most common failure.

## Designing coverage for a change

1. What renders? → component tests for each visual state (loading/error/empty/success — `QueryStates` gives these for free; test the consumer's wiring, not the wrapper).
2. What round-trips? → integration tests with MSW overrides for the success + at least one error path.
3. Is it a critical user flow (import, sync, workflow run, playlist edit)? → at most one E2E spec; everything else stays at the MSW tier.
4. Shared state pollution symptoms (pass alone, fail together) are already handled by `renderWithProviders`' fresh QueryClient — if you see them anyway, look for module-level state.

## Commands

```bash
pnpm --prefix web test                          # all Vitest
pnpm --prefix web test src/pages/Library.test.tsx  # one file
# E2E — CI-pinned Docker image (see CLAUDE.md version-bump bar for the full command)
pnpm --prefix web test:e2e:audit                # fixture-driven visual audit harness
```
