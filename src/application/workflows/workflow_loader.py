"""Shared workflow definition loader for CLI and API.

Parses JSON workflow files into typed WorkflowDef entities. Used by both
the CLI (workflow_commands.py) and FastAPI routes to load definitions
from the same directory without duplicating parsing logic.
"""

# pyright: reportAny=false
# Legitimate Any: JSON parsing produces heterogeneous dicts

import json
from pathlib import Path
from typing import cast

from src.config.logging import get_logger
from src.domain.entities.shared import JsonValue
from src.domain.entities.workflow import WorkflowDef, parse_workflow_def

logger = get_logger(__name__)


def get_definitions_dir() -> Path:
    """Get the path to the workflow definitions directory."""
    return Path(__file__).parent / "definitions"


def load_workflow_def(path: Path) -> WorkflowDef:
    """Parse a JSON workflow file into a typed WorkflowDef.

    Args:
        path: Path to a .json workflow definition file.

    Returns:
        Typed WorkflowDef with all tasks parsed.

    Raises:
        FileNotFoundError: If the path does not exist.
        json.JSONDecodeError: If the file contains invalid JSON.
        KeyError/TypeError: If required fields are missing or malformed.
    """
    raw: dict[str, JsonValue] = cast(
        dict[str, JsonValue], json.loads(path.read_text(encoding="utf-8"))
    )
    raw.setdefault("id", path.stem)
    raw.setdefault("name", "Unknown")
    return parse_workflow_def(raw)


def list_workflow_defs(definitions_dir: Path | None = None) -> list[WorkflowDef]:
    """Load all workflow definitions from a directory.

    Args:
        definitions_dir: Directory containing .json workflow files.
            Defaults to the built-in definitions directory.

    Returns:
        List of parsed WorkflowDef entities, skipping files that fail to parse.
    """
    directory = definitions_dir or get_definitions_dir()
    if not directory.exists():
        return []

    workflows: list[WorkflowDef] = []
    for json_file in directory.glob("*.json"):
        try:
            workflows.append(load_workflow_def(json_file))
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Skipping {json_file.name}: {e}")
            continue

    return workflows
