#!/usr/bin/env python3
"""Generate the agent-parity capability matrix (docs/web-ui/capability-matrix.md).

The parity contract (D4) says: anything a human can do through the app, the
in-app AI assistant can do too — and nothing more. That contract is enforced in
code by ``tests/unit/application/tools/test_registry_parity.py``, but the
enforcement is invisible unless you read the test. This script renders it as a
human-auditable table: every ``*UseCase`` class in ``src/application/use_cases``,
the chat tool that covers it (or the exclusion bucket that accounts for it), and
the rationale for each exclusion.

The output is GENERATED — never hand-edit ``docs/web-ui/capability-matrix.md``.
A freshness test (``tests/unit/application/tools/test_capability_matrix.py``)
regenerates the content in-memory and fails CI if the checked-in file drifts, so
a use-case or tool change that isn't regenerated is caught.

Usage:
    uv run python scripts/generate_capability_matrix.py
"""

import ast
from operator import itemgetter
from pathlib import Path

from src.application.tools.registry import (
    BLACKLISTED_USE_CASES,
    INTERNAL_USE_CASES,
    MECHANICALLY_EXCLUDED_USE_CASES,
    NOT_YET_COVERED,
    TOOLS,
)
import src.application.use_cases as use_cases_pkg
from src.interface.mcp.exposure import mcp_exposure

# Human-readable MCP-exposure cell per tool (v0.9.3). Derived from the same
# ``mcp_exposure`` classifier the stdio server uses, so the doc can't drift from
# what the server actually exposes.
_MCP_LABEL: dict[str, str] = {
    "exposed": "exposed",
    "agentic": "chat-only (agentic)",
    "pending_tasks": "chat-only (pending Tasks)",
}

_OUTPUT_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "web-ui" / "capability-matrix.md"
)

# Per-use-case exclusion rationale, extracted from the classification comments in
# ``registry.py``. The buckets are small and stable; a short hand-authored line
# per excluded use case reads better than parsing free-form comments. Any newly
# excluded use case that lands here without a rationale falls back to the
# bucket's default (see ``_BUCKET_DEFAULT_RATIONALE``) — still auditable, just
# terser — and the freshness test keeps the doc in sync regardless.
_RATIONALE: dict[str, str] = {
    # Blacklisted (human-only by product decision).
    "RecordChatFeedbackUseCase": (
        "Feedback about the assistant comes from the human thumbs UI only — the "
        "agent must never file feedback on itself."
    ),
    # Mechanically excluded (no chat file channel).
    "ExportLastFmLikesUseCase": (
        "Produces a file export; chat has no file input/output channel."
    ),
    # Internal plumbing (no direct human surface; reached via workflows/tools).
    "ExecuteWorkflowRunUseCase": (
        "Workflow-run executor driven by RunWorkflowUseCase / the scheduler, "
        "never called standalone."
    ),
    "GetOperationSnapshotUseCase": (
        "Frontend SSE-watchdog fallback keyed on an ephemeral operation_id; the "
        "agent reads run status via query_operations instead."
    ),
    "EnrichTracksUseCase": (
        "Enricher-node step of the workflow engine; the agent enriches by "
        "generating and running a workflow with an enricher node."
    ),
    "MatchAndIdentifyTracksUseCase": (
        "Internal enrich/import step needing a live connector API; reached via "
        "the same workflow path, no direct human surface."
    ),
    "CreateConnectorPlaylistUseCase": (
        "Workflow destination.* capability built only by the engine; the agent "
        "creates a connector playlist by running a workflow with that destination."
    ),
    "UpdateConnectorPlaylistUseCase": (
        "Workflow destination.* capability built only by the engine; the agent "
        "updates a connector playlist by running a workflow with that destination."
    ),
}

_BUCKET_DEFAULT_RATIONALE: dict[str, str] = {
    "blacklisted": "Human-only by product decision (D4).",
    "mechanically-excluded": "No chat channel for this capability.",
    "internal": "Engine/pipeline plumbing with no direct human surface.",
    "not-yet-covered": "Classified but not yet covered by a tool.",
}

