# 25 — CLI structural pass: ui renderer split, helper param-objects, command-family split

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** interface · **Suggested executor:** Opus · **Effort:** M · **ROI:** med · **Risk:** low-med · **Status:** Not Started

## Problem

Audit verdict on the CLI seeds first: `playlist_commands.py` (1,487 lines) and `workflow_commands.py` (1,207) are **wide, not deep** — 37 and 25 small functions, near-zero complexity violations; the "one file per command group" convention (.claude/rules/cli-patterns.md) explains the size. Three genuine items remain:

1. **`ui.py:_display_table_result` (line 71)** — the CLI's only triple-PLR function (14 branches, 52 statements, 16 locals): summary table + track-details table + dynamic metric columns + fresh/cached styling in one body.
2. **Two mega-arg helpers:** `cli_helpers.py:184` `run_import_with_progress` (10 args) and `:722` `run_schedule_command` (11 args) — the CLI's two real PLR0913 offenders that aren't Typer command signatures.
3. **`playlist_commands.py` carries five distinct command families** in one file: core CRUD (lines 41-112), manual editing (113-186, v0.8.11), links (628-859), Spotify browse/import/refresh (860-1167), assignments (1168+). Each family has its own `_impl`/`_async` helper cluster.

## Why it matters

Maintainer: (1) is the shared result renderer every command funnels through; (2) are called from every import/schedule command; (3) means a links change and an assignments change collide in the same 1,487-line file. User: none — identical CLI surface.

## Proposed change

1. Split `_display_table_result` along its table boundaries: `_render_summary_table(result)`, `_render_track_details_table(result)` (owning metric columns + fresh/cached styling). Behavior-identical output.
2. Frozen attrs param objects for the two helpers (e.g. `ImportProgressSpec`, `ScheduleCommandSpec`); update their call sites (`git grep run_import_with_progress\|run_schedule_command`).
3. Convert `playlist_commands.py` to a package `interface/cli/playlist/` with `crud.py`, `editing.py`, `links.py`, `spotify.py`, `assignments.py`, and `__init__.py` exposing the single `app = typer.Typer(...)` with all commands registered — `app.py` registration unchanged, `mixd playlist …` surface identical. Amend `.claude/rules/cli-patterns.md`'s "one file per command group" line to permit a package per group when families warrant (propose wording in the PR). Apply the same split to `workflow_commands.py` only if its families are as cleanly separable — judge on inspection; skipping it is fine (note why).

## Blast radius & behavior-preservation

CLI output must be byte-comparable (tests assert output substrings). Typer command names/options unchanged. Test files patch at command-module call sites (`src.interface.cli.playlist_commands.resolve_track_ref` style) — update patch targets to the new module paths in the same pass.

## Test plan

Existing: `uv run pytest tests/unit/interface/cli/` — CliRunner suites assert exit codes + output; they are the characterization net. Update patch paths only; no assertions change.

## Guardrails (do not skip)

- **Clean break:** `playlist_commands.py` deleted after the split; no stub module.
- **Grep gate:** `git grep 'playlist_commands'` returns nothing when done (app.py + tests updated).
- **Layer flow:** commands keep calling use cases via `run_async(execute_use_case(...))` only.
- **Green:** `uv run pytest` stays green; patch-path updates are not weakening.
- **Ratchet:** ui.py's 3 PLR hits and cli_helpers' 2 PLR0913 hits should clear; Typer command signatures (sync_commands:40, track_commands:266, workflow_commands:672) legitimately need many CLI options — see the ratchet map in spoke 26 for how PLR0913 handles them.
- **Scope discipline:** don't restructure `interactive_menu.py` / `progress_provider.py`; not audited as debt.

## Notes / counter-proposal

The command-family split (item 3) is the optional third of this spoke — items 1–2 stand alone if the user prefers minimal churn.
