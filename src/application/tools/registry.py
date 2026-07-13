"""Single source of truth for the chat assistant's capability surface.

Every chat tool is declared here as a ``ToolSpec`` binding its LLM-facing
description and JSON input schema to its dispatcher (and, for write tools, its
confirmed-mutation executor) and to the application use cases it exposes. The
API tool list, the tool dispatch, and the confirmation dispatch are all derived
from ``TOOLS`` — there is no second place to keep in sync. The (future) MCP
server is the second consumer of the same tuple.

The parity contract (D4): anything a human can do through the app, the agent
can do — and nothing more. The classification sets below make that contract
explicit and testable (``tests/unit/application/tools/test_registry_parity.py``):
every ``*UseCase`` class in ``src/application/use_cases`` must be reachable from
a ``ToolSpec`` or accounted for as blacklisted (human-only), mechanically
excluded (no chat channel), internal plumbing, or not-yet-covered (v0.9.1's
job). A CI test fails on any unclassified class, so the contract can't decay as
features ship.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Literal, cast

from attrs import define

from src.application.chat import confirmed_actions, subagent, tool_executor
from src.application.chat.dispatchers import (
    assignments_write,
    connector_playlists_write,
    library,
    links,
    links_write,
    long_ops,
    matches_write,
    operations,
    playlists,
    playlists_write,
    preferences_write,
    schedules,
    stats,
    tags,
    tags_write,
    tracks_write,
    workflows_read,
    workflows_write,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import (
    OperationLauncher,
    ToolContext,
    ToolDispatch,
)
from src.application.chat.user_data import strip_user_data
from src.application.chat.workflow_schema import build_workflow_def_schema
from src.config.settings import settings
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import ToolExecutionError

type ToolKind = Literal["read", "write", "agentic"]
# A confirmed mutation executor: the claimed pending action + acting user id.
type ConfirmedExecutor = Callable[[PendingAction, str], Awaitable[JsonValue]]

# Tool-definition mappings from each dispatcher module (v0.9.1 parity coverage).
# Order is load-bearing — it fixes the prompt-tool order, so append new modules'
# SPECS at the end. Listed explicitly (not by iterating module objects) so each
# ``SPECS`` keeps its ``list[dict[str, object]]`` type instead of collapsing to
# ``Any`` under a heterogeneous module union.
_DISPATCHER_SPECS_LISTS: tuple[list[dict[str, object]], ...] = (
    # Read tools (Epic 1)
    library.SPECS,
    tags.SPECS,
    playlists.SPECS,
    links.SPECS,
    stats.SPECS,
    operations.SPECS,
    workflows_read.SPECS,
    schedules.SPECS,
    # Write tools (Epic 2 — two-phase confirmation)
    tracks_write.SPECS,
    matches_write.SPECS,
    tags_write.SPECS,
    preferences_write.SPECS,
    playlists_write.SPECS,
    connector_playlists_write.SPECS,
    links_write.SPECS,
    assignments_write.SPECS,
    workflows_write.SPECS,
    # Long-running operation tools (Epic 3 — launched via OperationLauncher)
    long_ops.SPECS,
)


@define(frozen=True, slots=True)
class ToolSpec:
    """One chat capability: schema + dispatcher + parity accounting.

    ``kind`` is load-bearing: ``read`` tools answer queries; ``write`` tools
    propose two-phase mutations (an ``executor`` is required and runs only after
    the user confirms); ``agentic`` tools are capabilities rather than queries
    (sandbox, delegation) — they may execute server-side, in which case they
    carry no ``dispatch``. Invariants are enforced at construction so an
    inconsistent spec cannot exist past import time.
    """

    name: str
    description: str
    input_schema: Mapping[str, JsonValue]
    dispatch: ToolDispatch | None
    use_cases: tuple[str, ...] = ()
    kind: ToolKind = "read"
    executor: ConfirmedExecutor | None = None
    # A write tool whose confirmed commit is a long-running operation launched by
    # the interface layer (imports, syncs, workflow runs). It carries no
    # application ``executor``; instead the confirm path runs it through the
    # injected ``OperationLauncher`` and returns an ``{operation_id, run_id}``
    # handle. Mutually exclusive with ``executor``.
    launches_operation: bool = False
    # Deferred tools stay out of the upfront prompt until tool search surfaces
    # them. Deferred is the default at this registry size (~40 tools): accuracy
    # degrades past ~10 upfront tools, so only a curated hot set and the agentic
    # capabilities load eagerly; the rest are discovered via tool_search, which
    # appends their schemas (preserving the tools/system prompt cache).
    defer_loading: bool = True

    def __attrs_post_init__(self) -> None:
        if self.kind == "write":
            # A write commits either synchronously (executor) or by launching a
            # long-running operation — exactly one, never both, never neither.
            if (self.executor is not None) == self.launches_operation:
                raise ValueError(
                    f"{self.name}: a write tool needs exactly one of executor / "
                    "launches_operation"
                )
        elif self.executor is not None or self.launches_operation:
            raise ValueError(
                f"{self.name}: only write tools carry an executor or launch operations"
            )
        if self.dispatch is None and self.kind != "agentic":
            raise ValueError(
                f"{self.name}: only agentic server tools may omit a dispatcher"
            )
        if self.kind == "agentic" and self.defer_loading:
            raise ValueError(f"{self.name}: agentic capabilities must not be deferred")


_CORE_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="describe_node",
        description=(
            "Call this before proposing or editing a workflow to confirm which "
            "node types exist and what parameters each takes. Given a node_type "
            "it returns that node's category, purpose, and config fields (with "
            "types, defaults, and select options); omit node_type to list every "
            "available node type by category. Prevents hallucinated node names "
            "and missing required parameters."
        ),
        input_schema=tool_executor.DESCRIBE_NODE_INPUT_SCHEMA,
        dispatch=tool_executor.handle_describe_node,
        use_cases=(),  # static-catalog helper — exposes no application use case
        kind="read",
        defer_loading=False,  # hot set: the workflow-generation flow needs it upfront
    ),
    ToolSpec(
        name="list_user_workflows",
        description=(
            "Call this to see the user's saved workflows — name, description, "
            "task count, and the workflow_id that get_workflow and "
            "save_workflow take. Call it before referring to, updating, or "
            "answering questions about saved workflows so ids and names are "
            "real, never guessed."
        ),
        input_schema=tool_executor.LIST_USER_WORKFLOWS_INPUT_SCHEMA,
        dispatch=tool_executor.handle_list_user_workflows,
        use_cases=("ListWorkflowsUseCase",),
        kind="read",
    ),
    ToolSpec(
        name="get_workflow",
        description=(
            "Call this to fetch one saved workflow's complete definition by "
            "its workflow_id (from list_user_workflows). Use it before "
            "refining an existing workflow so the edit starts from what is "
            "actually saved, and to answer questions about what a specific "
            "workflow does."
        ),
        input_schema=tool_executor.GET_WORKFLOW_INPUT_SCHEMA,
        dispatch=tool_executor.handle_get_workflow,
        use_cases=("GetWorkflowUseCase",),
        kind="read",
    ),
    ToolSpec(
        name="generate_workflow_def",
        description=(
            "Call this with a complete workflow definition whenever you "
            "build or refine a workflow for the user — it validates the "
            "definition against the node catalog and DAG rules and renders "
            "a graph preview the user sees. Always pass the full definition "
            "(every task), never a partial edit. If validation fails you get "
            "the exact failures back; fix them and call again."
        ),
        input_schema=build_workflow_def_schema(),
        dispatch=tool_executor.handle_generate_workflow_def,
        use_cases=(),  # pure validation/preview — persists nothing
        kind="read",
        defer_loading=False,  # hot set: the flagship workflow-generation tool
    ),
    ToolSpec(
        name="validate_workflow_def",
        description=(
            "Use this to check a workflow definition you did not just "
            "generate — one the user pasted, or a saved workflow fetched via "
            "get_workflow — against the node catalog and DAG rules. Returns "
            "findings as data (valid flag, errors, warnings) for you to "
            "report; it does not render a preview or change anything."
        ),
        input_schema=tool_executor.VALIDATE_WORKFLOW_DEF_INPUT_SCHEMA,
        dispatch=tool_executor.handle_validate_workflow_def,
        use_cases=(),  # pure validation — persists nothing
        kind="read",
    ),
    ToolSpec(
        name="save_workflow",
        description=(
            "Call this to propose persisting a workflow definition after a "
            "successful generate_workflow_def — pass the exact definition it "
            "accepted, plus workflow_id when updating an existing workflow "
            "(omit it to create). The save is a proposal: nothing persists "
            "until the user confirms on the card this returns."
        ),
        input_schema=tool_executor.SAVE_WORKFLOW_INPUT_SCHEMA,
        dispatch=tool_executor.handle_save_workflow,
        use_cases=("CreateWorkflowUseCase", "UpdateWorkflowUseCase"),
        kind="write",
        executor=confirmed_actions.exec_save_workflow,
    ),
)


# The code-execution sandbox tool type. Its "input_schema" IS the raw
# server-tool block the API wants ({type, name}); build_tools emits it verbatim.
# `20260120` is the REPL-persistence + programmatic-tool-calling variant (verify
# against the installed anthropic SDK before shipping — couplefins pinned this
# on SDK 0.97; mixd runs a newer SDK).
_CODE_EXECUTION_TOOL_TYPE = "code_execution_20260120"

# Read tools are callable both directly and from the sandbox. Listing both
# callers deviates from the docs' pick-one guidance on purpose: direct calls
# answer one-shot questions without a container spin-up; programmatic calls let
# sandbox code aggregate large results without them entering model context.
# Write and agentic tools NEVER get allowed_callers — mutations stay two-phase
# behind human confirmation, and delegation stays a top-level decision.
_READ_ALLOWED_CALLERS: tuple[str, ...] = ("direct", _CODE_EXECUTION_TOOL_TYPE)

_DELEGATE_ANALYSIS_SCHEMA: Mapping[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "question": {
            "type": "string",
            "description": (
                "The self-contained investigation question for the subagent to "
                "research and answer."
            ),
        },
        "scope": {
            "type": "string",
            "description": (
                "Optional extra constraints (time range, playlists, tags) that "
                "narrow the investigation."
            ),
        },
    },
    "required": ["question"],
}


async def _handle_delegate_analysis(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Run the read-only research subagent and return its dense summary.

    Lives here (not in a dispatcher module) so it can reference ``execute_tool``
    and ``build_subagent_tools`` without the ``registry -> subagent -> use_case``
    chain leading back to the registry. The subagent reuses the injected loop
    executor, so it dispatches through this same registry with fresh context.
    """
    question = tool_input.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ToolExecutionError("delegate_analysis requires a non-empty 'question'")
    scope = tool_input.get("scope")
    scope_str = scope if isinstance(scope, str) and scope.strip() else None
    return await subagent.run_subagent(
        question,
        scope_str,
        ctx,
        tools=build_subagent_tools(),
        execute_fn=execute_tool,
        cfg=settings.chat,
    )


