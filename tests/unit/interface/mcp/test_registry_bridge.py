"""The registry → MCP metadata bridge: annotations, exposure, schema shaping.

Pure functions over the shared ``TOOLS`` tuple — no SDK transport, no DB. These
lock the D4 derivation the v0.9.3 plan fixes: annotations from ``kind``, the
exposed-set filter, and the confirm-field augmentation on write schemas.
"""

from src.application.tools.registry import TOOLS
from src.interface.mcp.exposure import mcp_exposure
from src.interface.mcp.server import (
    _CONFIRM_PROPERTIES,
    _to_mcp_tool,
    exposed_specs,
    mcp_annotations,
)


class TestAnnotations:
    def test_read_tool_is_readonly_idempotent_closed_world(self) -> None:
        spec = next(s for s in TOOLS if s.kind == "read")
        ann = mcp_annotations(spec)
        assert ann.read_only_hint is True
        assert ann.destructive_hint is False
        assert ann.idempotent_hint is True
        assert ann.open_world_hint is False

    def test_write_tool_is_destructive_not_readonly(self) -> None:
        spec = next(s for s in TOOLS if s.kind == "write")
        ann = mcp_annotations(spec)
        assert ann.read_only_hint is False
        assert ann.destructive_hint is True
        assert ann.idempotent_hint is False
        assert ann.open_world_hint is False

    def test_every_exposed_tool_has_derivable_annotations(self) -> None:
        # No exception, and read/write hints are mutually exclusive per tool.
        for spec in exposed_specs():
            ann = mcp_annotations(spec)
            assert ann.read_only_hint is not ann.destructive_hint


class TestExposure:
    def test_reads_and_sync_writes_exposed(self) -> None:
        read = next(s for s in TOOLS if s.kind == "read")
        sync_write = next(
            s for s in TOOLS if s.kind == "write" and not s.launches_operation
        )
        assert mcp_exposure(read) == "exposed"
        assert mcp_exposure(sync_write) == "exposed"

    def test_agentic_and_long_ops_not_exposed(self) -> None:
        agentic = next(s for s in TOOLS if s.kind == "agentic")
        long_op = next(s for s in TOOLS if s.launches_operation)
        assert mcp_exposure(agentic) == "agentic"
        assert mcp_exposure(long_op) == "pending_tasks"

    def test_exposed_specs_excludes_agentic_and_long_ops(self) -> None:
        names = {s.name for s in exposed_specs()}
        assert "code_execution" not in names
        assert "delegate_analysis" not in names
        assert "tool_search_tool_bm25" not in names
        assert "run_workflow" not in names  # launches_operation
        assert "import_data" not in names  # launches_operation
        # And it is exactly the 'exposed' partition of the registry.
        assert names == {s.name for s in TOOLS if mcp_exposure(s) == "exposed"}

    def test_exposed_set_is_nonempty_with_reads_and_writes(self) -> None:
        kinds = {s.kind for s in exposed_specs()}
        assert kinds == {"read", "write"}


class TestSchemaAugmentation:
    def test_write_schema_gains_confirm_fields(self) -> None:
        spec = next(s for s in TOOLS if s.kind == "write" and not s.launches_operation)
        tool = _to_mcp_tool(spec)
        props = tool.input_schema.get("properties", {})
        assert "confirm" in props
        assert "confirm_token" in props
        # Original properties are preserved alongside the injected ones.
        for original_key in dict(spec.input_schema).get("properties", {}):
            assert original_key in props

    def test_read_schema_untouched(self) -> None:
        spec = next(s for s in TOOLS if s.kind == "read")
        tool = _to_mcp_tool(spec)
        props = tool.input_schema.get("properties", {})
        assert "confirm" not in props
        assert "confirm_token" not in props

    def test_confirm_properties_are_declared(self) -> None:
        assert set(_CONFIRM_PROPERTIES) == {"confirm", "confirm_token"}
