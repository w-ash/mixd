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

from src.application.chat import confirmed_actions, tool_executor
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
    # them (v0.9.2). Non-deferred is the default while the registry is small.
    defer_loading: bool = False

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
    )


TOOLS: tuple[ToolSpec, ...] = _CORE_TOOLS + tuple(
    _spec_from_mapping(entry) for specs in _DISPATCHER_SPECS_LISTS for entry in specs
)

_SPECS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOLS}


def build_tools() -> list[dict[str, object]]:
    """Anthropic tool list in registry order.

    Order must be deterministic — tools render first in the prompt, so any
    reordering invalidates the whole prompt cache. The cache breakpoint goes on
    the last non-deferred entry (a deferred tool cannot carry ``cache_control``,
    and deferred tools are excluded from the cached prefix anyway).
    ``allowed_callers`` and tool-search deferral are stamped starting in v0.9.2.
    """
    tool_list: list[dict[str, object]] = []
    last_loaded = -1
    for spec in TOOLS:
        tool: dict[str, object] = {
            "name": spec.name,
            "description": spec.description,
            "input_schema": dict(spec.input_schema),
        }
        if spec.defer_loading:
            tool["defer_loading"] = True
        else:
            last_loaded = len(tool_list)
        tool_list.append(tool)
    if last_loaded >= 0:
        tool_list[last_loaded]["cache_control"] = {"type": "ephemeral"}
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
