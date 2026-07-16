"""The D4 bidirectional-parity contract, enforced in CI.

Every ``*UseCase`` class in ``src/application/use_cases`` must be classified in
exactly one bucket: covered by a ToolSpec, blacklisted (human-only),
mechanically excluded (no chat channel), internal plumbing, or not-yet-covered.
A new use case that lands in none of these buckets fails here — so "anything a
human can do, the agent can do" can't silently rot as features ship.

The test also guards the registry's own quality invariants (unique names,
when-to-call descriptions, valid input schemas, write-tool confirmation).
"""

import ast
from pathlib import Path
import re

import pytest

from src.application.tools import registry
from src.application.tools.registry import (
    BLACKLISTED_USE_CASES,
    INTERNAL_USE_CASES,
    MECHANICALLY_EXCLUDED_USE_CASES,
    NOT_YET_COVERED,
    TOOLS,
)
import src.application.use_cases as use_cases_pkg

_WHEN_TO_CALL = re.compile(r"^(Call|Use|When)\b")


def _discover_use_case_classes() -> set[str]:
    """AST-scan the use_cases package for ``*UseCase`` class definitions.

    Uses ``ast`` rather than importing so discovery has no import side effects
    and matches exactly what a maintainer sees when grepping for the classes.
    """
    root = Path(use_cases_pkg.__file__).parent
    names: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("UseCase"):
                names.add(node.name)
    return names


def _covered_use_cases() -> set[str]:
    return {uc for spec in TOOLS for uc in spec.use_cases}


# --- Parity contract -------------------------------------------------------


