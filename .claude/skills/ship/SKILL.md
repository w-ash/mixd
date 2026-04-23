---
name: ship
description: Ship a completed backlog version — reconcile against stories, run all health checks with zero tolerance, bump version, and commit. Use after all implementation for a version is done.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash Read Edit Grep Glob
---

# Ship

Post-implementation release workflow for a completed backlog version.

Recent commit conventions:
!`git log --oneline -5`

## Versioning scheme

Mixd uses `major.minor.feature.revision` — see the `version-management` rule. Summary for this skill:

- First ship of a new feature (e.g., `0.7.6`) → bump **feature**, no revision segment.
- Any ship after that on the same feature, while it's still `🚀 Shipped` in the roadmap (post-deploy fixes, additions discovered during prod testing) → bump **revision** (`0.7.6.1`, `0.7.6.2`, ...).
- The README row transitions `🔜 Not Started` → `🔨 In Progress` → `🚀 Shipped` via `/ship`. **Promotion to `✅ Completed` is always user-driven**, never something `/ship` decides on its own.

## Step 0: Pre-flight checks

- Run `git status` to see the current state. If there are unexpected untracked or modified files beyond implementation work, warn the user before proceeding.
- Read `docs/backlog/README.md` to identify the current feature row and what comes next.
- Read `pyproject.toml` to check the current version.

Classify the ship into one of three shapes before proceeding:

| Shape | Detection | Version action | Backlog-row action |
|---|---|---|---|
| **First ship of a new feature** | The first `🔨 In Progress` or `🔜 Not Started` row matches the work in `git status` | Bump **feature** (e.g., `0.7.5` → `0.7.6`) | `🔜 Not Started` / `🔨 In Progress` → `🚀 Shipped` |
| **Revision of the `🚀 Shipped` feature** | The current row is `🚀 Shipped` and the working-tree changes are fixes/additions on that feature | Bump **revision** (e.g., `0.7.6` → `0.7.6.1`, or `0.7.6.1` → `0.7.6.2`) | No row change; stays `🚀 Shipped` |
| **Already shipped this exact feature + revision** | Matching version is already tagged and the row is `🚀 Shipped` or `✅ Completed` with no new changes | Stop — "This version is already shipped." | none |

If the shape is ambiguous (e.g., `git status` shows work that straddles both the current `🚀 Shipped` feature and an unstarted one), stop and ask the user which to ship.

## Step 1: Reconcile implementation against backlog

Behavior depends on the shape classified in Step 0.

### First ship of a new feature

- From the README version matrix, find the feature row being shipped.
- Read the corresponding `docs/backlog/v0.X.x.md` file.
- Review `git log` since the last shipped version and `git status` to understand what was actually implemented.
- Compare the actual changes against each unchecked `- [ ]` story in the backlog:
  - **Clearly done**: The implementation fully satisfies the story's "What" — check it off.
  - **Partially done or done differently**: Update the story's Notes field to describe what was actually delivered and how it differs from the plan. Check it off only if the intent was met, even if the approach changed.
  - **Not touched**: Leave unchecked.
- If implementation substantially deviates from the plan (new scope, skipped stories, different architecture), stop and report the discrepancies to the user before making any backlog changes. Wait for instructions.
- Leave the **Post-Deploy Revisions** epic alone — it belongs to the `🚀 Shipped` phase, not to the first ship.

### Revision of the `🚀 Shipped` feature

- A revision doesn't necessarily correspond to a pre-planned backlog story. It often ships work discovered during prod testing.
- Append an entry to the feature's **Post-Deploy Revisions** epic in `docs/backlog/v0.X.x.md`. One bullet per revision, ordered by revision number:
  - `- [x] **v0.7.6.1** (YYYY-MM-DD) — one-line description of what shipped and why` + a short Notes sentence on what triggered it.
- If the revision closes a pre-existing unchecked story in the backlog (rare but possible), check it off per the "first ship" rules.
- Never promote the feature row to `✅ Completed` from this step; revisions never close the feature out.

## Step 2: Update backlog stories

