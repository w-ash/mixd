"""Unit-of-work transaction + repository-provider protocol.

Split from the former monolithic ``interfaces.py``.
"""

from typing import Protocol, Self

from src.domain.repositories.chat_feedback import ChatFeedbackRepositoryProtocol
from src.domain.repositories.checkpoint import CheckpointRepositoryProtocol
from src.domain.repositories.connector import (
    ConnectorPlaylistRepositoryProtocol,
    ConnectorRepositoryProtocol,
    ServiceConnectorProvider,
)
from src.domain.repositories.like import LikeRepositoryProtocol
from src.domain.repositories.match_review import MatchReviewRepositoryProtocol
from src.domain.repositories.metric import MetricsRepositoryProtocol
from src.domain.repositories.operation_run import OperationRunRepositoryProtocol
from src.domain.repositories.play import (
    ConnectorPlayRepositoryProtocol,
    PlaysRepositoryProtocol,
)
from src.domain.repositories.playlist import (
    PlaylistAssignmentRepositoryProtocol,
    PlaylistLinkRepositoryProtocol,
    PlaylistRepositoryProtocol,
    PlaylistSyncBaseRepositoryProtocol,
)
from src.domain.repositories.preference import PreferenceRepositoryProtocol
from src.domain.repositories.schedule import ScheduleRepositoryProtocol
from src.domain.repositories.stats import StatsRepositoryProtocol
from src.domain.repositories.tag import TagRepositoryProtocol
from src.domain.repositories.track import (
    TrackIdentityServiceProtocol,
    TrackMergeServiceProtocol,
    TrackRepositoryProtocol,
)
from src.domain.repositories.workflow import (
    WorkflowRepositoryProtocol,
    WorkflowRunRepositoryProtocol,
    WorkflowVersionRepositoryProtocol,
)


class UnitOfWorkProtocol(Protocol):
    """Unit of Work interface for transaction boundary management.

    This protocol follows Clean Architecture principles by allowing the application
    layer to control transaction boundaries while keeping the implementation details
    in the infrastructure layer. Each UnitOfWork instance manages a single database
    transaction and provides access to all repositories sharing that transaction.
    """

    async def __aenter__(self) -> Self:
        """Enter async context manager."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        """Exit async context manager with automatic commit/rollback."""
        ...

    async def commit(self) -> None:
        """Explicitly commit the current transaction."""
        ...

    async def rollback(self) -> None:
        """Explicitly rollback the current transaction."""
        ...

    def get_track_repository(self) -> TrackRepositoryProtocol:
        """Get track repository using this unit of work's transaction."""
        ...

    def get_playlist_repository(self) -> PlaylistRepositoryProtocol:
        """Get playlist repository using this unit of work's transaction."""
        ...

    def get_like_repository(self) -> LikeRepositoryProtocol:
        """Get like repository using this unit of work's transaction."""
        ...

    def get_checkpoint_repository(self) -> CheckpointRepositoryProtocol:
        """Get checkpoint repository using this unit of work's transaction."""
        ...

    def get_connector_repository(self) -> ConnectorRepositoryProtocol:
        """Get connector repository using this unit of work's transaction."""
        ...

    def get_metrics_repository(self) -> MetricsRepositoryProtocol:
        """Get metrics repository using this unit of work's transaction."""
        ...

    def get_plays_repository(self) -> PlaysRepositoryProtocol:
        """Get plays repository using this unit of work's transaction."""
        ...

    def get_track_identity_service(self) -> TrackIdentityServiceProtocol:
        """Get track identity service using this unit of work's transaction."""
        ...

    def get_service_connector_provider(self) -> ServiceConnectorProvider:
        """Get service connector provider for accessing music service connectors."""
        ...

    def get_playlist_link_repository(self) -> PlaylistLinkRepositoryProtocol:
        """Get playlist link repository for managing canonical-to-external playlist mappings."""
        ...

    def get_playlist_sync_base_repository(
        self,
    ) -> PlaylistSyncBaseRepositoryProtocol:
        """Get the per-link sync base repository (last-reconciled external snapshot)."""
        ...

    def get_connector_playlist_repository(
        self,
    ) -> ConnectorPlaylistRepositoryProtocol:
        """Get connector playlist repository for playlist-related operations."""
        ...

    def get_connector_play_repository(self) -> ConnectorPlayRepositoryProtocol:
        """Get connector play repository for play ingestion and resolution operations."""
        ...

    def get_workflow_repository(self) -> WorkflowRepositoryProtocol:
        """Get workflow repository using this unit of work's transaction."""
        ...

    def get_workflow_run_repository(self) -> WorkflowRunRepositoryProtocol:
        """Get workflow run repository using this unit of work's transaction."""
        ...

    def get_workflow_version_repository(self) -> WorkflowVersionRepositoryProtocol:
        """Get workflow version repository using this unit of work's transaction."""
        ...

    def get_match_review_repository(self) -> MatchReviewRepositoryProtocol:
        """Get match review repository for review queue operations."""
        ...

    def get_preference_repository(self) -> PreferenceRepositoryProtocol:
        """Get preference repository for track preference operations."""
        ...

    def get_tag_repository(self) -> TagRepositoryProtocol:
        """Get tag repository for track tag operations."""
        ...

    def get_playlist_assignment_repository(
        self,
    ) -> PlaylistAssignmentRepositoryProtocol:
        """Get playlist assignment repository for connector-playlist → metadata bindings."""
        ...

    def get_stats_repository(self) -> StatsRepositoryProtocol:
        """Get cross-table stats repository for dashboard aggregation."""
        ...

    def get_operation_run_repository(self) -> OperationRunRepositoryProtocol:
        """Get operation_run repository for SSE-operation audit log."""
        ...

    def get_schedule_repository(self) -> ScheduleRepositoryProtocol:
        """Get schedule repository for workflow/sync calendar triggers."""
        ...

    def get_track_merge_service(self) -> TrackMergeServiceProtocol:
        """Get track merge service using this unit of work's transaction."""
        ...

    def get_chat_feedback_repository(self) -> ChatFeedbackRepositoryProtocol:
        """Get chat feedback repository for thumbs-up/down on generated workflows."""
        ...
