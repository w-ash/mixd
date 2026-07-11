"""Chat stream event types — yielded by the chat use case.

These map to the 7 SSE event types the frontend consumes. The two server-tool
(code execution) events are emitted only once the sandbox is enabled in v0.9.2;
the vocabulary ships whole from day one so the protocol never churns.
"""

from attrs import define

from src.domain.entities.shared import JsonDict, JsonValue


@define(frozen=True, slots=True)
class TextDelta:
    """Incremental text from the LLM."""

    text: str


@define(frozen=True, slots=True)
class ToolStartEvent:
    """Emitted when the model invokes a tool."""

    name: str
    tool_use_id: str


@define(frozen=True, slots=True)
class ToolResultEvent:
    """Emitted after a tool executes."""

    name: str
    tool_use_id: str
    summary: JsonValue
    is_error: bool = False


@define(frozen=True, slots=True)
class ServerToolStartEvent:
    """Emitted when the API starts a server-side tool (code execution, v0.9.2)."""

    name: str
    tool_use_id: str
    input: JsonDict


@define(frozen=True, slots=True)
class ServerToolResultEvent:
    """Emitted when a server-side code execution finishes (v0.9.2)."""

    tool_use_id: str
    stdout: str
    stderr: str
    return_code: int
