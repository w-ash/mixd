---
paths:
  - "web/src/components/**"
  - "web/src/pages/**"
  - "web/src/theme.css"
---
# Design Review Workflow (Playwright MCP)

When iterating on UI — fixing padding, alignment, color, layout, or responsive breakpoints — use the Playwright MCP server (loaded as `mcp__plugin_playwright_playwright__*`) to **see** the rendered page rather than guessing from the diff.

The CI visual-regression suite (`web/e2e/visual.spec.ts`) is a guard against regressions after-the-fact. The MCP loop is the **dev-time feedback** that produces clean changes before commit. They're complementary.

For a **deterministic full-state inventory** — every state of a page (loading/empty/error/populated, each status × direction, dialogs) side by side across viewport and theme — the MCP loop can't help: it runs against `pnpm dev` with no seeded data, so it can't reach the populated/unresolved/error states. Use the **fixture-driven visual-audit harness** instead (route-mocked, `pnpm --prefix web test:e2e:audit` → screenshots in `web/e2e/__audit__/`). Worked example + how to add a new page: `.claude/rules/web-e2e-patterns.md` § *Visual-audit harness*.

## The loop

1. Make the code change.
2. Vite HMR auto-reloads (no manual refresh if `pnpm dev` is running).
3. **`browser_snapshot`** — accessibility tree, ~200–400 tokens. Default observation tool. Confirms element identity, structure, ARIA roles.
4. **`browser_take_screenshot`** with a `locator` (e.g., `[role="navigation"]`) — capture just the affected region when visual appearance is the question. ~5–8K tokens.
5. **`browser_evaluate`** — `window.getComputedStyle(el).padding` etc. when neither tree nor pixels resolve the question (e.g., a Tailwind class you suspect didn't apply).
6. Full-page screenshots only when a layout problem affects the whole composition. Last resort.

## Tool selection cheat sheet

| Question | Tool | Why |
|---|---|---|
| "Does this element exist with the right role / aria-label?" | `browser_snapshot` | Cheap, semantic |
| "Does the bottom nav clear the iPhone home indicator?" | `browser_take_screenshot` with locator | Visual verification of one region |
| "Why is `p-4` rendering as 12px instead of 16px?" | `browser_evaluate` (computed style) | Pixels lie about CSS source; computed values are truth |
| "Does the Dashboard look broken at iPad portrait?" | `browser_resize` + `browser_snapshot`, then locator screenshot if structure looks fine | Confirm structure first, then check pixels |

## Multi-viewport iteration

Mixd's responsive model has one breakpoint that matters (`lg:` = 1024px, set in `useIsMobile`). Resize between snapshots in the same session:

```
browser_resize(390, 844)    # iPhone 15 Pro — mobile shell
browser_snapshot
browser_resize(820, 1180)   # iPad portrait — also mobile shell
browser_snapshot
browser_resize(1280, 800)   # Desktop — Sidebar + main
browser_snapshot
```

Catches responsive issues in one round-trip rather than three separate sessions.

## Anti-patterns

- **Full-page screenshots in every loop iteration.** 50K tokens × 5 iterations = 250K of pure observation overhead. Use locator screenshots for component-level fixes.
- **Screenshot-first when the question is structural.** "Is this button keyboard-accessible?" → `browser_snapshot`, not pixels.
- **Committing observation screenshots.** `web/.playwright-mcp/` is in `.gitignore`; if you save screenshots elsewhere, gitignore them or `rm` after the loop. The committed PNGs under `web/e2e/__screenshots__/` are CI baselines, distinct from observation artifacts.
- **Iterating without HMR running.** If `pnpm dev` isn't up you're inspecting stale or 503-error state. Restart before observation.
- **Reading the user's screenshot when an MCP snapshot would do.** A user-pasted screenshot is a debugging hint, not a substitute for `browser_snapshot` — fetch fresh state via MCP after every change.

## Starting the dev server

- **`pnpm dev`** (root) — full stack: PostgreSQL via Docker testcontainers + API + Vite. Use when the design question depends on data shape.
- **`pnpm --prefix web dev`** — Vite only, all API calls 503. Faster startup, sufficient for pure layout/styling work where the page renders empty/loading state.

The default `playwright.config.ts` `webServer.command: "pnpm dev"` runs from `web/` so it picks up the Vite-only script — Playwright MCP can attach to whichever server is running on `localhost:5173`.
