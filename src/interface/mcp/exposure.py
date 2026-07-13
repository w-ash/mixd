"""The single classifier for which registry tools the MCP server exposes.

Kept SDK-free (imports only ``ToolSpec``) so the capability-matrix generator can
import it without pulling the MCP SDK, and so ``server.py``'s ``exposed_specs``
and the generated matrix stay in lockstep — one source of truth for the MCP
coverage column.

v0.9.3 now-slice: read + synchronous write tools are ``exposed``. ``agentic``
tools are chat-executor concerns (the MCP client brings its own loop). The five
long-running ``launches_operation`` writes are ``pending_tasks`` — they wait for
the gated Tasks-extension epic (they need an interface-layer operation launcher
that is currently FastAPI-coupled).
"""

from typing import Literal

from src.application.tools.registry import ToolSpec

type McpExposure = Literal["exposed", "agentic", "pending_tasks"]


def mcp_exposure(spec: ToolSpec) -> McpExposure:
    """Classify one registry tool's MCP exposure from its ``kind`` alone."""
    if spec.kind == "agentic":
        return "agentic"
    if spec.launches_operation:
        return "pending_tasks"
    return "exposed"
