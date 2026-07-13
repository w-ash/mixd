"""Low-level MCP server built from the shared tool registry.

Verified against the installed ``mcp==2.0.0b1`` / ``mcp-types==2.0.0b1`` (v2,
stateless core). RE-VERIFY on the 2026-07-28 stable-v2 bump — the low-level API
below is beta:

- Types live in the separate ``mcp_types`` package (v2 removed ``mcp.types``):
  ``Tool``, ``ToolAnnotations``, ``TextContent``, ``CallToolResult``,
  ``ListToolsResult``, ``CallToolRequestParams``, ``PaginatedRequestParams``.
  ``Tool(input_schema=…)`` serialises to the ``inputSchema`` wire key; annotation
  fields serialise to ``readOnlyHint`` etc. via alias.
- ``mcp.server.lowlevel.Server`` dropped v1's ``@server.list_tools()`` /
  ``@server.call_tool()`` decorators. Handlers register via
  ``add_request_handler(method, params_type, handler)`` where the handler is
  ``async (ServerRequestContext, params) -> BaseModel | dict | None``. Method
  strings + params types match the SDK's own table: ``"tools/list"`` →
  ``PaginatedRequestParams``, ``"tools/call"`` → ``CallToolRequestParams``.
- ``mcp.server.stdio.stdio_server()`` yields ``(read, write)`` streams;
  ``Server.run(read, write, server.create_initialization_options())`` owns the
  initialize handshake. stdout is the JSON-RPC channel — logging goes to stderr
  (see ``mcp_commands.serve``).

The low-level ``Server`` (not the high-level ``MCPServer``) is used deliberately:
mixd's tools are data — a ``TOOLS`` tuple carrying hand-built JSON schemas —
whereas ``MCPServer.add_tool(fn)`` infers a schema from a Python function.
Iterating the registry into ``list_tools``/``call_tool`` is the faithful fit and
what the v0.9.3 plan prescribes.
"""

from collections.abc import Mapping
import json

from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp_types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)

from src.application.chat.protocols import ToolContext
from src.application.tools.registry import TOOLS, ToolSpec, execute_tool
from src.config import get_logger
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import ToolExecutionError
from src.interface.mcp.confirmation import handle_write_call
from src.interface.mcp.exposure import mcp_exposure

logger = get_logger(__name__)

SERVER_NAME = "mixd"

# Optional properties injected into every exposed write tool's schema so a client
# can drive the two-phase confirmation in-band. The base schemas set
# ``additionalProperties: false``, so these must be declared as real properties.
_CONFIRM_PROPERTIES: dict[str, JsonValue] = {
    "confirm": {
        "type": "boolean",
        "description": (
            "Omit (or false) to preview the change and receive a confirm_token. "
            "Set true — with the confirm_token from that preview and the same "
            "arguments — to commit."
        ),
    },
    "confirm_token": {
        "type": "string",
        "description": "The confirm_token returned by a prior preview call.",
    },
}


def mcp_annotations(spec: ToolSpec) -> ToolAnnotations:
    """Derive MCP tool annotations from the registry ``kind``.

    Fixed formula (annotations are untrusted client-side hints — mutation safety
    lives in the in-band confirmation gate, never here): reads are read-only and
    idempotent; writes are (blanket) destructive; mixd acts only on the user's
    own library, so the world is closed.
    """
    is_read = spec.kind == "read"
    return ToolAnnotations(
        read_only_hint=is_read,
        destructive_hint=spec.kind == "write",
        idempotent_hint=is_read,
        open_world_hint=False,
    )


def exposed_specs() -> list[ToolSpec]:
    """Registry tools exposed over MCP in the now-slice.

    Read + synchronous write tools only. ``agentic`` tools are chat-executor
    concerns (the client brings its own loop); ``launches_operation`` writes
    wait for the gated Tasks-extension epic. The ``mcp_exposure`` classifier is
    the single source of truth shared with the capability matrix.
    """
    return [spec for spec in TOOLS if mcp_exposure(spec) == "exposed"]


def _to_mcp_tool(spec: ToolSpec) -> Tool:
    """Build the MCP ``Tool`` for a registry spec, augmenting write schemas."""
    schema: dict[str, JsonValue] = dict(spec.input_schema)
    if spec.kind == "write":
        existing = schema.get("properties")
        properties: dict[str, JsonValue] = (
            dict(existing) if isinstance(existing, Mapping) else {}
        )
        properties.update(_CONFIRM_PROPERTIES)
        schema["properties"] = properties
    return Tool(
        name=spec.name,
        description=spec.description,
        input_schema=schema,
        annotations=mcp_annotations(spec),
    )


def _text_result(payload: JsonValue, *, is_error: bool = False) -> CallToolResult:
    """Wrap a JSON-serialisable result as a single-text-block CallToolResult."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return CallToolResult(
        content=[TextContent(type="text", text=text)], is_error=is_error
    )


async def _handle_list_tools(
    _context: ServerRequestContext[object, PaginatedRequestParams],
    _params: PaginatedRequestParams,
) -> ListToolsResult:
    """Return every exposed tool. No pagination — the registry is small (~30)."""
    return ListToolsResult(tools=[_to_mcp_tool(spec) for spec in exposed_specs()])


def _build_call_handler(user_id: str):
    """Bind the acting user id into the tools/call handler (stateless per call)."""
    exposed = {spec.name: spec for spec in exposed_specs()}

    async def _handle_call_tool(
        _context: ServerRequestContext[object, CallToolRequestParams],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        ctx = ToolContext(user_id=user_id)
        arguments: dict[str, JsonValue] = dict(params.arguments or {})
        spec = exposed.get(params.name)
        if spec is None:
            return _text_result(
                {"error": f"Unknown tool: {params.name}"}, is_error=True
            )
        try:
            if spec.kind == "write":
                result = await handle_write_call(spec, arguments, ctx)
            else:
                result = await execute_tool(spec.name, arguments, ctx)
        except ToolExecutionError as e:
            # Actionable error as a tool result (the model self-corrects in-turn),
            # not a protocol error.
            return _text_result({"error": str(e)}, is_error=True)
        return _text_result(result)

    return _handle_call_tool


def build_server(user_id: str) -> Server[object]:
    """Assemble the MCP server bound to ``user_id`` (identity from the env)."""
    server: Server[object] = Server(SERVER_NAME)
    server.add_request_handler("tools/list", PaginatedRequestParams, _handle_list_tools)
    server.add_request_handler(
        "tools/call", CallToolRequestParams, _build_call_handler(user_id)
    )
    return server


async def serve_stdio(user_id: str) -> None:
    """Run the MCP server over stdio until the client disconnects."""
    from mcp.server.stdio import stdio_server

    server = build_server(user_id)
    logger.info(
        "mcp_server_start",
        user_id=user_id,
        tool_count=len(exposed_specs()),
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )
