"""Play-history repository + import/resolve service protocols.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypedDict
from uuid import UUID

from attrs import define

from src.domain.entities import (
    ConnectorTrackPlay,
    OperationResult,
    PlaySource,
    TrackPlay,
)
from src.domain.entities.progress import ProgressEmitter

if TYPE_CHECKING:
    from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class LastfmImportParams:
    """Selectors for a Last.fm play import (API-based, checkpoint-bounded).

    ``username`` is the *request* username (CLI affordance); the importer
    resolves the effective account token-first (see the importer's
    ``_resolve_username``) and threads the resolved name through the pipeline.
    """

    username: str | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None
    limit: int | None = None


@define(frozen=True, slots=True)
class SpotifyImportParams:
    """Selectors for a Spotify play import (personal-data file export)."""

    file_path: Path
    batch_size: int | None = None


# Spotify's recently-played endpoint caps a page at 50 and retains no more than
# that, so a larger request is silently unachievable. Declared once, beside the
# params type it bounds, so the application clamp and the HTTP client agree.
RECENTLY_PLAYED_PAGE_LIMIT: Final = 50

# The one scope the recently-played poller gates on (v0.10.1) — a grant can be
# short only this and still serve likes/playlists. Declared here for the same
# reason as the page limit: the poll policy, the pre-flight dependency, and the
# connector all have to agree on it, and they sit in three different layers.
RECENTLY_PLAYED_SCOPE: Final = "user-read-recently-played"


@define(frozen=True, slots=True)
class SpotifyRecentImportParams:
    """Selectors for a Spotify recently-played API poll.

    Carries no cursor: resume position lives on the ``("spotify", "plays")``
    sync checkpoint, keyed on the mixd user the pipeline threads through, so a
    poll cannot accidentally resume from another tenant's position.

    ``force`` ignores that checkpoint for one poll and re-reads the whole
    retained window. Re-ingesting already-seen plays is harmless (the ledger's
    dedup constraint conflict-skips them), so this is a recovery lever for a
    cursor that ran ahead of what was actually stored.
    """

    limit: int = RECENTLY_PLAYED_PAGE_LIMIT
    force: bool = False


type PlayImportParams = (
    LastfmImportParams | SpotifyImportParams | SpotifyRecentImportParams
)

# Where an import's data comes from. One service can offer both — Spotify reads
# GDPR export files AND polls the recently-played API — so the importer registry
# keys on (service, kind) rather than service alone.
type ImportKind = Literal["file", "api"]


type PlaySortBy = Literal[
    "total_plays_desc",
    "last_played_desc",
    "title_asc",
    "random",
    "played_at_desc",
    "first_played_asc",
]


class PlayAggregationResult(TypedDict, total=False):
    """Typed result from play aggregation queries.

    Each key maps track IDs to their aggregated value. All keys are optional
    (total=False) because callers request specific metric subsets.
    """

    total_plays: dict[UUID, int]
    first_played_dates: dict[UUID, datetime | None]
    last_played_dates: dict[UUID, datetime | None]
    period_plays: dict[UUID, int]


class ResolutionMetrics(TypedDict, total=False):
    """Metrics produced by play resolution.

    All keys are optional (total=False) because different resolvers
    (Spotify, Last.fm) emit different subsets of metrics.
    """

    raw_plays: int
    accepted_plays: int
    duration_excluded: int
    incognito_excluded: int
    error_count: int
    resolution_failures: list[dict[str, str]]
    new_tracks_count: int
    updated_tracks_count: int
    unique_tracks_processed: int
    tracks_resolved: int
    fallback_resolved: int
    redirect_resolved: int
    spotify_enhanced_count: int
    dead_ids_unresolved: int
    isrc_suspect_deferred: int


@define(frozen=True, slots=True)
class PlayResolutionOutcome:
    """Result of resolving a batch of connector plays.

    ``resolutions`` pairs each successfully resolved (and policy-accepted)
    connector play with its canonical track id — the orchestrator persists
    these onto the ledger (``connector_plays.resolved_track_id``) after
    Phase 2. Plays that failed to resolve or were policy-excluded
    (duration/incognito filters) are absent: their ledger rows keep
    ``resolved_track_id IS NULL``, which is what keeps them outside the
    canonical-play projection.
    """

    track_plays: list[TrackPlay]
    metrics: ResolutionMetrics
    resolutions: tuple[tuple[ConnectorTrackPlay, UUID], ...]


class PlaysRepositoryProtocol(Protocol):
    """Repository interface for play history operations."""

    def bulk_insert_plays(self, plays: list[TrackPlay]) -> Awaitable[tuple[int, int]]:
        """Bulk insert plays.

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        ...

    def get_recent_plays(
        self, *, user_id: str, limit: int = 100, sort_by: PlaySortBy | None = None
    ) -> Awaitable[list[TrackPlay]]:
        """Get recent plays.

        Args:
            user_id: Owner's user ID.
            limit: Maximum number of plays to return
            sort_by: Optional sorting method
        """
        ...

    def get_play_aggregations(
        self,
        track_ids: list[UUID],
        metrics: list[str],
        *,
        user_id: str,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> Awaitable[PlayAggregationResult]:
        """Get aggregated play data for specified tracks and metrics.

        Args:
            track_ids: List of track IDs to get play data for
            metrics: List of metrics to calculate ["total_plays", "last_played_dates", "period_plays"]
            user_id: Owner's user ID.
            period_start: Start date for period-based metrics (optional)
            period_end: End date for period-based metrics (optional)

        Returns:
            Typed dictionary mapping metric names to {track_id: value} dictionaries.
        """
        ...

    def find_plays_in_window(
        self,
        start: datetime,
        end: datetime,
        *,
        user_id: str,
    ) -> Awaitable[list[TrackPlay]]:
        """All of a user's plays with ``played_at`` in [start, end)."""
        ...

    def get_plays_by_ids(
        self,
        play_ids: Sequence[UUID],
        *,
        user_id: str,
    ) -> Awaitable[list[TrackPlay]]:
        """Fetch plays by id, scoped to the owner."""
        ...

    def bulk_update_plays(
        self,
        updates: Sequence[tuple[UUID, Mapping[str, object]]],
    ) -> Awaitable[None]:
        """Batch-update projected field values on existing plays."""
        ...

    def find_unsourced_play_ids(self, *, user_id: str) -> Awaitable[list[UUID]]:
        """Ids of a user's plays with no ledger membership."""
        ...

    def delete_plays_without_sources(
        self,
        candidate_ids: Sequence[UUID],
        *,
        user_id: str,
    ) -> Awaitable[int]:
        """Delete candidate plays no ledger observation backs anymore."""
        ...

    def get_play_sources_for_connector_plays(
        self,
        connector_play_ids: Sequence[UUID],
        *,
        user_id: str,
    ) -> Awaitable[list[PlaySource]]:
        """Membership edges for the given ledger observations."""
        ...

    def get_play_sources_for_plays(
        self,
        play_ids: Sequence[UUID],
        *,
        user_id: str,
    ) -> Awaitable[list[PlaySource]]:
        """Membership edges pointing at the given canonical plays."""
        ...

    def bulk_upsert_play_sources(
        self, sources: Sequence[PlaySource]
    ) -> Awaitable[None]:
        """Insert-or-repoint membership edges (arbiter: connector_play_id)."""
        ...


class ConnectorPlayRepositoryProtocol(Protocol):
    """Repository interface for connector play operations.

    Handles raw play data from external music services before resolution to canonical plays.
    Follows the same clean pattern as other connector repositories with simple resolution tracking.
    """

    def bulk_insert_connector_plays(
        self, connector_plays: Iterable[ConnectorTrackPlay]
    ) -> Awaitable[tuple[int, int]]:
        """Bulk insert connector plays from external API data.

        Args:
            connector_plays: Any iterable of ConnectorTrackPlay from ingestion.
                A generator is valid and preferred for large imports — the
                implementation streams and counts as it goes, so nothing forces
                a 198k-row export to be resident all at once. ``len()`` is
                never taken.

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count), where the counts
            sum to the number of items the iterable yielded.
        """
        ...

    def get_resolved_played_at_days(self, *, user_id: str) -> Awaitable[list[date]]:
        """Ascending distinct UTC calendar days holding ≥1 resolved ledger row.

        The full-history projection's pre-query: a multi-year history with
        sparse listening projects only the days that hold observations, so
        the day list — not a (min, max) span to tile — is what the rebuild
        needs. Empty when the user has no resolved rows.
        """
        ...

    def find_resolved_in_window(
        self,
        start: datetime,
        end: datetime,
        *,
        user_id: str,
    ) -> Awaitable[list[ConnectorTrackPlay]]:
        """Resolved ledger observations with ``played_at`` in [start, end).

        Window-only, no track filter — the projection's resolution-divergence
        bridge needs observations that resolved to different canonical tracks
        but share normalized identity.
        """
        ...

    def bulk_update_resolution(
        self,
        resolutions: Sequence[tuple[ConnectorTrackPlay, UUID]],
        *,
        resolved_at: datetime,
    ) -> Awaitable[int]:
        """Persist canonical resolution onto ledger rows.

        Matches stored rows by the ledger natural key (user_id, connector_name,
        connector_track_identifier, played_at, ms_played) — NOT by entity id:
        conflict-skipped re-import rows keep their original stored ids, and
        natural-key matching lets a re-import heal rows whose first resolution
        attempt failed.

        Returns:
            Number of ledger rows updated.
        """
        ...


class PlayImporterProtocol(Protocol):
    """Protocol for play import services in infrastructure layer.

    Infrastructure classes implement this to provide service-specific play import
    capabilities (Last.fm API, Spotify file export, etc.). Application layer
    orchestrates via PlayImportOrchestrator without knowing the concrete service.
    """

    async def import_plays(
        self,
        uow: UnitOfWorkProtocol,
        params: PlayImportParams,
        *,
        user_id: str,
        progress_emitter: ProgressEmitter | None = None,
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import plays and return result with connector plays for resolution.

        Each importer accepts its own params type from the union and raises
        ``TypeError`` on a mismatch (the importer registry is stringly-typed;
        this check restores the type boundary at runtime).

        ``user_id`` is required (v0.10.1): it is both the ledger tenancy stamp
        and the sync-checkpoint key, and the first unattended per-user writer
        (scheduler-dispatched polls) makes a silent default unsafe.
        """
        ...


class PlayResolverProtocol(Protocol):
    """Protocol for play resolution services in infrastructure layer.

    Resolves raw ConnectorTrackPlay objects to canonical TrackPlay objects
    by looking up or creating canonical tracks for each external ID.
    """

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> PlayResolutionOutcome:
        """Resolve connector plays to canonical track plays.

        Args:
            connector_plays: Raw plays from external service.
            uow: Unit of work for database operations.
            user_id: Authenticated user ID for data scoping.
            progress_callback: Optional progress reporting.

        Returns:
            PlayResolutionOutcome with track plays, metrics, and the
            connector-play↔track resolution pairs for ledger write-back.
        """
        ...


class PlayImportProvider(Protocol):
    """Looks up the importer/resolver a given service and import kind need.

    Only the *lookup* is infrastructure — both return values are already
    domain protocols. Declaring the lookup here is what lets the import use
    case pick a per-service implementation without naming any connector, which
    is the property its docstrings have always claimed.
    """

    def create_play_importer(
        self, service: str, kind: ImportKind, uow: UnitOfWorkProtocol
    ) -> Awaitable[PlayImporterProtocol]:
        """Importer for ``service`` reading from ``kind`` (live API or export file)."""
        ...

    def create_play_resolver(
        self, service: str, uow: UnitOfWorkProtocol | None = None
    ) -> Awaitable[PlayResolverProtocol]:
        """Resolver for ``service`` — how its identifiers map to canonical tracks."""
        ...
