"""SQLAlchemy ORM models, one module per aggregate.

This package is the single import surface for every model::

    from src.infrastructure.persistence.database.models import DBTrack

Every module is imported here so the shared declarative registry is complete
before the first ``configure_mappers()`` — a cross-module relationship is a
deferred annotation (PEP 649) that resolves lazily by class name, and a module
that is not imported turns into a runtime error on the first query that touches
it.
``tests/integration/test_schema_gates.py`` fails the build if a mapped class
or a table ever goes missing from the registry or the metadata.

Modules mirror ``persistence/repositories/``: ``track`` is the leaf (the
canonical track and its child rows); ``mapping``, ``play`` and ``playlist``
import it for their to-one side, and ``track`` names their classes only under
``TYPE_CHECKING``. Every other module references foreign tables by name and
imports nothing but ``base``.
"""

from src.infrastructure.persistence.database.models.auth import (
    DBOAuthAuthorizationCode,
    DBOAuthAuthorizationRequest,
    DBOAuthClient,
    DBOAuthRefreshToken,
    DBOAuthState,
    DBOAuthToken,
)
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    DatabaseModel,
    TimestampMixin,
    metadata,
)
from src.infrastructure.persistence.database.models.chat import (
    DBChatFeedback,
    DBPendingAction,
)
from src.infrastructure.persistence.database.models.mapping import (
    DBMatchReview,
    DBTrackMapping,
)
from src.infrastructure.persistence.database.models.operation_run import (
    DBOperationRun,
)
from src.infrastructure.persistence.database.models.play import (
    DBConnectorPlay,
    DBPlaySource,
    DBTrackPlay,
)
from src.infrastructure.persistence.database.models.playlist import (
    DBConnectorPlaylist,
    DBPlaylist,
    DBPlaylistAssignment,
    DBPlaylistAssignmentMember,
    DBPlaylistMapping,
    DBPlaylistSyncBase,
    DBPlaylistTrack,
)
from src.infrastructure.persistence.database.models.resolution import (
    DBResolutionEvent,
    DBResolutionNegative,
)
from src.infrastructure.persistence.database.models.schedule import DBSchedule
from src.infrastructure.persistence.database.models.sync import DBSyncCheckpoint
from src.infrastructure.persistence.database.models.track import (
    DBConnectorTrack,
    DBTrack,
    DBTrackLike,
    DBTrackMetric,
    DBTrackPreference,
    DBTrackPreferenceEvent,
    DBTrackTag,
    DBTrackTagEvent,
)
from src.infrastructure.persistence.database.models.user_settings import (
    DBUserSettings,
)
from src.infrastructure.persistence.database.models.workflow import (
    DBWorkflow,
    DBWorkflowRun,
    DBWorkflowRunNode,
    DBWorkflowVersion,
)

__all__ = [
    "BaseEntity",
    "DBChatFeedback",
    "DBConnectorPlay",
    "DBConnectorPlaylist",
    "DBConnectorTrack",
    "DBMatchReview",
    "DBOAuthAuthorizationCode",
    "DBOAuthAuthorizationRequest",
    "DBOAuthClient",
    "DBOAuthRefreshToken",
    "DBOAuthState",
    "DBOAuthToken",
    "DBOperationRun",
    "DBPendingAction",
    "DBPlaySource",
    "DBPlaylist",
    "DBPlaylistAssignment",
    "DBPlaylistAssignmentMember",
    "DBPlaylistMapping",
    "DBPlaylistSyncBase",
    "DBPlaylistTrack",
    "DBResolutionEvent",
    "DBResolutionNegative",
    "DBSchedule",
    "DBSyncCheckpoint",
    "DBTrack",
    "DBTrackLike",
    "DBTrackMapping",
    "DBTrackMetric",
    "DBTrackPlay",
    "DBTrackPreference",
    "DBTrackPreferenceEvent",
    "DBTrackTag",
    "DBTrackTagEvent",
    "DBUserSettings",
    "DBWorkflow",
    "DBWorkflowRun",
    "DBWorkflowRunNode",
    "DBWorkflowVersion",
    "DatabaseModel",
    "TimestampMixin",
    "metadata",
]