# Agentic tools. Server tools (``dispatch is None``: code_execution) are called
# by the model and executed by the API; the adapter forwards their result blocks
# as server-tool events, and their ``input_schema`` holds the raw {type, name}
# the API expects, not a JSON Schema. ``delegate_analysis`` is agentic but
# dispatched locally — it spawns the read-only research subagent. Placed last so
# the model-facing read/write tools keep their established prompt order
# (reordering invalidates the prompt cache).
_AGENTIC_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="code_execution",
        description=(
            "Server-side Python sandbox for batch computation over the user's "
            "library. The model writes code that calls read tools programmatically; "
            "intermediate results stay in the sandbox and only the aggregate returns "
            "to the conversation."
        ),
        input_schema={"type": _CODE_EXECUTION_TOOL_TYPE, "name": "code_execution"},
        dispatch=None,
        use_cases=(),
        kind="agentic",
        defer_loading=False,  # agentic capabilities are never deferred
    ),
    ToolSpec(
        name="delegate_analysis",
        description=(
            "Use this to delegate a deep, multi-step investigation of the user's "
            "library to a research subagent — 'compare my listening this spring vs "
            "last spring and tell me what changed', 'which starred tracks fell out "
            "of rotation this year and why'. It runs a fresh read-only loop and "
            "returns one dense summary, keeping this conversation uncluttered. Do "
            "NOT call it for questions one or two tools answer directly, and do NOT "
            "call it for arithmetic over known data — use the code sandbox for that."
        ),
        input_schema=_DELEGATE_ANALYSIS_SCHEMA,
        dispatch=_handle_delegate_analysis,
        use_cases=(),
        kind="agentic",
        defer_loading=False,  # agentic capabilities are never deferred
    ),
    ToolSpec(
        name="tool_search_tool_bm25",
        description=(
            "Server-side BM25 search over the deferred tool set. The model calls "
            "it to discover a tool whose schema is not loaded upfront."
        ),
        input_schema={
            "type": "tool_search_tool_bm25_20251119",
            "name": "tool_search_tool_bm25",
        },
        dispatch=None,
        use_cases=(),
        kind="agentic",
        defer_loading=False,  # the search tool itself must always be loaded
    ),
)


