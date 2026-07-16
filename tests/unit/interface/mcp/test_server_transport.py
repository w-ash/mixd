"""End-to-end transport: a real MCP ClientSession over in-memory streams.

Stands up the mixd server and drives it through the SDK's own client, so the
handler wiring, annotation serialisation, and the injected confirm/confirm_token
fields are exercised over the actual JSON-RPC protocol — not just called
in-process. DB-backed dispatch is stubbed (``execute_tool`` /
``execute_confirmed_action``); the propose path is the real, DB-free one.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from uuid import uuid4

import anyio
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
import pytest

from src.domain.entities.shared import JsonValue
from src.interface.mcp import confirmation, server
from tests.fixtures import InMemoryPendingActionStore


@asynccontextmanager
async def _connected_client() -> AsyncIterator[ClientSession]:
    """Yield an initialised client session wired to the mixd server."""
    built = server.build_server("default")
    async with create_client_server_memory_streams() as (
        client_streams,
        server_streams,
    ):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                lambda: built.run(
                    server_read, server_write, built.create_initialization_options()
                )
            )
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                yield session
            tg.cancel_scope.cancel()


def _text(result: object) -> JsonValue:
    """Parse the single text block of a CallToolResult back to JSON."""
    content = result.content  # type: ignore[attr-defined]
    return json.loads(content[0].text)


class TestListTools:
    async def test_lists_exposed_tools_only(self) -> None:
        async with _connected_client() as session:
            listed = await session.list_tools()
        names = {t.name for t in listed.tools}
        assert names == {s.name for s in server.exposed_specs()}
        for hidden in ("code_execution", "delegate_analysis", "run_workflow"):
            assert hidden not in names

    async def test_annotations_and_confirm_fields_ride_the_wire(self) -> None:
        async with _connected_client() as session:
            listed = await session.list_tools()
        by_name = {t.name: t for t in listed.tools}
        read = by_name["query_library"]
        write = by_name["manage_tags"]
        assert read.annotations is not None
        assert read.annotations.read_only_hint is True
        assert write.annotations is not None
        assert write.annotations.destructive_hint is True
        assert "confirm" in write.input_schema.get("properties", {})
        assert "confirm_token" in write.input_schema.get("properties", {})


class TestCallTool:
    async def test_read_call_dispatches_and_returns_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_execute(name: str, args: object, ctx: object) -> JsonValue:
            return {"tool": name, "ok": True}

        monkeypatch.setattr(server, "execute_tool", _fake_execute)
        async with _connected_client() as session:
            result = await session.call_tool("query_library", {"view": "search"})
        assert _text(result) == {"tool": "query_library", "ok": True}

    async def test_user_data_tags_stripped_from_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Dispatchers wrap user-originated strings in <user_data> tags as a
        # chat-side prompt-injection defense. MCP clients are untaught, so the
        # tags must not reach them — the server strips them before the wire (K2).
        async def _fake_execute(name: str, args: object, ctx: object) -> JsonValue:
            return {"name": "<user_data>Playlist</user_data>", "ok": True}

        monkeypatch.setattr(server, "execute_tool", _fake_execute)
        async with _connected_client() as session:
            result = await session.call_tool("query_library", {"view": "search"})
        raw = result.content[0].text  # type: ignore[attr-defined]
        assert "<user_data>" not in raw
        assert "</user_data>" not in raw
        assert _text(result) == {"name": "Playlist", "ok": True}

    async def test_unknown_tool_returns_error_result(self) -> None:
        async with _connected_client() as session:
            result = await session.call_tool("does_not_exist", {})
        assert result.is_error
        assert "Unknown tool" in _text(result)["error"]  # type: ignore[index]

    async def test_write_two_phase_over_the_wire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fresh = InMemoryPendingActionStore()
        monkeypatch.setattr(confirmation, "pending_action_store", fresh)
        monkeypatch.setattr(
            "src.application.chat.dispatchers._common.pending_action_store", fresh
        )

        async def _fake_commit(action: object, user_id: str) -> JsonValue:
            return {"status": "confirmed", "description": "done"}

        monkeypatch.setattr(confirmation, "execute_confirmed_action", _fake_commit)

        args = {"operation": "batch_tag", "track_ids": [str(uuid4())], "tag": "jazz"}
        async with _connected_client() as session:
            preview = _text(await session.call_tool("manage_tags", dict(args)))
            assert preview["status"] == "needs_confirmation"  # type: ignore[index]

            committed = _text(
                await session.call_tool(
                    "manage_tags",
                    {
                        **args,
                        "confirm": True,
                        "confirm_token": preview["confirm_token"],
                    },  # type: ignore[index]
                )
            )
        assert committed["status"] == "confirmed"  # type: ignore[index]