In `docs/backlog/v0.X.x.md`, for each story confirmed as done (first-ship shape only; revisions use the Post-Deploy Revisions epic instead — see Step 1):
- Check the box: `- [ ]` → `- [x]`
- Set `Status: Completed (YYYY-MM-DD)` with today's date
- Update Notes if the implementation differed from the original plan — the backlog should reflect what actually shipped, not just what was planned

## Step 3: Update the roadmap (transition to 🚀 Shipped, never ✅ Completed)

In `docs/backlog/README.md`:

**First-ship shape:**
- Transition the feature row from `🔜 Not Started` / `🔨 In Progress` → `🚀 Shipped`.
- Update the `**Current Version**:` header to the newly shipped version number.
- Update the `**Next**:` header to the next `🔜 Not Started` feature from the matrix.
- **Do not** write `✅ Completed`. That transition is user-driven and happens later, separately, when the user confirms the feature is stable in prod.

**Revision shape:**
- No row-status change. The feature stays `🚀 Shipped`.
- Update the `**Current Version**:` header to the new revision (e.g., `0.7.6.1`).
- The `**Next**:` header is unchanged.

## Step 3b: Archive completed version file

File archival happens **only when the user explicitly closes out the last feature in a minor series** (see Step 10) — never inside `/ship`'s automatic flow. Skip this step.

## Step 4: Bump the version

Bump according to the shape classified in Step 0:

- **First ship of a new feature**: bump the **feature** segment (e.g., `0.7.5` → `0.7.6`). No revision segment.
- **Revision of the `🚀 Shipped` feature**:
  - If the current version has no revision segment (e.g., `0.7.6`), bump to `0.7.6.1`.
  - If the current version already has a revision segment (e.g., `0.7.6.2`), increment it (`0.7.6.3`).
  - If the current version is a historical `.postN` form (e.g., `0.7.5.post2`), the next revision starts the new scheme — continue the count: `0.7.5.post2` → `0.7.5.3` (continues from the `.post2` count). Don't reset or re-tag history.
- Never use `.postN`; it's reserved for metadata-only fixes and mixd doesn't ship those.

Write the new version into `pyproject.toml`.

## Step 5: Sync toolchain

Only if Step 4 changed `pyproject.toml`:
1. `uv sync` — updates installed package metadata so `importlib.metadata.version()` returns the new version
2. `pnpm --prefix web sync-api` — exports OpenAPI schema (picks up new version) + runs Orval codegen
3. Verify: `uv run python -c "from src import __version__; print(__version__)"` — should print the new version

If the version was not bumped, skip this entire step.

## Step 6: Code health gate

**Zero tolerance. No exceptions. No "pre-existing" passes.**

Run ALL health checks before committing. If any check produces errors, warnings, or test failures — STOP. Do not proceed to Step 7.

### Checks to run

Run backend and frontend checks in parallel where possible (use parallel Bash calls):

**Backend (run in parallel):**
- `uv run pytest` — fast tests (default addopts exclude slow/diagnostic)
- `uv run ruff check . --fix` — lint + autofix
- `uv run ruff format .` — autoformat

**Frontend (run in parallel with backend):**
- `pnpm --prefix web test` — Vitest component tests
- `pnpm --prefix web check` — Biome lint + format check
- `pnpm --prefix web build` — production build (catches TS type errors in bundled output)

**After the above complete:**
- `uv run basedpyright src/` — strict type checking (slowest check, runs last)

### On failure

If ANY check fails:
1. **STOP** — do not proceed to Step 7, 8, or 9.
2. **Report the exact errors** — show the actual output, not a summary. The user needs to see what failed.
3. **Never assume issues are pre-existing.** If it fails now, it blocks now. This is the quality gate.
4. **Suggest an architectural fix** — propose a solution that addresses the root cause. Do NOT suggest suppressions (`# noqa`, `# type: ignore`, `# pyright: ignore`, `# biome-ignore`). If the check is catching something real, fix the code. If the check is wrong, fix the check's configuration.
5. **Wait for the user** to decide: fix the issues and re-run `/ship`, or explicitly waive specific failures.

### On success

All seven checks pass with zero errors and zero warnings → proceed to Step 7.

## Step 7: Pre-commit hygiene

### 7a: Ensure generated-file exclusions