def _spec_from_mapping(entry: Mapping[str, object]) -> ToolSpec:
    """Build a ``ToolSpec`` from a dispatcher module's ``SPECS`` mapping.

    The dispatcher packages (``chat/dispatchers/*``) declare their tools as
    plain mappings so they never import this module (which would cycle:
    ``registry -> dispatchers -> registry``). This is the one place those
    mappings become typed specs; ``ToolSpec``'s ``__attrs_post_init__`` still
    validates every field, so a malformed entry fails at import.
    """
    executor = entry.get("executor")
    return ToolSpec(
        name=cast("str", entry["name"]),
        description=cast("str", entry["description"]),
        input_schema=cast("Mapping[str, JsonValue]", entry["input_schema"]),
        dispatch=cast("ToolDispatch", entry["dispatch"]),
        use_cases=cast("tuple[str, ...]", entry.get("use_cases", ())),
        kind=cast("ToolKind", entry.get("kind", "read")),
        executor=cast("ConfirmedExecutor", executor) if executor is not None else None,
        launches_operation=bool(entry.get("launches_operation", False)),
        # Deferred by default (the registry-wide default); a dispatcher opts a
        # hot tool into the upfront set with ``"defer_loading": False``.
        defer_loading=bool(entry.get("defer_loading", True)),
    )


