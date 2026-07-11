"""Shared tool registry — the chat assistant's capability surface.

One declaration per capability (``ToolSpec``), consumed by the in-app chat
executor now and the MCP server in v0.9.3. The parity contract (D4) lives here
too: every application use case is classified in exactly one bucket, enforced
in CI, so "anything a human can do, the agent can do" can't silently rot.
"""
