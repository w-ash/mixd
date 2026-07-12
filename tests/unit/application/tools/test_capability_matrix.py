"""Freshness + well-formedness of the generated agent capability matrix.

``docs/web-ui/capability-matrix.md`` is generated from the tool registry by
``scripts/generate_capability_matrix.py``. A hand-synced mirror of generatable
truth decays, so this test regenerates the content in-memory and asserts the
checked-in file matches byte-for-byte — a use-case or tool change that isn't
regenerated fails CI. It also guards the table's shape: every data row has all
four columns and no blank cells.
"""

from pathlib import Path

from scripts.generate_capability_matrix import _OUTPUT_PATH, build_matrix

_DISPOSITIONS = {
    "covered",
    "blacklisted",
    "mechanically-excluded",
    "internal",
    "not-yet-covered",
}


def test_checked_in_matrix_is_up_to_date() -> None:
    generated = build_matrix()
    on_disk = _OUTPUT_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/web-ui/capability-matrix.md is stale — regenerate with "
        "`uv run python scripts/generate_capability_matrix.py`"
    )


def test_output_path_is_the_documented_location() -> None:
    assert (
        Path(__file__).resolve().parents[4] / "docs" / "web-ui" / "capability-matrix.md"
    ) == _OUTPUT_PATH


def _capability_rows(content: str) -> list[list[str]]:
    """The capability table's data rows (between its header and the next section)."""
    lines = content.splitlines()
    start = lines.index(
        "| Capability (use case) | Chat tool | Disposition | Rationale |"
    )
    rows: list[list[str]] = []
    for line in lines[start + 2 :]:  # skip header + separator
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def test_capability_table_is_well_formed() -> None:
    rows = _capability_rows(build_matrix())
    assert rows, "expected at least one capability row"
    for row in rows:
        assert len(row) == 4, f"row must have 4 columns: {row}"
        assert all(cell for cell in row), f"no blank cells allowed: {row}"
        _name, tool, disposition, rationale = row
        assert disposition in _DISPOSITIONS, f"bad disposition: {disposition}"
        if disposition == "covered":
            assert tool != "—", f"covered row must name a tool: {row}"
            assert rationale == "—", f"covered row has no rationale: {row}"
        else:
            assert tool == "—", f"excluded row has no tool: {row}"
            assert rationale != "—", f"excluded row must give a rationale: {row}"


def test_tools_table_lists_every_registered_tool() -> None:
    from src.application.tools.registry import TOOLS

    content = build_matrix()
    for spec in TOOLS:
        assert f"| {spec.name} | {spec.kind} |" in content, (
            f"tool {spec.name} missing from the tools table"
        )