TOOLS: tuple[ToolSpec, ...] = (
    _CORE_TOOLS
    + tuple(
        _spec_from_mapping(entry)
        for specs in _DISPATCHER_SPECS_LISTS
        for entry in specs
    )
    + _AGENTIC_TOOLS
)

_SPECS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOLS}


def _tool_dict(spec: ToolSpec) -> dict[str, object]:
    """The ``{name, description, input_schema}`` wrapper for a dispatched tool."""
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": dict(spec.input_schema),
    }


def _stamp_cache(tools: list[dict[str, object]], idx: int) -> None:
    """Stamp the single ephemeral cache breakpoint on ``tools[idx]``.

    A no-op for ``idx < 0`` (empty list, or no stampable target), so callers can
    pass ``len(tools) - 1`` unconditionally.
    """
    if idx >= 0:
        tools[idx]["cache_control"] = {"type": "ephemeral"}


# --- Page-contextual tool routing (v0.9.2) --------------------------------
#
# Rule-based context routing: the web client sends the coarse UI section the
# user is on, and the deferred read tools relevant to that section are promoted
# into the loaded set. Rule-based (not semantic) keeps it deterministic and
# free — a UI route is the cleanest domain signal there is. Held to <=3 names
# per page so the loaded set stays under the ~10-tool ceiling past which
# selection accuracy degrades (OpenAI's own guidance; corroborated across the
# 2026 progressive-disclosure literature). Promoted tools ride the UNCACHED tail
# (see ``build_tools``), so navigating between pages never busts the cached core.
_PAGE_TOOL_HINTS: Mapping[str, tuple[str, ...]] = {
    "playlists": ("query_playlists", "query_playlist_links"),
    "library": ("query_playlists", "query_stats"),
    "workflows": ("list_user_workflows", "get_workflow", "query_workflow_history"),
    "dashboard": ("query_stats", "query_operations"),
    "imports": ("query_operations",),
}


def _promoted_tool_names(page: str | None) -> frozenset[str]:
    """Deferred read tools to load eagerly for the user's current UI section.

    Unknown or absent pages promote nothing — the surface degrades cleanly to
    the static core plus tool-search discovery.
    """
    return frozenset(_PAGE_TOOL_HINTS.get(page or "", ()))


