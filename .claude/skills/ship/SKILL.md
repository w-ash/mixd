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

## Step 0: Pre-flight checks

- Run `git status` to see the current state. If there are unexpected untracked or modified files beyond implementation work, warn the user before proceeding.
- Read `docs/backlog/README.md` to identify the current version and next target (first `🔨 In Progress` or `🔜 Not Started` row).
- Read `pyproject.toml` to check the current version.
- If **all** stories in the target version are already checked `[x]` AND the README matrix already shows `✅ Completed`, stop and report: "This version is already shipped."

## Step 1: Reconcile implementation against backlog

- From the README version matrix, find the version being shipped (first `🔨 In Progress` row, or first `🔜 Not Started` if none are in progress).
- Read the corresponding `docs/backlog/v0.X.x.md` file.
- Review `git log` since the last shipped version and `git status` to understand what was actually implemented.
- Compare the actual changes against each unchecked `- [ ]` story in the backlog:
  - **Clearly done**: The implementation fully satisfies the story's "What" — check it off.
  - **Partially done or done differently**: Update the story's Notes field to describe what was actually delivered and how it differs from the plan. Check it off only if the intent was met, even if the approach changed.
  - **Not touched**: Leave unchecked.
- If implementation substantially deviates from the plan (new scope, skipped stories, different architecture), stop and report the discrepancies to the user before making any backlog changes. Wait for instructions.

## Step 2: Update backlog stories

In `docs/backlog/v0.X.x.md`, for each story confirmed as done:
- Check the box: `- [ ]` → `- [x]`
- Set `Status: Completed (YYYY-MM-DD)` with today's date
- Update Notes if the implementation differed from the original plan — the backlog should reflect what actually shipped, not just what was planned

## Step 3: Update the roadmap

In `docs/backlog/README.md`:
- Only update the version matrix row to `✅ Completed` if **all** stories in that version are now checked `[x]`. If unchecked stories remain, leave the row as `🔨 In Progress`.
- If the version is fully complete:
  - Update the `**Current Version**:` header line to the newly completed version number
  - Update the `**Next**:` header line to the next `🔜 Not Started` version from the matrix

## Step 3b: Archive completed version file

If **all** stories in the entire `v0.X.x.md` file (across all sub-versions) are now checked `[x]`:
- Move the file from `docs/backlog/` to `docs/backlog/completed/` using `git mv`
- This only applies when the whole minor series is done (e.g., all of v0.6.0–v0.6.5), not just one sub-version

## Step 4: Bump the version

Compare the completed version number against the current `version` in `pyproject.toml`:
- If the completed version is **higher** than pyproject.toml's version → bump pyproject.toml to match
- If the completed version is **equal to or lower** than pyproject.toml's version → skip the bump (already at or past this version)

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

Create a git commit following the conventions from the git log above. The message should be a concise summary with the version tag. Include the Co-Authored-By trailer.

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
