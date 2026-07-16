"""Unit tests for chat SSE terminal-line formatting.

The in-stream error path must map known exceptions to their shared code and
hide internals behind INTERNAL_ERROR for unmapped ones (R1), mirroring what the
HTTP error envelope does — no ``str(exc)`` leak over the wire.
"""

import json

from src.domain.exceptions import ChatUnavailableError
from src.interface.api.chat_sse import _terminal_line


def _payload(line: str) -> dict[str, object]:
    assert line.startswith("data: ")
    return json.loads(line[len("data: ") :])


class TestTerminalLine:
    def test_done_when_no_exception(self) -> None:
        assert _payload(_terminal_line(None)) == {"type": "done"}

    def test_mapped_exception_keeps_message(self) -> None:
        body = _payload(_terminal_line(ChatUnavailableError("no key configured")))
        assert body["type"] == "error"
        assert body["code"] == "CHAT_UNAVAILABLE"
        assert body["message"] == "no key configured"

    def test_unmapped_exception_hides_internals(self) -> None:
        secret = "psycopg: password=hunter2 host=internal-db"
        body = _payload(_terminal_line(RuntimeError(secret)))
        assert body["code"] == "INTERNAL_ERROR"
        # The raw exception text must not leak — generic message only.
        assert body["message"] == "An internal error occurred"
        assert "hunter2" not in body["message"]
