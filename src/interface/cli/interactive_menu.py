"""Reusable interactive menu pattern for CLI commands.

Eliminates duplication of the Panel → options → Prompt.ask() → dispatch
pattern used by history, likes, and workflow command menus.
"""

from collections.abc import Callable

from attrs import define
from rich.panel import Panel
from rich.prompt import Prompt

from src.interface.cli.console import get_console

console = get_console()

_EXIT_CHOICES = ("", "q", "quit", "exit", "cancel")


@define(frozen=True, slots=True)
class MenuOption:
    """A single option in an interactive menu.

    Attributes:
        key: Numeric key displayed to user (e.g., "1").
        aliases: Alternative text inputs that select this option (e.g., ["lastfm"]).
        label: Rich-formatted display text for the option.
        handler: Function to call when this option is selected.
    """

    key: str
    aliases: list[str]
    label: str
    handler: Callable[[], None]


def run_interactive_menu(
    title: str,
    subtitle: str,
    options: list[MenuOption],
    *,
    pre_menu: Callable[[], None] | None = None,
) -> None:
    """Display a Rich Panel menu, prompt for selection, and dispatch.

    Args:
        title: Bold title shown in the Panel border.
        subtitle: Descriptive text inside the Panel.
        options: Ordered list of menu options.
        pre_menu: Optional callback to display extra info before the options list.
    """
    console.print(
        Panel.fit(
            subtitle,
            title=f"[bold blue]{title}[/bold blue]",
            border_style="blue",
        )
    )

    if pre_menu:
        pre_menu()

    # Display numbered options
    for opt in options:
        console.print(f"  [cyan]{opt.key}[/cyan]. {opt.label}")

    # Build choices: numbers + aliases + exit terms
    choices: list[str] = []
    dispatch: dict[str, Callable[[], None]] = {}
    for opt in options:
        choices.append(opt.key)
        dispatch[opt.key] = opt.handler
        for alias in opt.aliases:
            choices.append(alias)
            dispatch[alias] = opt.handler

    choices.extend(_EXIT_CHOICES)

    max_key = max((int(o.key) for o in options if o.key.isdigit()), default=0)
    prompt_text = f"Select option [1-{max_key}]" if max_key else "Select option"

    choice = Prompt.ask(
        prompt_text,
        choices=choices,
        default="",
        show_choices=False,
    ).strip()

    if choice in _EXIT_CHOICES:
        return

    handler = dispatch.get(choice)
    if handler:
        handler()
