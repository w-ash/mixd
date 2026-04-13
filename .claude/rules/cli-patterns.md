# CLI Command Rules

Mixd CLI commands are a thin Typer shell over application use cases. They parse arguments, resolve references, call the use case, and render results — no business logic.

## Module Layout

- One file per command group in `src/interface/cli/` (e.g. `preference_commands.py`, `tag_commands.py`).
- Each file exposes `app = typer.Typer(help=…)` + `@app.command(name=…)` functions.
- Register in `src/interface/cli/app.py` via `app.add_typer(module.app, name="preference", help=…, rich_help_panel="🎵 Track Operations")`.

## Arguments

- `<id_or_search>`-style arguments are `str`, passed through `resolve_track_ref(ref, user_id=...)` → `Track`. Never accept raw `UUID` arguments unless the command genuinely requires an exact ID (merge, admin).
- `--playlist <name_or_id>` → `resolve_playlist_ref(ref, user_id=...)` → `Playlist`. Same UUID-first-then-name-match pattern.
- Enum-like string options (preference state, tag action) go through a `validate_*` helper that raises `typer.BadParameter`. Never use plain `if x not in VALID: raise typer.Exit(1)`.
- Date / path options reuse `parse_date_string`, `validate_file_path`, etc. from `cli_helpers.py`.

## Error handling

- Invalid argument → `typer.BadParameter("reason")` → Typer prints a clean one-liner and exits 2. No stack trace.
- Domain `NotFoundError` → catch in the command, `console.print(f"[red]Error: {e}[/red]")`, `raise typer.Exit(1) from e`.
- Database errors → `handle_cli_error(e, context)` (already handles classification via `classify_database_error`).
- Unknown exceptions → let the top-level handler in `app.py:main()` print the classified message. Don't swallow.

## Output

- **Primary output** (the command's result) → `get_console()` with Rich formatting. Green for success, dim for no-ops, cyan for data rows.
- **Errors** → `get_error_console()` so they hit stderr and don't pollute piped stdout.
- **Track listings** → `render_tracks_table(tracks, title=..., extra_columns=[...])`. Don't hand-roll a Table when columns are Title/Artist/ID + feature-specific extras.
- **Batch summaries** → `BatchOperationResult(succeeded, skipped, failed)` + `render_batch_summary(result, title=...)`. Three-row `Succeeded / Skipped / Failed` table with `Total`. Print the summary table AFTER any per-item errors have been logged to `get_error_console()`.
- **Progress** → `brand_status("…")` context manager for single operations; `progress_coordination_context()` for multi-stage imports.

## Tests

- Use `typer.testing.CliRunner` — invoke via `app` from `src.interface.cli.app` (the real registration path, not the subcommand module directly).
- Patch `resolve_track_ref` / `resolve_playlist_ref` / `run_async` at the command-module call site (e.g. `src.interface.cli.preference_commands.resolve_track_ref`) when you want to avoid a database fixture.
- Assert on three things: exit code, relevant output substring, and mock call kwargs. Always assert `"Traceback" not in result.output` when testing error paths.
- Test files live at `tests/unit/interface/cli/test_<command_group>_commands.py`.

## What NOT to do

- Don't parse UUIDs by hand (`UUID(track_id)`) in command bodies — a typo becomes a stack trace. Use `resolve_track_ref`.
- Don't duplicate state / tag validation — use the shared `validate_*` helper so the error message is identical across commands.
- Don't build ad-hoc Tables for track listings — extend `render_tracks_table` with `extra_columns` instead.
- Don't aggregate batch results as a dict of lists — use `BatchOperationResult` so tests can assert on the typed shape.
- Don't `print()` to stdout — always go through the `console` singletons so tests can capture and users get styling.