def build_tools(
    *, enable_code_execution: bool = True, page: str | None = None
) -> list[dict[str, object]]:
    """Anthropic tool list: a cached core prefix followed by an uncached tail.

    The prefix is the always-hot curated core (a handful of reads plus the
    dispatched agentic tools); its last entry carries the single tools-array
    cache breakpoint. The prefix is page-INVARIANT, so navigating between UI
    sections never invalidates it. The tail holds the deferred pool (surfaced on
    demand via tool-search), the raw server-tool blocks (which reject a cache
    stamp), and whatever ``page`` promotes for the current section — all
    loaded-but-uncached, so per-page variation costs only a few re-sent schemas.

    Order within each group follows registry order (deterministic — reordering
    invalidates the cache). ``enable_code_execution`` marks read tools
    sandbox-callable (``allowed_callers``) and exposes the ``code_execution``
    server tool; off, it is dropped and the surface degrades to direct calls.
    Agentic server tools (``dispatch is None``) emit their raw ``{type, name}``
    block verbatim — the API rejects the ``{name, description, input_schema}``
    wrapper for them.
    """
    promoted = _promoted_tool_names(page)
    prefix: list[dict[str, object]] = []  # always-hot, cached, page-invariant
    tail: list[dict[str, object]] = []  # server tools + deferred/promoted pool
    for spec in TOOLS:
        if spec.name == "code_execution" and not enable_code_execution:
            continue
        if spec.dispatch is None and spec.kind == "agentic":
            tail.append(dict(spec.input_schema))  # raw server-tool block
            continue
        tool = _tool_dict(spec)
        if enable_code_execution and spec.kind == "read":
            tool["allowed_callers"] = list(_READ_ALLOWED_CALLERS)
        if not spec.defer_loading:
            prefix.append(tool)  # curated core + dispatched agentic
        elif spec.name in promoted:
            tail.append(tool)  # page-promoted: loaded for this section, uncached
        else:
            tool["defer_loading"] = True
            tail.append(tool)  # deferred: discovered via tool_search
    _stamp_cache(prefix, len(prefix) - 1)
    return prefix + tail


# The research subagent's hot set: the reads an investigation reaches for most.
# Same progressive-disclosure discipline as the main loop — kept under the ~10
# ceiling with the long tail deferred behind tool-search. It matters more here
# than anywhere: the subagent runs at low effort, where a bloated tool list most
# degrades selection.
_SUBAGENT_HOT_TOOLS: frozenset[str] = frozenset({
    "query_library",
    "query_stats",
    "list_tags",
    "query_playlists",
    "query_workflow_history",
    "query_operations",
    "query_schedules",
})


def build_subagent_tools() -> list[dict[str, object]]:
    """Read-only toolset for the ``delegate_analysis`` research subagent.

    The ``read`` slice only — no mutations, no code execution, no nested
    ``delegate_analysis`` (delegation is one level deep), no ``allowed_callers``
    (no sandbox to be called from). A curated hot set loads upfront; the rest of
    the reads defer behind ``tool_search`` so the subagent can still reach them.
    The cache breakpoint sits on the last hot tool; the trailing search tool is
    tiny and rides uncached.
    """
    tool_list: list[dict[str, object]] = []
    last_hot = -1
    for spec in TOOLS:
        if spec.kind != "read":
            continue
        tool = _tool_dict(spec)
        if spec.name in _SUBAGENT_HOT_TOOLS:
            last_hot = len(tool_list)
        else:
            tool["defer_loading"] = True
        tool_list.append(tool)
    tool_list.append(dict(_SPECS_BY_NAME["tool_search_tool_bm25"].input_schema))
    _stamp_cache(tool_list, last_hot)
    return tool_list


async def execute_tool(
    name: str,
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,
) -> JsonValue:
    """Dispatch a tool call to its handler and return a JSON-serializable result.

    The input sanitizer strips ``<user_data>`` tags the model may echo back as
    tool inputs (the single dispatch point) so they can never break lookups or
    persist into stored input.
    """
    spec = _SPECS_BY_NAME.get(name)
    if spec is None:
        raise ToolExecutionError(f"Unknown tool: {name}")
    if spec.dispatch is None:
        raise ToolExecutionError(f"Tool {name!r} executes server-side")
    clean = cast("dict[str, JsonValue]", strip_user_data(dict(tool_input)))
    try:
        return await spec.dispatch(clean, ctx)
    except ToolExecutionError:
        raise
    except Exception as e:
        raise ToolExecutionError(f"Tool {name!r} failed: {e}") from e