def test_every_use_case_is_classified_exactly_once() -> None:
    discovered = _discover_use_case_classes()
    covered = _covered_use_cases()
    buckets = {
        "covered": covered,
        "blacklisted": set(BLACKLISTED_USE_CASES),
        "mechanically_excluded": set(MECHANICALLY_EXCLUDED_USE_CASES),
        "internal": set(INTERNAL_USE_CASES),
        "not_yet_covered": set(NOT_YET_COVERED),
    }

    # Every discovered use case is classified.
    classified = set().union(*buckets.values())
    unclassified = discovered - classified
    assert not unclassified, f"Unclassified use cases (add to a bucket): {unclassified}"

    # No bucket references a use case that no longer exists.
    stale = classified - discovered
    assert not stale, f"Stale entries — no such use case: {stale}"

    # Buckets are pairwise disjoint: exactly one classification each.
    names = list(buckets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            overlap = buckets[a] & buckets[b]
            assert not overlap, f"Use case in both {a} and {b}: {overlap}"


def test_not_yet_covered_is_empty() -> None:
    # v0.9.1 closed the parity contract: every use case is covered or explicitly
    # excluded. NOT_YET_COVERED must stay empty — a new use case that needs a
    # tool must grow one (or an exclusion), not sit in a backlog set.
    assert frozenset() == NOT_YET_COVERED, (
        f"Parity is closed — cover these or add them to an exclusion bucket: "
        f"{set(NOT_YET_COVERED)}"
    )


# --- Registry quality invariants -------------------------------------------


def test_tool_names_are_unique() -> None:
    names = [spec.name for spec in TOOLS]
    assert len(names) == len(set(names)), f"Duplicate tool names: {names}"


def test_descriptions_lead_with_a_when_to_call_sentence() -> None:
    for spec in TOOLS:
        # Server tools (dispatch is None: code_execution, tool_search) carry a raw
        # {type, name} schema and their description is never sent to the API, so
        # the when-to-call standard doesn't apply. Dispatched tools — including
        # agentic delegate_analysis — are model-facing and must comply.
        if spec.dispatch is None:
            continue
        assert len(spec.description) >= 50, f"{spec.name}: description too short"
        assert _WHEN_TO_CALL.match(spec.description), (
            f"{spec.name}: description must lead with a when-to-call sentence "
            f"(Call/Use/When…), got: {spec.description[:40]!r}"
        )


def test_input_schemas_are_valid_object_schemas() -> None:
    for spec in TOOLS:
        # Server tools carry a raw {type, name} server-tool block, not a JSON
        # Schema; only dispatched tools have a validatable object schema.
        if spec.dispatch is None:
            continue
        schema = spec.input_schema
        assert schema.get("type") == "object", f"{spec.name}: not an object schema"
        assert schema.get("additionalProperties") is False, (
            f"{spec.name}: input_schema must set additionalProperties: false"
        )


def test_non_agentic_tools_have_a_dispatcher() -> None:
    for spec in TOOLS:
        if spec.kind == "agentic":
            continue
        assert spec.dispatch is not None, f"{spec.name}: missing dispatcher"


def test_write_tools_carry_a_confirmation_path() -> None:
    # Every write commits after confirmation via exactly one path: a synchronous
    # ``executor`` or a long-running ``launches_operation`` (interface launcher).
    for spec in TOOLS:
        if spec.kind == "write":
            assert (spec.executor is not None) ^ spec.launches_operation, (
                f"{spec.name}: a write tool needs exactly one of executor / "
                "launches_operation"
            )


def test_build_tools_stamps_cache_breakpoints_per_tier() -> None:
    # Two-tier caching: the invariant prefix always carries a breakpoint; a page
    # that promotes tools adds a second on the promoted segment. No page promotes
    # nothing-then-something, so the count is exactly 1 (pageless / bare page) or
    # 2 (a promoting page). A breakpoint must never land on a server tool — a raw
    # {type, name} block rejects cache_control and would 400 the request.
    for enable in (True, False):
        pageless = registry.build_tools(enable_code_execution=enable)
        assert len([t for t in pageless if "cache_control" in t]) == 1

        promoting = registry.build_tools(enable_code_execution=enable, page="workflows")
        breakpoints = [t for t in promoting if "cache_control" in t]
        assert len(breakpoints) == 2, "prefix + promoted segment each cache-stamped"
        for bp in breakpoints:
            # A dispatched {name, description, input_schema} wrapper, never a raw
            # {type, name} server-tool block.
            assert "input_schema" in bp, f"cache breakpoint on a server tool: {bp}"

    # Dispatched tools carry the {name, description, input_schema} wrapper; server
    # tools emit their raw {type, name} block instead.
    for t in pageless:
        if "type" in t:
            assert {"type", "name"} <= t.keys(), f"malformed server tool: {t}"
        else:
            assert {"name", "description", "input_schema"} <= t.keys()


def test_tool_search_and_agentic_tools_are_never_deferred() -> None:
    by_name = {s.name: s for s in TOOLS}
    # A deferred search tool could never be found — it must always load.
    assert by_name["tool_search_tool_bm25"].defer_loading is False
    # Agentic capabilities are never deferred (the model under-reaches for them
    # otherwise) — also enforced at construction, asserted here as the contract.
    for spec in TOOLS:
        if spec.kind == "agentic":
            assert spec.defer_loading is False, spec.name


def test_hot_set_stays_small_and_nonempty() -> None:
    # Accuracy degrades past ~10 upfront tools; keep the loaded set well under
    # that, and never empty (the API 400s "All tools have defer_loading set").
    # Every page must respect the ceiling, since page routing promotes tools.
    pages = [None, *registry._PAGE_TOOL_HINTS]
    for enable in (True, False):
        for page in pages:
            loaded = [
                t
                for t in registry.build_tools(enable_code_execution=enable, page=page)
                if "defer_loading" not in t
            ]
            assert loaded, "at least one tool must always be non-deferred"
            names = [t.get("name") or t.get("type") for t in loaded]
            assert len(loaded) <= 10, f"too many upfront tools on {page!r}: {names}"


def test_page_routing_keeps_the_prefix_invariant() -> None:
    # A page's hinted tools become loaded (no defer_loading); off-page they stay
    # deferred. Crucially the cached PREFIX — the tools up to and including the
    # FIRST breakpoint — must be identical across pages, so navigation never busts
    # the core cache. Promoted tools ride their own segment past that breakpoint.
    def cached_prefix(page: str | None) -> list[str]:
        tools = registry.build_tools(page=page)
        cut = next(i for i, t in enumerate(tools) if "cache_control" in t)
        return [str(t.get("name") or t.get("type")) for t in tools[: cut + 1]]

    baseline = cached_prefix(None)
    # The demoted flagship workflow tools must NOT be in the invariant prefix.
    assert "generate_workflow_def" not in baseline
    assert "describe_node" not in baseline
    for page in registry._PAGE_TOOL_HINTS:
        assert cached_prefix(page) == baseline, f"page {page!r} shifted the cache"

    loaded_names = {
        str(t["name"])
        for t in registry.build_tools(page="playlists")
        if "defer_loading" not in t and "name" in t
    }
    assert {"query_playlists", "query_playlist_links"} <= loaded_names
    # The same tools are deferred when the user is elsewhere.
    off_page = registry.build_tools(page="library")
    deferred = {t.get("name") for t in off_page if t.get("defer_loading")}
    assert "query_playlist_links" in deferred


def test_workflow_page_promotes_the_demoted_flagship_tools() -> None:
    # describe_node / generate_workflow_def are deferred globally (reclaiming a hot
    # slot on the 4 non-workflow pages) but promoted back into the loaded set on
    # the workflows page, where the generation flow needs them upfront.
    on_page = {
        str(t["name"])
        for t in registry.build_tools(page="workflows")
        if "defer_loading" not in t and "name" in t
    }
    assert {"describe_node", "generate_workflow_def"} <= on_page

    off_page = registry.build_tools(page="library")
    deferred = {t.get("name") for t in off_page if t.get("defer_loading")}
    assert {"describe_node", "generate_workflow_def"} <= deferred


def test_page_hint_map_is_valid_and_bounded() -> None:
    # The import-time validator already ran (importing registry would have failed
    # otherwise); assert the invariants it guards, and that it actually rejects
    # the three drift modes rather than passing vacuously.
    registry._validate_page_hints()  # current map is well-formed
    assert registry._MAX_PROMOTED_PER_PAGE >= 1
    for page, names in registry._PAGE_TOOL_HINTS.items():
        assert page in registry._CANONICAL_PAGES
        assert len(names) <= registry._MAX_PROMOTED_PER_PAGE
        for name in names:
            spec = registry._SPECS_BY_NAME[name]
            assert spec.defer_loading
            assert spec.kind == "read"

    # Drift modes the validator must catch (patch the module map, expect ValueError).
    original = registry._PAGE_TOOL_HINTS
    bad_maps = [
        {"not_a_page": ("query_stats",)},  # unknown page key
        {"library": ("no_such_tool",)},  # unknown tool name
        {"library": ("save_workflow",)},  # a write tool, not a deferred read
        {"library": tuple(["query_stats"] * (registry._MAX_PROMOTED_PER_PAGE + 1))},
    ]
    try:
        for bad in bad_maps:
            registry._PAGE_TOOL_HINTS = bad  # type: ignore[assignment]
            with pytest.raises(ValueError):
                registry._validate_page_hints()
    finally:
        registry._PAGE_TOOL_HINTS = original  # type: ignore[assignment]


def test_read_tools_are_sandbox_callable_only_when_enabled() -> None:
    # allowed_callers is stamped uniformly on read tools by build_tools (not a
    # ToolSpec field); write and server tools never get it, so mutations stay
    # two-phase and the sandbox can't invoke them.
    read_names = {s.name for s in TOOLS if s.kind == "read"}

    enabled = {t["name"]: t for t in registry.build_tools(enable_code_execution=True)}
    for name, tool in enabled.items():
        if name in read_names:
            assert tool.get("allowed_callers") == ["direct", "code_execution_20260120"]
        else:
            assert "allowed_callers" not in tool
    assert "code_execution" in enabled

    disabled = {
        t.get("name"): t for t in registry.build_tools(enable_code_execution=False)
    }
    assert "code_execution" not in disabled
    assert all("allowed_callers" not in t for t in disabled.values())
