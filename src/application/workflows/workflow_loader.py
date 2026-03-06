"""Shared workflow definition loader for CLI and API.

Parses JSON workflow files into typed WorkflowDef entities. Used by both
the CLI (workflow_commands.py) and FastAPI routes to load definitions
from the same directory without duplicating parsing logic.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: JSON parsing produces heterogeneous dicts

import json
from pathlib import Path
from typing import Any

from src.config.logging import get_logger
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

logger = get_logger(__name__)


def get_definitions_dir() -> Path:
    """Get the path to the workflow definitions directory."""
    return Path(__file__).parent / "definitions"


def _parse_task(t: dict[str, Any]) -> WorkflowTaskDef:
    """Parse a single task dict from JSON into a typed WorkflowTaskDef."""
    return WorkflowTaskDef(
        id=str(t["id"]),
        type=str(t["type"]),
        config=dict(t.get("config", {})),
        upstream=list(t.get("upstream", [])),
        result_key=t.get("result_key"),
    )


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
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    raw_tasks: list[dict[str, Any]] = raw.get("tasks", [])
    tasks = [_parse_task(t) for t in raw_tasks]

    return WorkflowDef(
        id=str(raw.get("id", path.stem)),
        name=str(raw.get("name", "Unknown")),
        description=str(raw.get("description", "")),
        version=str(raw.get("version", "1.0")),
        tasks=tasks,
    )


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
