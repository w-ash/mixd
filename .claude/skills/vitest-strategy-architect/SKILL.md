---
name: vitest-strategy-architect
description: Use this skill when you need Vitest component testing strategy, React Testing Library patterns, Tanstack Query mocking with MSW, test-value checks (kill criterion, mutation testing), or Playwright E2E test design for mixd's web UI (v0.3.0+).
---

# Frontend Test Strategy — mixd web UI

> Related skill: `api-contracts` (REST + SSE conventions). E2E editing specifics (incl. the visual-audit harness) auto-load from `.claude/rules/web-e2e-patterns.md` when touching `web/e2e/**` — don't restate them here.
>
> Test value standard: `.claude/rules/test-value.md` (kill criterion, expected values, mocks, banned patterns V1–V10). This skill applies it to the web UI and does not restate it.

## Test tiers

Pick the tier that owns the contract. There is no ratio to hit.

- **Component** — `src/**/*.test.tsx`, RTL, MSW-mocked API, <100ms each. Rendering + interactions.
- **Integration** — same naming convention; real Tanstack Query against MSW, flows across components, <1s each.
- **E2E** — `web/e2e/*.spec.ts`, Playwright, Chromium desktop only, critical flows only. **Run in the CI-pinned Docker image** — native macOS false-fails (procedure + current image tag in `web/e2e/README.md`).

Test user behavior via accessible queries (`getByRole`/`getByLabelText`), never class names or implementation details. Prefer integration over isolated unit tests.

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

## Writing a test

Apply `.claude/rules/test-value.md`. Web specifics:

1. **Mock the network** with `server.use` overrides.
2. **Assert what the user sees.** Query by role, label, or text; assert exact content and state (`toHaveTextContent("3 tracks")`, `toBeDisabled()`).
3. **A call as the contract** (navigation, mutation args) → `toHaveBeenCalledWith`. For API calls, capture the request in the MSW handler and assert its body.

Biome runs `nursery/useExpect` (test with no assertion) and `suspicious/noMisplacedAssertion` (`expect` outside a test) on `*.test.ts(x)` as errors.

## Choosing tests for a change

1. What renders? → component tests for each visual state the change affects (loading/error/empty/success — `QueryStates` gives these for free; test the consumer's wiring, not the wrapper).
2. What round-trips? → MSW-backed tests for each response the UI handles differently (e.g. a 409 that shows a conflict message). A response with no distinct UI needs no test.
3. Is it a critical user flow (import, sync, workflow run, playlist edit)? → at most one E2E spec; everything else stays at the MSW tier.
4. Shared state pollution symptoms (pass alone, fail together) are already handled by `renderWithProviders`' fresh QueryClient — if you see them anyway, look for module-level state.

## Mutation testing

`pnpm --prefix web test:mutate` runs Stryker on `src/lib/**` and `src/hooks/**`. Survivors: `test-value.md` (Mutation survivors).

## Commands

```bash
pnpm --prefix web test                          # all Vitest
pnpm --prefix web test src/pages/Library.test.tsx  # one file
pnpm --prefix web test:mutate                   # Stryker on src/lib + src/hooks
# E2E — CI-pinned Docker image (see CLAUDE.md version-bump bar for the full command)
pnpm --prefix web test:e2e:audit                # fixture-driven visual audit harness
```
