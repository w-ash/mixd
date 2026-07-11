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

from src.application.chat import tool_executor
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext, ToolDispatch
from src.application.chat.user_data import strip_user_data
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import ToolExecutionError

type ToolKind = Literal["read", "write", "agentic"]
# A confirmed mutation executor: the claimed pending action + acting user id.
type ConfirmedExecutor = Callable[[PendingAction, str], Awaitable[JsonValue]]


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
    # Deferred tools stay out of the upfront prompt until tool search surfaces
    # them (v0.9.2). Non-deferred is the default while the registry is small.
    defer_loading: bool = False

    def __attrs_post_init__(self) -> None:
        if (self.kind == "write") != (self.executor is not None):
            raise ValueError(
                f"{self.name}: kind {self.kind!r} inconsistent with executor"
            )
        if self.dispatch is None and self.kind != "agentic":
            raise ValueError(
                f"{self.name}: only agentic server tools may omit a dispatcher"
            )
        if self.kind == "agentic" and self.defer_loading:
            raise ValueError(f"{self.name}: agentic capabilities must not be deferred")


TOOLS: tuple[ToolSpec, ...] = (
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


async def execute_confirmed_action(action: PendingAction, user_id: str) -> JsonValue:
    """Execute a confirmed pending mutation through its registered executor."""
    spec = _SPECS_BY_NAME.get(action.tool_name)
    if spec is None or spec.executor is None:
        raise ToolExecutionError(f"Unknown mutation tool: {action.tool_name}")
    return await spec.executor(action, user_id)


# --- Parity accounting (asserted by test_registry_parity.py) ---------------

# Human-only by product decision (D4). mixd's human-only capabilities —
# connector OAuth/token flows, account management, ``mixd admin reset`` — are
# not application use cases (they live in the connector/interface layer), so
# none of the 81 use-case classes are blacklisted. The set exists so a future
# use case that IS human-only lands here explicitly rather than growing a tool.
BLACKLISTED_USE_CASES: frozenset[str] = frozenset()

# Excluded because chat has no file input/output channel, not by policy.
MECHANICALLY_EXCLUDED_USE_CASES: frozenset[str] = frozenset({
    "ExportLastFmLikesUseCase",
})

# Engine/pipeline plumbing the chat layer never invokes directly: the workflow
# run executor is driven by RunWorkflowUseCase / the scheduler, not the agent.
INTERNAL_USE_CASES: frozenset[str] = frozenset({
    "ExecuteWorkflowRunUseCase",
})

# Classified but not yet covered — coverage is v0.9.1's job (Full Capability
# Parity). The parity test enforces *classification* here, not coverage: a NEW
# use case that lands in none of these buckets fails CI, so classification
# discipline holds from the first tool. Entries move into a ToolSpec's
# ``use_cases`` as v0.9.1 (and v0.9.0 Phase 3) build the tools that cover them.
NOT_YET_COVERED: frozenset[str] = frozenset({
    "AddPlaylistTracksUseCase",
    "ApplyPlaylistAssignmentsUseCase",
    "BatchTagTracksUseCase",
    "CheckDataIntegrityUseCase",
    "CreateAndApplyAssignmentUseCase",
    "CreateCanonicalPlaylistUseCase",
    "CreateConnectorPlaylistUseCase",
    "CreatePlaylistAssignmentUseCase",
    "CreatePlaylistLinkUseCase",
    "CreateWorkflowUseCase",
    "DeleteCanonicalPlaylistUseCase",
    "DeletePlaylistAssignmentUseCase",
    "DeletePlaylistLinkUseCase",
    "DeleteScheduleUseCase",
    "DeleteTagUseCase",
    "DeleteWorkflowUseCase",
    "DuplicateWorkflowUseCase",
    "EnrichTracksUseCase",
    "GetDashboardStatsUseCase",
    "GetLatestWorkflowRunsUseCase",
    "GetLikedTracksUseCase",
    "GetMatchMethodHealthUseCase",
    "GetOperationRunUseCase",
    "GetOperationSnapshotUseCase",
    "GetPlayedTracksUseCase",
    "GetPreferredTracksUseCase",
    "GetScheduleUseCase",
    "GetSyncCheckpointStatusUseCase",
    "GetTrackDetailsUseCase",
    "GetTrackPlaylistsUseCase",
    "GetWorkflowRunUseCase",
    "GetWorkflowUseCase",
    "GetWorkflowVersionUseCase",
    "ImportConnectorPlaylistsAsCanonicalUseCase",
    "ImportSpotifyLikesUseCase",
    "ImportTracksUseCase",
    "InstantiateWorkflowUseCase",
    "ListActiveRunsUseCase",
    "ListConnectorPlaylistsUseCase",
    "ListMatchReviewsUseCase",
    "ListOperationRunsUseCase",
    "ListPlaylistLinksUseCase",
    "ListPlaylistsUseCase",
    "ListSchedulesUseCase",
    "ListTagsUseCase",
    "ListTracksUseCase",
    "ListWorkflowRunsUseCase",
    "ListWorkflowsUseCase",
    "ListWorkflowVersionsUseCase",
    "MatchAndIdentifyTracksUseCase",
    "MergeTagsUseCase",
    "MergeTrackAndFetchDetailsUseCase",
    "MergeTracksUseCase",
    "PreviewPlaylistSyncUseCase",
    "PreviewWorkflowUseCase",
    "ReadCanonicalPlaylistUseCase",
    "ReadPlaylistTracksPageUseCase",
    "RefreshConnectorPlaylistsUseCase",
    "RelinkConnectorTrackUseCase",
    "RemovePlaylistEntriesUseCase",
    "RenameTagUseCase",
    "ReorderPlaylistEntriesUseCase",
    "RepairUnresolvedEntriesUseCase",
    "ResolveMatchReviewUseCase",
    "RevertWorkflowVersionUseCase",
    "RunWorkflowUseCase",
    "SetPrimaryMappingUseCase",
    "SetTrackPreferenceUseCase",
    "SyncPlaylistLinkUseCase",
    "SyncPreferencesFromLikesUseCase",
    "TagTrackUseCase",
    "ToggleScheduleUseCase",
    "UnlinkConnectorTrackUseCase",
    "UntagTrackUseCase",
    "UpdateCanonicalPlaylistUseCase",
    "UpdateConnectorPlaylistUseCase",
    "UpdatePlaylistLinkUseCase",
    "UpdateWorkflowUseCase",
    "UpsertScheduleUseCase",
})
