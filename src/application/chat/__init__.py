"""Chat-assistant application layer (agentic workspace, v0.9.x).

The in-app assistant's orchestration lives here: the agentic tool loop, the
system-prompt composition, untrusted-content wrapping, pending-action store,
and the tool dispatchers. Tools themselves are declared in
``src.application.tools.registry`` — the single capability surface shared with
the (future) MCP server. This layer depends only on the ``LLMClientProtocol``,
never on the Anthropic SDK.
"""
