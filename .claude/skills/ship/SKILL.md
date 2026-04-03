---
name: ship
description: Ship a completed backlog version — reconcile implementation against stories, update backlog, bump version, sync toolchain, stage, and commit. Use after all implementation for a version is done.
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

## Step 6: Stage and commit

- List the files that will be staged (backlog files, pyproject.toml, uv.lock, web/openapi.json, web/src/api/generated/*, plus any unstaged implementation files).
- Stage files by explicit name — never `git add -A` or `git add .`.
- Create a git commit following the conventions from the git log above. The message should be a concise summary with the version tag. Include the Co-Authored-By trailer.
- If the commit fails due to a pre-commit hook (e.g., ruff reformatted files), re-stage the affected files and retry the commit. Do NOT use `--no-verify`.

## Step 7: Tag and deploy

After the commit succeeds:

1. Create the version tag: run `git tag v{VERSION}` (e.g., `git tag v0.6.6`).
2. Let the user know what's ready and how to deploy when they choose to:

> Version is committed and tagged locally. When you're ready to deploy, run `deploy` in your terminal.

### What `deploy` does

`deploy` is a shell function (defined in `~/.zshrc`) that runs:

```bash
git push origin main --tags && sleep 3 && gh run watch <latest-run-id>
```

1. Pushes the commit and tag to origin together
2. The `v*` tag triggers `.github/workflows/release.yml`, which runs two jobs:
   - **GitHub Release**: generates changelog via git-cliff, creates a GitHub Release
   - **Deploy to Fly.io**: `flyctl deploy --remote-only` with `BUILD_HASH=$GITHUB_SHA`
3. Fly.io runs `alembic upgrade head` as the release command (before switching traffic)
4. Health check at `/api/v1/health` gates the traffic cutover
5. `gh run watch` streams all of this in the user's terminal