Check that `.pre-commit-config.yaml` excludes `^web/src/api/generated/` from `trailing-whitespace` and `end-of-file-fixer` hooks. These files are auto-generated by Orval (`pnpm --prefix web sync-api`), regenerated on every version bump, and already excluded from Biome linting. Fixing their whitespace is pointless — it returns on next codegen.

If the exclusions are already present, skip. If not, add them:

```yaml
-   id: trailing-whitespace
    exclude: ^web/src/api/generated/
-   id: end-of-file-fixer
    exclude: ^web/src/api/generated/
```

### 7b: Stage files

List all files to be staged, grouped by category:
- Backlog files (`docs/backlog/`)
- Version files (`pyproject.toml`, `uv.lock`)
- API schema and generated files (`web/openapi.json`, `web/src/api/generated/*`)
- Pre-commit config (if modified)
- Implementation source files
- Test files

Stage files by explicit name — never `git add -A` or `git add .`.

### 7c: Run pre-commit on staged files

Run `pre-commit run` (on staged files only, not `--all-files`) to catch any remaining issues.

- **All hooks pass**: proceed to Step 8.
- **Hooks auto-fix files** (ruff-format, trailing-whitespace): report which files were changed, re-stage them, and re-run `pre-commit run`. The second run should pass cleanly.
- **Hooks fail on non-auto-fixable issues** (invalid YAML, `breakpoint()` left in code, large files): STOP, report the failure, wait for user.

## Step 8: Commit

Create a git commit following the conventions from the git log above. The first line should carry the full version in the form `<type>: v<version> — <summary>`, e.g.:

- First feature ship: `feat: v0.7.6 — Spotify flow polish`
- Revision (fix): `fix: v0.7.6.1 — tag rename atomicity`
- Revision (new work found in prod): `feat: v0.7.6.2 — preview cache warmup`
- Revision (internal cleanup): `refactor: v0.7.6.3 — extract resolve helper`

The conventional-commit prefix (`fix:` / `feat:` / `refactor:` / `chore:`) carries the nature of the work; the version number just counts. Do **not** append a `Co-Authored-By` trailer.

If the commit fails (it shouldn't after Step 7c, but just in case):
- Read the hook output to understand what failed
- If hooks auto-fixed files: re-stage and retry once
- If it fails again: STOP and report. Do NOT use `--no-verify`.

## Step 9: Tag and deploy

After the commit succeeds:

1. Create the version tag: run `git tag v{VERSION}` (e.g., `git tag v0.6.6`).
2. Let the user know what's ready and how to deploy when they choose to:

> Version is committed and tagged locally. When you're ready to deploy, run `deploy` in your terminal.

### What `deploy` does

`deploy` is a shell function (defined in `~/.zshrc`) that:

1. Guards against missing tags (must run `/ship` first)
2. Pushes the commit and tag to origin together
3. The `v*` tag triggers `.github/workflows/release.yml`, which runs two jobs:
   - **GitHub Release**: generates changelog via git-cliff, creates a GitHub Release
   - **Deploy to Fly.io**: `flyctl deploy --remote-only` with `BUILD_HASH=$GITHUB_SHA`
4. Polls for the exact workflow run matching the pushed commit (up to 60s), then streams it via `gh run watch`
5. Fly.io runs `alembic upgrade head` as the release command (before switching traffic)
6. Health check at `/api/v1/health` gates the traffic cutover

## Step 10: Close out (user-driven, separate from /ship)

`/ship` never promotes a feature from `🚀 Shipped` to `✅ Completed`. That happens only when the user explicitly confirms the feature is stable in prod after testing.

When the user says something like *"v0.7.6 is good, move on"* or *"close out v0.7.6"*:

1. In `docs/backlog/README.md`, transition that feature row from `🚀 Shipped` → `✅ Completed`.
2. If all feature rows within a minor series (e.g., all of `v0.7.0` through `v0.7.N`) are now `✅ Completed`, `git mv docs/backlog/v0.7.x.md docs/backlog/completed/` — only at the level of the whole minor series, never a single feature.
3. No version bump, no commit required on its own — the closeout typically rides along with the next feature's first ship.

Do not initiate closeout inside `/ship`; leave it as an explicit request the user makes when they're ready.