_EM_DASH = "—"


def _discover_use_case_classes() -> set[str]:
    """AST-scan the use_cases package for ``*UseCase`` class definitions.

    Mirrors ``_discover_use_case_classes`` in the parity test: an ``ast`` scan
    (not an import) so discovery has no side effects and matches exactly what a
    maintainer sees when grepping for the classes.
    """
    root = Path(use_cases_pkg.__file__).parent
    names: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("UseCase"):
                names.add(node.name)
    return names


def _tool_by_use_case() -> dict[str, str]:
    """Map each covered use-case name to the chat tool that exposes it."""
    mapping: dict[str, str] = {}
    for spec in TOOLS:
        for uc in spec.use_cases:
            mapping[uc] = spec.name
    return mapping


def _first_sentence(text: str) -> str:
    """First sentence of a tool description, collapsed to one line."""
    collapsed = " ".join(text.split())
    head, _, _ = collapsed.partition(". ")
    return head if head.endswith(".") else f"{head}."


def _disposition(name: str, tool_by_uc: dict[str, str]) -> tuple[str, str, str]:
    """Return ``(tool_or_dash, disposition, rationale)`` for one use case."""
    if name in tool_by_uc:
        return tool_by_uc[name], "covered", _EM_DASH
    if name in BLACKLISTED_USE_CASES:
        bucket = "blacklisted"
    elif name in MECHANICALLY_EXCLUDED_USE_CASES:
        bucket = "mechanically-excluded"
    elif name in INTERNAL_USE_CASES:
        bucket = "internal"
    elif name in NOT_YET_COVERED:
        bucket = "not-yet-covered"
    else:
        bucket = "unclassified"
    rationale = _RATIONALE.get(name, _BUCKET_DEFAULT_RATIONALE.get(bucket, _EM_DASH))
    return _EM_DASH, bucket, rationale


def build_matrix() -> str:
    """Render the full capability-matrix Markdown document as a string."""
    tool_by_uc = _tool_by_use_case()
    rows = [
        (name, *_disposition(name, tool_by_uc)) for name in _discover_use_case_classes()
    ]
    # Sort by disposition, then use-case name — deterministic, stable output.
    rows.sort(key=itemgetter(2, 0))

    covered = sum(1 for r in rows if r[2] == "covered")
    excluded = len(rows) - covered

    lines = [
        "<!-- GENERATED by scripts/generate_capability_matrix.py — do not edit."
        " Run `uv run python scripts/generate_capability_matrix.py` to regenerate."
        " -->",
        "",
        "# Agent Capability Matrix",
        "",
        "The **parity contract (D4)**: anything a user can do through the web UI",
        "or CLI, the in-app AI assistant can do too — and nothing more. Every",
        "application use case below is either covered by a chat tool or accounted",
        "for in an exclusion bucket (blacklisted, mechanically excluded, or",
        "internal plumbing). This table is generated from",
        "`src/application/tools/registry.py` and enforced by",
        "`tests/unit/application/tools/test_registry_parity.py`.",
        "",
        f"**{len(rows)} capabilities: {covered} covered, {excluded} excluded.**",
        "",
        "| Capability (use case) | Chat tool | Disposition | Rationale |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {name} | {tool} | {disposition} | {rationale} |"
        for name, tool, disposition, rationale in rows
    )

    lines.extend([
        "",
        "## Chat tools",
        "",
        "The tools the assistant calls, in registry (prompt) order. The **MCP**",
        "column is what the `mixd mcp serve` stdio server exposes to external",
        "clients (v0.9.3): read + synchronous write tools are exposed; agentic",
        "tools and long-running (operation-launching) writes stay chat-only for",
        "now — the latter pending the gated Tasks-extension epic.",
        "",
        "| Tool | Kind | MCP | Description |",
        "| --- | --- | --- | --- |",
    ])
    lines.extend(
        f"| {spec.name} | {spec.kind} | {_MCP_LABEL[mcp_exposure(spec)]} "
        f"| {_first_sentence(spec.description)} |"
        for spec in TOOLS
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    content = build_matrix()
    _OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(f"Wrote {_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
