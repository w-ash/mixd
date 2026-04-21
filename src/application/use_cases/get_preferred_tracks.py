"""Retrieves tracks the user has assigned a given preference state.

Feeds the `source.preferred_tracks` workflow node: "start a workflow from all
my starred tracks" (or yah / hmm / nah). Thin wrapper over
`track_repo.list_tracks(preference=<state>)` — no pagination, no cursors, no
extra preference/tag hydration (downstream enrichers handle that).
"""

from attrs import define, field
from attrs.validators import and_, ge, in_, instance_of, le

from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities.preference import PreferenceState
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


_PREFERENCE_STATE_VALUES: tuple[PreferenceState, ...] = ("hmm", "nah", "yah", "star")


@define(frozen=True, slots=True)
class GetPreferredTracksCommand:
    """Configuration for retrieving tracks by preference state."""

    user_id: str
    state: PreferenceState = field(validator=in_(_PREFERENCE_STATE_VALUES))
    limit: int = field(
        default=BusinessLimits.DEFAULT_LIBRARY_QUERY_LIMIT,
        validator=and_(instance_of(int), ge(1), le(BusinessLimits.MAX_USER_LIMIT)),
    )


@define(frozen=True, slots=True)
class GetPreferredTracksResult:
    """Result of preferred tracks retrieval."""

    tracklist: TrackList
    execution_time_ms: int = 0


@define(slots=True)
class GetPreferredTracksUseCase:
    """Fetches tracks filtered by preference state for workflow source nodes."""

    async def execute(
        self, command: GetPreferredTracksCommand, uow: UnitOfWorkProtocol
    ) -> GetPreferredTracksResult:
        timer = ExecutionTimer()

        logger.info(
            "Retrieving preferred tracks",
            state=command.state,
            limit=command.limit,
        )

        async with uow:
            track_repo = uow.get_track_repository()
            # Skip COUNT — source node infers truncation from len(tracks) vs limit.
            page = await track_repo.list_tracks(
                user_id=command.user_id,
                preference=command.state,
                limit=command.limit,
                include_total=False,
            )

        tracklist = TrackList(
            tracks=page["tracks"],
            metadata={"operation": "get_preferred_tracks"},
        )

        return GetPreferredTracksResult(
            tracklist=tracklist,
            execution_time_ms=timer.stop(),
        )
