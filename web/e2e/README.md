# mixd web E2E tests

Playwright end-to-end suite. See `web/.claude/rules/web-e2e-patterns.md` for
file-layout, mocking, and assertion conventions.

## Two configs

- **`web/playwright.config.ts`** (default) — runs without auth (no
  `VITE_NEON_AUTH_URL`). Excludes `auth-*.spec.ts` via `testIgnore`. Used by
  `pnpm test:e2e` and the `web-e2e` CI job.
- **`web/playwright-auth.config.ts`** — runs with auth enabled, mocked via
  `page.route()`. Used by `pnpm test:e2e:auth`.

## Visual regression

`visual.spec.ts` baselines each route × theme as a `toHaveScreenshot`
assertion. Snapshots live in `web/e2e/__screenshots__/visual.spec.ts/`.

### Diff thresholds (set in `playwright.config.ts`)

- `maxDiffPixelRatio: 0.005` — 0.5% of pixels may differ before failure
- `animations: "disabled"` — CSS animations frozen at first frame
- `caret: "hide"` — text cursor masked (fixes flaky-blink false positives)

### Updating baselines (CI-only policy)

**Local-generated PNGs are not accepted.** Font rendering differs across
macOS, Windows, and Linux; mixing platforms produces noisy diffs that
regenerate on every PR. The single source of truth is the Playwright Docker
image used in CI.

To regenerate baselines:

1. Push your branch to a PR.
2. Comment `/update-snapshots` on the PR (or run the workflow manually
   via the Actions tab — `web-e2e` workflow, `update-snapshots: true`).
3. CI runs `playwright test --update-snapshots` inside
   `mcr.microsoft.com/playwright:v1.59.1-noble` and commits the
   regenerated PNGs to your branch.
4. Pull the auto-commit and continue work.

Until that workflow trigger ships, regenerate locally with the same
Docker image:

```bash
docker run --rm -it \
  -v "$PWD":/work -w /work/web \
  -p 5173:5173 \
  mcr.microsoft.com/playwright:v1.59.1-noble \
  bash -c "corepack enable && corepack prepare pnpm@11.0.8 --activate \
           && pnpm install --frozen-lockfile \
           && pnpm test:e2e -- --update-snapshots"
```

PNGs that don't match CI font rendering will fail review.

### Adding a route to the visual suite

Append to the `ROUTES` array in `visual.spec.ts`. Each entry generates
`{slug}-{theme}.png` for every theme. If the route requires API responses
(track lists, playlist data, etc.), add the corresponding `page.route()`
mocks in the same file or in a shared fixture.

## Running

```bash
pnpm --prefix web test:e2e           # default config (includes visual.spec.ts)
pnpm --prefix web test:e2e:auth      # auth flows only
pnpm --prefix web exec playwright test --update-snapshots  # regenerate (CI only — see above)
pnpm --prefix web exec playwright test --ui  # interactive runner
```
