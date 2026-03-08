# CLI with Typer + Rich

Patterns for building production CLI tools: Typer app structure, async bridging, interactive menus, Rich progress displays, and the same use case delegation used by FastAPI.

---

## App Structure

### Root App with Subcommands

```python
# src/interface/cli/app.py
import typer
from typing import Annotated

app = typer.Typer(
    help="My Project CLI",
    no_args_is_help=True,
    rich_markup_mode="rich",       # Enable [bold], [cyan], etc. in help text
    add_completion=False,
    pretty_exceptions_enable=True,
)
```

### Initialization Callback

The `@app.callback()` runs before every command — use it for global setup:

```python
@app.callback()
def init_cli(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    setup_logging(verbose)
    Path("data").mkdir(exist_ok=True)
```

### Subcommand Registration

Organize commands into domain-specific modules, each with its own `Typer` app:

```python
def _register_commands() -> None:
    """Lazy imports avoid circular dependencies."""
    from src.interface.cli import history_commands, workflow_commands

    app.add_typer(
        workflow_commands.app,
        name="workflow",
        help="Execute and manage workflows",
        rich_help_panel="Workflow Execution",
    )
    app.add_typer(
        history_commands.app,
        name="history",
        help="Import and manage history",
        rich_help_panel="Data Management",
    )

_register_commands()
```

---

## Async Bridge

Typer commands are synchronous, but your application layer is async. Bridge the gap with a simple runner:

```python
# src/interface/cli/async_runner.py
import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Bridge sync CLI → async application layer."""
    async def _run_with_executor() -> T:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=8))
        return await coro

    return asyncio.run(_run_with_executor())
```

**Usage in commands**:
```python
@app.command()
def sync_data() -> None:
    result = run_async(execute_use_case(
        lambda uow: SyncDataUseCase(uow).execute()
    ))
    display_result(result)
```

This uses the same `execute_use_case()` runner as FastAPI — zero business logic duplication between CLI and web. See [FastAPI Backend](fastapi-backend.md) for the runner pattern.

---

## Command Modules

Each domain gets its own module with a local Typer app:

```python
# src/interface/cli/workflow_commands.py
import typer

app = typer.Typer(
    help="Execute and manage workflows",
    no_args_is_help=False,        # Allow bare invocation for interactive mode
    rich_markup_mode="rich",
)
```

### Progressive Discovery

Use `invoke_without_command=True` to show an interactive browser when called with no subcommand:

```python
@app.callback(invoke_without_command=True)
def workflow_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _show_interactive_browser()

@app.command()
def run(workflow_id: str | None = None) -> None:
    """Execute a specific workflow."""
    if workflow_id is None:
        workflow_id = _prompt_for_selection()
    _execute_workflow(workflow_id)
```

This supports both interactive users (`my-app workflow` → browser) and automation (`my-app workflow run --workflow-id=my-flow`).

---

## Interactive Menus

A reusable menu component eliminates Panel → Prompt → dispatch boilerplate:

```python
from attrs import define

@define(frozen=True, slots=True)
class MenuOption:
    key: str               # "1", "2", etc.
    aliases: list[str]     # ["lastfm", "last.fm"]
    label: str             # Rich markup: "[bold]Last.fm[/bold] — Import history"
    handler: Callable[[], None]


def run_interactive_menu(
    *,
    title: str,
    subtitle: str,
    options: list[MenuOption],
) -> None:
    console = get_console()
    console.print(Panel.fit(f"[bold]{title}[/bold]\n{subtitle}"))

    for opt in options:
        console.print(f"  [{opt.key}] {opt.label}")

    choice = Prompt.ask("Select", choices=[o.key for o in options] + [a for o in options for a in o.aliases])
    selected = next(o for o in options if choice == o.key or choice in o.aliases)
    selected.handler()
```

**Usage**:
```python
run_interactive_menu(
    title="Data Import",
    subtitle="Import your listening history",
    options=[
        MenuOption(
            key="1",
            aliases=["lastfm"],
            label="[bold]Last.fm[/bold] — Import scrobbles",
            handler=_import_lastfm,
        ),
        MenuOption(
            key="2",
            aliases=["spotify"],
            label="[bold]Spotify[/bold] — Import streaming history",
            handler=_import_spotify,
        ),
    ],
)
```

---

## Console Management

A global singleton console ensures consistent terminal width detection:

```python
# src/interface/cli/console.py
from rich.console import Console

_console: Console | None = None

def get_console() -> Console:
    global _console
    if _console is None:
        _console = Console()
    return _console
```

For error output, use a separate stderr console:

```python
_err_console: Console | None = None

def get_err_console() -> Console:
    global _err_console
    if _err_console is None:
        _err_console = Console(stderr=True)
    return _err_console
```

---

## Rich Output Patterns

### Result Tables

```python
from rich.table import Table

def display_result(result: OperationResult) -> None:
    console = get_console()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column(style="green bold")

    for metric in result.metrics:
        table.add_row(metric.label, _format_value(metric.value))

    console.print(table)
```

### Styled Panels

```python
from rich.panel import Panel

console.print(Panel.fit(
    f"[bold green]Success[/bold green] — Processed {count} items",
    border_style="green",
))
```

### Dim Styling for Secondary Data

```python
# Fresh data in bold, cached data dimmed
style = "bold" if item.is_fresh else "dim"
table.add_row(f"[{style}]{item.name}[/{style}]", str(item.count))
```

---

## Progress Display

For long-running operations, use Rich's `Progress` + `Live` for coordinated terminal output:

```python
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn


async def run_with_progress(operations: list[Operation]) -> None:
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        refresh_per_second=10,
    )

    with Live(progress, refresh_per_second=10, redirect_stdout=True):
        for op in operations:
            task_id = progress.add_task(op.name, total=op.total_items)

            async for item in op.execute():
                progress.update(task_id, advance=1)

            progress.update(task_id, description=f"[green]✓[/green] {op.name}")
```

**Sub-operation indenting**: Display child operations as `  ↳ Description` for visual hierarchy.

---

## Error Handling

```python
import typer
from typing import Never

def handle_cli_error(e: Exception, message: str) -> Never:
    err_console = get_err_console()
    err_console.print(f"[red]Error: {message}: {e}[/red]")
    raise typer.Exit(1) from e
```

**Usage in commands**:
```python
@app.command()
def import_data(source: str) -> None:
    try:
        result = run_async(_do_import(source))
        display_result(result)
    except ConnectionError as e:
        handle_cli_error(e, f"Failed to connect to {source}")
```

---

## Helper Utilities

Common patterns that appear across command modules:

### Date Parsing with Validation

```python
from datetime import UTC, datetime

def parse_date_string(date_str: str | None, field_name: str = "date") -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        get_err_console().print(f"[red]Invalid {field_name} format: {date_str}. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1) from None
```

### Confirmation Prompts

```python
if not typer.confirm(f"This will process {count} items. Continue?"):
    raise typer.Abort()
```

---

## Key Design Principles

- **Commands are thin** — 10-20 lines of parameter handling + delegation
- **Same use case runner** as FastAPI — `execute_use_case()` shared across CLI and web
- **Progressive discovery** — interactive menus for humans, direct flags for automation
- **Consistent Rich styling** — `[bold]`, `[cyan]`, `[dim]` for visual hierarchy
- **Proper exit codes** — `typer.Exit(1)` for errors, not bare exceptions