async def execute_confirmed_action(
    action: PendingAction,
    user_id: str,
    *,
    operation_launcher: OperationLauncher | None = None,
) -> JsonValue:
    """Execute a confirmed pending mutation.

    Synchronous writes commit through their registered ``executor``. A write that
    launches a long-running operation instead runs through the interface-provided
    ``operation_launcher`` (imports, syncs, workflow runs) and returns the
    ``{operation_id, run_id}`` handle; that path is unavailable (launcher is
    ``None``) outside the FastAPI chat route.
    """
    spec = _SPECS_BY_NAME.get(action.tool_name)
    if spec is None:
        raise ToolExecutionError(f"Unknown mutation tool: {action.tool_name}")
    if spec.launches_operation:
        if operation_launcher is None:
            raise ToolExecutionError(
                f"{action.tool_name} launches a background operation, which is "
                "unavailable in this context"
            )
        return await operation_launcher(action, user_id)
    if spec.executor is None:
        raise ToolExecutionError(f"{action.tool_name} is not a confirmable mutation")
    return await spec.executor(action, user_id)


# --- Parity accounting (asserted by test_registry_parity.py) ---------------

# Human-only by product decision (D4). mixd's broader human-only capabilities
# — connector OAuth/token flows, account management, ``mixd admin reset`` —
# are not application use cases (they live in the connector/interface layer).
# RecordChatFeedbackUseCase is the set's first member: feedback *about the
# assistant* comes from the human thumbs UI only — the agent must never file
# feedback on itself.
BLACKLISTED_USE_CASES: frozenset[str] = frozenset({
    "RecordChatFeedbackUseCase",
})

# Excluded because chat has no file input/output channel, not by policy.
MECHANICALLY_EXCLUDED_USE_CASES: frozenset[str] = frozenset({
    "ExportLastFmLikesUseCase",
})

# Engine/pipeline plumbing with no direct human surface — the agent reaches
# these capabilities the same way a human does (by building and running a
# workflow, or through the tools that embed them), never by calling them
# standalone. A standalone tool would be a private agent capability, breaking
# the "and nothing more" half of the parity contract.
INTERNAL_USE_CASES: frozenset[str] = frozenset({
    # The workflow run executor is driven by RunWorkflowUseCase / the scheduler.
    "ExecuteWorkflowRunUseCase",
    # A frontend SSE-watchdog fallback: re-reads a run by its ephemeral
    # operation_id after a 45s stream stall. The agent has no natural handle on
    # that id and reads run status via query_operations (GetOperationRunUseCase),
    # so the snapshot is plumbing, not an agent capability.
    "GetOperationSnapshotUseCase",
    # An enricher-node step of the workflow engine (built only in
    # workflows/nodes/factories.py); no direct route or CLI. The agent enriches
    # by generating a workflow with an enricher node and running it.
    "EnrichTracksUseCase",
    # An internal step of enrich/import that requires a live connector API
    # instance; no direct human surface. Reached via the same workflow path.
    "MatchAndIdentifyTracksUseCase",
    # Workflow-destination capabilities (destination.* nodes) with no direct
    # route or CLI — only the workflow engine builds them. The agent creates or
    # updates a connector playlist by generating a workflow with that
    # destination and running it, exactly as a human does.
    "CreateConnectorPlaylistUseCase",
    "UpdateConnectorPlaylistUseCase",
})

# Classified but not yet covered — coverage is v0.9.1's job (Full Capability
# Parity). The parity test enforces *classification* here, not coverage: a NEW
# use case that lands in none of these buckets fails CI, so classification
# discipline holds from the first tool. Entries move into a ToolSpec's
# ``use_cases`` as v0.9.1 (and v0.9.0 Phase 3) build the tools that cover them.
# Empty as of v0.9.1: the parity contract is closed — every application use case
# is either covered by a ToolSpec or in an exclusion bucket above. Kept as an
# (empty) set so a NEW use case that lands unclassified still fails the parity
# test until it is deliberately covered or excluded.
NOT_YET_COVERED: frozenset[str] = frozenset()
