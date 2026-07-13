"""Mixd's MCP server — the second consumer of the shared tool registry.

``src/application/tools/registry.py`` is the single capability surface. The
in-app chat executor drives it today; this package exposes the same ``TOOLS``
tuple over the Model Context Protocol (stdio) so any MCP-aware client (Claude
Desktop, Cursor, Claude Code) can read from and act on a user's mixd library.

The server is pure transport: it derives tool metadata + annotations from the
registry and dispatches through the existing ``execute_tool`` /
``execute_confirmed_action`` paths, inheriting RLS tenant-scoping and two-phase
mutation confirmation unchanged. It adds no tool logic of its own.

Scope (v0.9.3 now-slice): read + synchronous ``write`` tools. ``agentic`` tools
(sandbox, subagent, tool search) are chat-executor concerns and are filtered
out — an MCP client brings its own agentic loop. The five long-running
``launches_operation`` writes wait for the gated Tasks-extension epic (they need
an interface-layer operation launcher that is currently FastAPI-coupled).
"""
