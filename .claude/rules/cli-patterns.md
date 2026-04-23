---
paths:
  - "src/interface/cli/**"
---
# CLI Command Rules

Mixd CLI commands are a thin Typer shell over application use cases: parse arguments, resolve references, call the use case, render results. Zero business logic.

## Module Layout

- One file per command group in `src/interface/cli/` (e.g. `preference_commands.py`, `tag_commands.py`).
- Each file exposes `app = typer.Typer(help=…)` + `@app.command(name=…)` functions.
- Register in `src/interface/cli/app.py` via `app.add_typer(module.app, name="preference", help=…, rich_help_panel="🎵 Track Operations")`.

## Arguments

- `<id_or_search>` arguments are `str`, resolved via `resolve_track_ref(ref, user_id=...)` → `Track`. Use raw `UUID` only for commands that genuinely need an exact ID (merge, admin).
- `--playlist <name_or_id>` resolves via `resolve_playlist_ref(ref, user_id=...)` → `Playlist` (UUID-first, then name match).
- Enum-like options (preference state, tag action) go through a shared `validate_*` helper that raises `typer.BadParameter`. Reusing the helper keeps the error message identical across commands.
- Date / path options use `parse_date_string`, `validate_file_path`, etc. from `cli_helpers.py`.

## Error handling

- Invalid argument → `typer.BadParameter("reason")` → Typer prints a clean one-liner, exit 2. No stack trace.
- Domain `NotFoundError` → catch in the command, `console.print(f"[red]Error: {e}[/red]")`, `raise typer.Exit(1) from e`.
- Database errors → `handle_cli_error(e, context)` (already classifies via `classify_database_error`).
- Unknown exceptions → let the top-level handler in `app.py:main()` print the classified message.

## Output

- **Primary output** → `get_console()` with Rich formatting. Green for success, dim for no-ops, cyan for data rows.
- **Errors** → `get_error_console()` (stderr — keeps piped stdout clean).
- **Track listings** → `render_tracks_table(tracks, title=..., extra_columns=[...])`. Extend with `extra_columns` for feature-specific data.
- **Batch summaries** → `BatchOperationResult(succeeded, skipped, failed)` + `render_batch_summary(result, title=...)`. Print summary AFTER per-item errors have been logged to `get_error_console()`.
- **Progress** → `brand_status("…")` for single operations; `progress_coordination_context()` for multi-stage imports.
- All output flows through the `console` singletons so tests can capture and users get styling.

## Tests

- Use `typer.testing.CliRunner` invoked via `app` from `src.interface.cli.app` (the real registration path).
- Patch `resolve_track_ref` / `resolve_playlist_ref` / `run_async` at the command-module call site (e.g. `src.interface.cli.preference_commands.resolve_track_ref`) to skip the database fixture.
- Assert exit code, relevant output substring, and mock call kwargs. Always assert `"Traceback" not in result.output` on error paths.
- Test files: `tests/unit/interface/cli/test_<command_group>_commands.py`.
