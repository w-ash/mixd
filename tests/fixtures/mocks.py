"""Composable mock builders for UnitOfWork and repository mocks.

Plain functions that construct pre-wired mocks. Tests import these instead of
re-building ``mock_uow`` fixtures from scratch in every file.

Usage::

    from tests.fixtures.mocks import make_mock_uow

    uow = make_mock_uow()  # lazy repos
    uow = make_mock_uow(track_repo=custom_mock)  # pre-configure one repo
"""

from __future__ import annotations

from annotationlib import Format
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import cache
import inspect
from typing import Any, get_origin
from unittest.mock import AsyncMock, MagicMock, NonCallableMagicMock, create_autospec
from uuid import uuid7

from src.application.connector_protocols import PlaylistConnector
from src.domain.entities import SyncCheckpoint
from src.domain.entities.connector import Capability, ConnectorDescriptor
from src.domain.entities.progress import ProgressEmitter
from src.domain.matching.artist_enrichment import ArtistEnrichmentProviderProtocol
from src.domain.matching.artist_equivalence import EMPTY_EQUIVALENCE
from src.domain.repositories.artist import (
    ArtistAliasRepositoryProtocol,
    ArtistConnectorRepositoryProtocol,
    ArtistFavoriteRepositoryProtocol,
    ArtistMinterProtocol,
    ArtistRepositoryProtocol,
)
from src.domain.repositories.chat_feedback import ChatFeedbackRepositoryProtocol
from src.domain.repositories.checkpoint import CheckpointRepositoryProtocol
from src.domain.repositories.connector import (
    ConnectorPlaylistRepositoryProtocol,
    ConnectorRepositoryProtocol,
)
from src.domain.repositories.like import LikeRepositoryProtocol
from src.domain.repositories.match_review import MatchReviewRepositoryProtocol
from src.domain.repositories.metric import MetricsRepositoryProtocol
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
from src.domain.repositories.resolution import (
    ResolutionEventRepositoryProtocol,
    ResolutionNegativeRepositoryProtocol,
    ResolutionRecorderProtocol,
)
from src.domain.repositories.schedule import ScheduleRepositoryProtocol
from src.domain.repositories.stats import StatsRepositoryProtocol
from src.domain.repositories.tag import TagRepositoryProtocol
from src.domain.repositories.track import TrackRepositoryProtocol
from src.domain.repositories.workflow import (
    WorkflowRepositoryProtocol,
    WorkflowRunRepositoryProtocol,
    WorkflowVersionRepositoryProtocol,
)


def _declared_capabilities(name: str) -> frozenset[Capability]:
    """The capability set the real registry declares for ``name``.

    Unknown names declare nothing, the same as Tidal and Discogs do today.
    """
    from src.infrastructure.connectors.discovery import discover_connectors

    config = discover_connectors().get(name)
    return config["capabilities"] if config is not None else frozenset()


def make_mock_connector_provider(
    connector: object = None,
    *,
    name: str = "spotify",
    capabilities: frozenset[Capability] | None = None,
    **overrides,
) -> MagicMock:
    """Build a ``MagicMock`` mimicking :class:`ServiceConnectorProvider`.

    ``describe`` returns a descriptor with the capabilities the real registry
    declares for ``name``, so the gate in ``resolve_capability`` behaves as it
    does in production — a test that needs a capability the connector does
    not declare says so with ``capabilities=``.
    """
    provider = MagicMock()
    if connector is not None:
        provider.get_connector.return_value = connector
    provider.describe.return_value = ConnectorDescriptor(
        name=name,
        display_name=name.title(),
        category="streaming",
        auth_method="oauth",
        capabilities=(
            capabilities if capabilities is not None else _declared_capabilities(name)
        ),
    )
    for key, value in overrides.items():
        setattr(provider, key, value)
    return provider


def fake_run_async[T](value: T) -> Callable[[Coroutine[Any, Any, Any]], T]:
    """Build a side_effect for patching ``run_async`` that returns ``value``.

    The CLI bridge calls ``run_async(coro)`` where ``coro`` is a fresh
    coroutine. A bare ``return_value`` patch never consumes the coroutine, so
    it leaks until garbage collection emits ``RuntimeWarning: coroutine ...
    was never awaited``. This helper closes the coroutine first.
    """

    def _closer(coro: Coroutine[Any, Any, Any]) -> T:
        coro.close()
        return value

    return _closer


# ---------------------------------------------------------------------------
# Protocol-specced mocks
# ---------------------------------------------------------------------------


@cache
def _awaitable_spec(protocol: type) -> type:
    """Return the protocol with each ``-> Awaitable[T]`` method made ``async``.

    The protocols declare ``def m(...) -> Awaitable[T]``. ``create_autospec``
    reads such a method as sync, so its mock cannot be awaited. This subclass
    declares each such method again as a coroutine function with the same
    signature.
    """
    namespace: dict[str, object] = {}
    for name, member in inspect.getmembers(protocol, inspect.isfunction):
        if name.startswith("_"):
            continue
        signature = inspect.signature(member, annotation_format=Format.FORWARDREF)
        if get_origin(signature.return_annotation) not in (Awaitable, Coroutine):
            continue

        async def stub(*_args: object, **_kwargs: object) -> None: ...

        stub.__signature__ = signature
        stub.__name__ = name
        namespace[name] = stub
    return type(protocol.__name__, (protocol,), namespace)


def protocol_mock(protocol: type, **attributes: object) -> NonCallableMagicMock:
    """Build a mock that accepts only what ``protocol`` declares.

    A misspelled method, an unknown keyword argument, or an override that
    names no protocol member raises instead of passing silently. Each
    awaitable method is an ``AsyncMock``. ``attributes`` sets non-method
    members, such as a property.
    """
    return create_autospec(
        _awaitable_spec(protocol), instance=True, spec_set=True, **attributes
    )


# ---------------------------------------------------------------------------
# Individual repository mocks
# ---------------------------------------------------------------------------


def make_mock_track_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`TrackRepositoryProtocol`."""
    repo = protocol_mock(TrackRepositoryProtocol)
    repo.find_tracks_by_ids.return_value = overrides.pop("find_tracks_by_ids", {})
    repo.find_tracks_by_isrcs.return_value = overrides.pop("find_tracks_by_isrcs", {})
    repo.find_tracks_by_title_artist.return_value = overrides.pop(
        "find_tracks_by_title_artist", {}
    )
    repo.save_track.side_effect = overrides.pop("save_track", None)
    repo.save_tracks.return_value = overrides.pop("save_tracks", [])
    repo.fill_blank_metadata.side_effect = overrides.pop("fill_blank_metadata", list)
    repo.find_duplicate_tracks_by_fingerprint.return_value = overrides.pop(
        "find_duplicate_tracks_by_fingerprint", []
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_playlist_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`PlaylistRepositoryProtocol`."""
    repo = protocol_mock(PlaylistRepositoryProtocol)
    repo.get_playlist_by_id.return_value = overrides.pop("get_playlist_by_id", None)
    repo.get_playlist_by_connector.return_value = overrides.pop(
        "get_playlist_by_connector", None
    )
    repo.save_playlist.side_effect = overrides.pop("save_playlist", lambda p: p)
    repo.delete_playlist.return_value = overrides.pop("delete_playlist", True)
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_connector_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`ConnectorRepositoryProtocol`."""
    repo = protocol_mock(ConnectorRepositoryProtocol)
    repo.find_tracks_by_connectors.return_value = overrides.pop(
        "find_tracks_by_connectors", {}
    )
    repo.find_tracks_by_connector_track_ids.return_value = overrides.pop(
        "find_tracks_by_connector_track_ids", {}
    )
    repo.upsert_connector_tracks.return_value = overrides.pop(
        "upsert_connector_tracks", {}
    )
    repo.touch_last_seen.return_value = overrides.pop("touch_last_seen", None)
    repo.map_track_to_connector.return_value = overrides.pop(
        "map_track_to_connector", None
    )
    repo.map_tracks_to_connectors.return_value = overrides.pop(
        "map_tracks_to_connectors", []
    )
    repo.get_full_mappings_for_track.return_value = overrides.pop(
        "get_full_mappings_for_track", []
    )
    repo.find_multiple_primary_violations.return_value = overrides.pop(
        "find_multiple_primary_violations", []
    )
    repo.find_missing_primary_violations.return_value = overrides.pop(
        "find_missing_primary_violations", []
    )
    repo.count_orphaned_connector_tracks.return_value = overrides.pop(
        "count_orphaned_connector_tracks", 0
    )
    repo.get_match_method_stats.return_value = overrides.pop(
        "get_match_method_stats", []
    )
    repo.ensure_connector_tracks.return_value = overrides.pop(
        "ensure_connector_tracks", {}
    )
    repo.count_confidence_evidence_divergence.return_value = overrides.pop(
        "count_confidence_evidence_divergence", 0
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_checkpoint_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`CheckpointRepositoryProtocol`."""
    repo = protocol_mock(CheckpointRepositoryProtocol)
    repo.get_sync_checkpoint.return_value = overrides.pop("get_sync_checkpoint", None)
    repo.save_sync_checkpoint.return_value = overrides.pop("save_sync_checkpoint", None)
    # Mirrors the real non-persisting-on-miss semantics: a fresh entity, not None.
    repo.get_or_create_sync_checkpoint.side_effect = (
        lambda user_id, service, entity_type: SyncCheckpoint(
            user_id=user_id, service=service, entity_type=entity_type
        )
    )
    # Poll lease defaults to *won*, so a test that isn't about single-flight
    # exercises the whole poll path. Tests for the losing branch pass
    # ``try_claim_poll=AsyncMock(return_value=False)``.
    repo.try_claim_poll.return_value = overrides.pop("try_claim_poll", True)
    repo.finish_poll.return_value = None
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_like_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`LikeRepositoryProtocol`."""
    repo = protocol_mock(LikeRepositoryProtocol)
    repo.save_track_likes_batch.return_value = overrides.pop(
        "save_track_likes_batch", []
    )
    repo.delete_track_likes_batch.return_value = overrides.pop(
        "delete_track_likes_batch", 0
    )
    repo.get_liked_status_batch.return_value = overrides.pop(
        "get_liked_status_batch", {}
    )
    repo.get_all_liked_tracks.return_value = overrides.pop("get_all_liked_tracks", [])
    repo.count_liked_tracks.return_value = overrides.pop("count_liked_tracks", 0)
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_artist_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`ArtistRepositoryProtocol`."""
    repo = protocol_mock(ArtistRepositoryProtocol)
    repo.get_artist_by_id.return_value = overrides.pop("get_artist_by_id", None)
    repo.save_artists.return_value = overrides.pop("save_artists", [])
    repo.list_artists.return_value = overrides.pop(
        "list_artists",
        {
            "artists": [],
            "total": 0,
            "track_counts": {},
            "favorited_ids": set(),
            "connector_names": {},
            "next_page_key": None,
        },
    )
    repo.list_needing_enrichment.return_value = overrides.pop(
        "list_needing_enrichment", []
    )
    repo.count_tracks_by_artist.return_value = overrides.pop(
        "count_tracks_by_artist", {}
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_artist_connector_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`ArtistConnectorRepositoryProtocol`."""
    repo = protocol_mock(ArtistConnectorRepositoryProtocol)
    repo.bulk_upsert_connector_artists.return_value = overrides.pop(
        "bulk_upsert_connector_artists", {}
    )
    repo.find_connector_artists.return_value = overrides.pop(
        "find_connector_artists", {}
    )
    repo.ensure_connector_artists.return_value = overrides.pop(
        "ensure_connector_artists", {}
    )
    repo.find_artists_by_connector_artist_ids.return_value = overrides.pop(
        "find_artists_by_connector_artist_ids", {}
    )
    repo.get_full_mappings_for_artist.return_value = overrides.pop(
        "get_full_mappings_for_artist", []
    )
    repo.ensure_primaries.return_value = overrides.pop("ensure_primaries", [])
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_artist_favorite_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`ArtistFavoriteRepositoryProtocol`."""
    repo = protocol_mock(ArtistFavoriteRepositoryProtocol)
    repo.favorite.return_value = overrides.pop("favorite", True)
    repo.unfavorite.return_value = overrides.pop("unfavorite", True)
    repo.get_favorite_status_batch.return_value = overrides.pop(
        "get_favorite_status_batch", set()
    )
    repo.get_favorite_artist_ids.return_value = overrides.pop(
        "get_favorite_artist_ids", frozenset()
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_artist_alias_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`ArtistAliasRepositoryProtocol`."""
    repo = protocol_mock(ArtistAliasRepositoryProtocol)
    repo.replace_aliases.return_value = overrides.pop("replace_aliases", 0)
    repo.get_artist_equivalence.return_value = overrides.pop(
        "get_artist_equivalence", EMPTY_EQUIVALENCE
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_artist_minter(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`ArtistMinterProtocol`."""
    from src.domain.repositories.artist import ArtistMintSummary

    minter = protocol_mock(ArtistMinterProtocol)
    minter.mint.return_value = overrides.pop("mint", ArtistMintSummary())
    for k, v in overrides.items():
        setattr(minter, k, v)
    return minter


def make_mock_artist_enrichment_provider(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`ArtistEnrichmentProviderProtocol`."""
    provider = protocol_mock(ArtistEnrichmentProviderProtocol)
    provider.search_artist.return_value = overrides.pop("search_artist", [])
    provider.lookup_artist.return_value = overrides.pop("lookup_artist", None)
    for k, v in overrides.items():
        setattr(provider, k, v)
    return provider


def make_mock_plays_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`PlaysRepositoryProtocol`."""
    repo = protocol_mock(PlaysRepositoryProtocol)
    repo.bulk_insert_plays.return_value = overrides.pop("bulk_insert_plays", (0, 0))
    repo.get_play_aggregations.return_value = overrides.pop("get_play_aggregations", {})
    repo.recompute_track_play_aggregates.return_value = overrides.pop(
        "recompute_track_play_aggregates", 0
    )
    repo.list_play_events.return_value = overrides.pop("list_play_events", ([], None))
    repo.get_play_histogram.return_value = overrides.pop("get_play_histogram", [])
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_metrics_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`MetricsRepositoryProtocol`."""
    repo = protocol_mock(MetricsRepositoryProtocol)
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_workflow_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`WorkflowRepositoryProtocol`."""
    repo = protocol_mock(WorkflowRepositoryProtocol)
    repo.list_workflows.return_value = overrides.pop("list_workflows", [])
    repo.get_workflow_by_id.return_value = overrides.pop("get_workflow_by_id", None)
    repo.save_workflow.side_effect = overrides.pop("save_workflow", lambda w: w)
    repo.delete_workflow.return_value = overrides.pop("delete_workflow", True)
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_playlist_link_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`PlaylistLinkRepositoryProtocol`."""
    repo = protocol_mock(PlaylistLinkRepositoryProtocol)
    repo.get_links_for_playlist.return_value = overrides.pop(
        "get_links_for_playlist", []
    )
    repo.get_link.return_value = overrides.pop("get_link", None)
    repo.create_link.side_effect = overrides.pop(
        "create_link", lambda link, *, user_id: link
    )
    repo.delete_link.return_value = overrides.pop("delete_link", True)
    repo.update_sync_status.return_value = overrides.pop("update_sync_status", None)
    repo.list_by_user_connector.return_value = overrides.pop(
        "list_by_user_connector", []
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_sync_base_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`PlaylistSyncBaseRepositoryProtocol`."""
    repo = protocol_mock(PlaylistSyncBaseRepositoryProtocol)
    repo.get_for_link.return_value = overrides.pop("get_for_link", None)
    repo.upsert.side_effect = overrides.pop("upsert", lambda base: base)
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_match_review_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`MatchReviewRepositoryProtocol`."""
    repo = protocol_mock(MatchReviewRepositoryProtocol)
    repo.list_pending_reviews.return_value = overrides.pop(
        "list_pending_reviews", ([], 0)
    )
    repo.get_review_by_id.return_value = overrides.pop("get_review_by_id", None)
    # Echoes its input: the real repository returns the rows it actually wrote,
    # and "all of them" is the right default. A test that needs the guard to
    # skip a row overrides this with a narrower list.
    repo.create_reviews_batch.side_effect = overrides.pop("create_reviews_batch", list)
    # Must stay a real frozenset: callers test membership against it, and an
    # AsyncMock's default return is a truthy Mock that swallows every `not in`.
    repo.existing_review_keys.return_value = overrides.pop(
        "existing_review_keys", frozenset()
    )
    repo.update_review_status.return_value = overrides.pop("update_review_status", None)
    repo.count_pending.return_value = overrides.pop("count_pending", 0)
    repo.count_stale_pending.return_value = overrides.pop("count_stale_pending", 0)
    repo.count_created_since.return_value = overrides.pop("count_created_since", 0)
    repo.count_pending_by_method.return_value = overrides.pop(
        "count_pending_by_method", {}
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_preference_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`PreferenceRepositoryProtocol`."""
    repo = protocol_mock(PreferenceRepositoryProtocol)
    repo.get_preferences.return_value = overrides.pop("get_preferences", {})
    repo.set_preferences.side_effect = overrides.pop(
        "set_preferences", lambda prefs, **kw: list(prefs)
    )
    repo.remove_preferences.return_value = overrides.pop("remove_preferences", 0)
    repo.add_events.side_effect = overrides.pop(
        "add_events", lambda events, **kw: list(events)
    )
    repo.list_by_state.return_value = overrides.pop("list_by_state", [])
    repo.count_by_state.return_value = overrides.pop("count_by_state", {})
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_tag_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`TagRepositoryProtocol`."""
    repo = protocol_mock(TagRepositoryProtocol)
    repo.get_tags.return_value = overrides.pop("get_tags", {})
    repo.add_tags.side_effect = overrides.pop("add_tags", lambda tags, **kw: list(tags))
    repo.remove_tags.side_effect = overrides.pop(
        "remove_tags", lambda pairs, **kw: list(pairs)
    )
    repo.add_events.side_effect = overrides.pop(
        "add_events", lambda events, **kw: list(events)
    )
    repo.list_tags.return_value = overrides.pop("list_tags", [])
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_connector_playlist_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`ConnectorPlaylistRepositoryProtocol`."""
    repo = protocol_mock(ConnectorPlaylistRepositoryProtocol)
    repo.upsert_model.side_effect = overrides.pop("upsert_model", lambda cp: cp)
    repo.list_summaries_by_connector.return_value = overrides.pop(
        "list_summaries_by_connector", []
    )
    repo.find_by_ids.return_value = overrides.pop("find_by_ids", [])
    repo.bulk_upsert_models.side_effect = overrides.pop("bulk_upsert_models", list)
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_metric_config() -> MagicMock:
    """Build a ``MagicMock`` mimicking :class:`MetricConfigProvider` protocol."""
    return MagicMock()


def make_mock_playlist_assignment_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`PlaylistAssignmentRepositoryProtocol`."""
    repo = protocol_mock(PlaylistAssignmentRepositoryProtocol)
    repo.list_for_user.return_value = overrides.pop("list_for_user", [])
    repo.list_for_ids.side_effect = overrides.pop(
        "list_for_ids",
        lambda ids, **kw: [],
    )
    repo.list_for_connector_playlist.return_value = overrides.pop(
        "list_for_connector_playlist", []
    )
    repo.list_for_connector_playlist_ids.side_effect = overrides.pop(
        "list_for_connector_playlist_ids",
        lambda ids, **kw: {},
    )
    repo.create_assignments.side_effect = overrides.pop(
        "create_assignments", lambda assignments, **kw: list(assignments)
    )
    repo.delete_assignment.return_value = overrides.pop("delete_assignment", True)
    repo.get_members_for_assignments.return_value = overrides.pop(
        "get_members_for_assignments", {}
    )
    repo.replace_members_for_assignments.side_effect = overrides.pop(
        "replace_members_for_assignments",
        lambda snapshots, **kw: sum(len(m) for m in snapshots.values()),
    )
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


def make_mock_workflow_run_repo(**overrides) -> NonCallableMagicMock:
    """Build a mock of :class:`WorkflowRunRepositoryProtocol`."""
    repo = protocol_mock(WorkflowRunRepositoryProtocol)
    repo.create_run.side_effect = overrides.pop("create_run", lambda r: r)
    # update_run_status returns bool: True = a row was transitioned (default),
    # False = a guarded terminal write no-op'd (already terminal).
    repo.update_run_status.return_value = overrides.pop("update_run_status", True)
    repo.save_node_record.side_effect = overrides.pop("save_node_record", lambda n: n)
    repo.update_node_status.return_value = overrides.pop("update_node_status", None)
    repo.get_runs_for_workflow.return_value = overrides.pop(
        "get_runs_for_workflow", ([], 0)
    )
    repo.get_active_runs_for_user.return_value = overrides.pop(
        "get_active_runs_for_user", ([], 0)
    )
    repo.get_run_by_id.return_value = overrides.pop("get_run_by_id", None)
    repo.get_latest_run_for_workflow.return_value = overrides.pop(
        "get_latest_run_for_workflow", None
    )
    repo.get_run_summaries_for_workflows.return_value = overrides.pop(
        "get_run_summaries_for_workflows", ({}, {})
    )
    repo.bump_heartbeat.return_value = overrides.pop("bump_heartbeat", None)
    repo.list_stalled_runs.return_value = overrides.pop("list_stalled_runs", [])
    for k, v in overrides.items():
        setattr(repo, k, v)
    return repo


# ---------------------------------------------------------------------------
# Progress tracking helpers
# ---------------------------------------------------------------------------


def make_tracking_emitter(uow: MagicMock) -> tuple[AsyncMock, list[str]]:
    """Build a mock ``ProgressEmitter`` that records call order with ``uow.commit``.

    Returns ``(emitter, call_order)`` where *call_order* tracks ``"commit"``
    and ``"complete"`` events — useful for asserting that ``uow.commit()``
    fires before ``complete_operation()``.
    """
    call_order: list[str] = []

    uow.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

    emitter = AsyncMock(spec=ProgressEmitter)
    emitter.start_operation = AsyncMock(return_value="op-id")
    emitter.complete_operation = AsyncMock(
        side_effect=lambda *_: call_order.append("complete")
    )
    emitter.emit_progress = AsyncMock()

    return emitter, call_order


# ---------------------------------------------------------------------------
# UnitOfWork mock
# ---------------------------------------------------------------------------


def make_mock_resolution_recorder() -> NonCallableMagicMock:
    """Recorder mock that records everything and suppresses nothing.

    ``active_rejections`` and ``backoff_suppressed`` return empty on purpose:
    the failure that matters is a real match silently withheld, so the default
    has to be the permissive one.

    ``matcher_version`` is set when the mock is built rather than assigned
    after it — the protocol declares a read-only property, and a test that
    assigns to it is asserting a capability production code does not have.
    """
    recorder = protocol_mock(ResolutionRecorderProtocol, matcher_version="test-matcher")
    recorder.record.return_value = 0
    recorder.record_supersessions.return_value = 0
    recorder.retire_mapping.return_value = True
    recorder.active_rejections.return_value = frozenset()
    recorder.remember_rejections.return_value = 0
    recorder.remember_no_match.return_value = 0
    recorder.clear_negatives.return_value = 0
    recorder.backoff_suppressed.return_value = frozenset()
    # A real mapping that knows every id asked about: the substitution seam
    # calls .get() on the result, and the collision-review step needs a row
    # id per deferred creation. An unconfigured AsyncMock child there sheds
    # an unawaited coroutine per event.
    recorder.connector_track_ids.side_effect = lambda identifiers, *, connector_name: {
        identifier: uuid7() for identifier in identifiers
    }
    return recorder


def attach_resolution_recorder(uow: MagicMock) -> NonCallableMagicMock:
    """Wire a permissive recorder onto a hand-rolled UoW mock.

    Connector tests build their own bare ``MagicMock()`` UoWs rather than using
    ``make_mock_uow``; the resolvers now ask those for a recorder, and a bare
    MagicMock hands back something that cannot be awaited.
    """
    recorder = make_mock_resolution_recorder()
    uow.get_resolution_recorder.return_value = recorder
    return recorder


def attach_artist_alias_repo(uow: MagicMock) -> NonCallableMagicMock:
    """Wire an alias cache that holds no aliases onto a hand-rolled UoW mock.

    The canonical-reuse step reads the batch's alias groups before its
    title/artist probe; a bare MagicMock there cannot be awaited.
    """
    repo = make_mock_artist_alias_repo()
    uow.get_artist_alias_repository.return_value = repo
    return repo


def attach_match_review_repo(uow: MagicMock) -> NonCallableMagicMock:
    """Wire a match-review repository onto a hand-rolled UoW mock.

    The inward resolvers queue a suspect ISRC collision's review through
    ``create_reviews_batch``; a bare MagicMock there cannot be awaited.
    """
    repo = make_mock_match_review_repo()
    uow.get_match_review_repository.return_value = repo
    return repo


def make_mock_uow(**repo_overrides) -> MagicMock:
    """Build a mock UnitOfWork with pre-wired repository mocks.

    All repositories are eagerly created and accessible via both
    ``uow.get_*_repository()`` (call) and ``uow.get_*_repository.return_value``
    (attribute access) for compatibility with existing test patterns.

    Parameters
    ----------
    track_repo, playlist_repo, connector_repo, checkpoint_repo, like_repo,
    plays_repo, metrics_repo : mock, optional
        Pre-built repository mocks. If omitted, a default is created.
    connector_provider : MagicMock, optional
        Pre-built service connector provider.

    Returns
    -------
    MagicMock
        A mock UoW whose ``get_*_repository()`` methods return mocks specced
        by :func:`protocol_mock` and whose async-context-manager protocol is
        wired up.
    """
    uow = MagicMock()

    uow.get_track_repository = MagicMock(
        return_value=repo_overrides.get("track_repo", make_mock_track_repo())
    )
    uow.get_playlist_repository = MagicMock(
        return_value=repo_overrides.get("playlist_repo", make_mock_playlist_repo())
    )
    uow.get_connector_repository = MagicMock(
        return_value=repo_overrides.get("connector_repo", make_mock_connector_repo())
    )
    uow.get_checkpoint_repository = MagicMock(
        return_value=repo_overrides.get("checkpoint_repo", make_mock_checkpoint_repo())
    )
    uow.get_like_repository = MagicMock(
        return_value=repo_overrides.get("like_repo", make_mock_like_repo())
    )
    uow.get_plays_repository = MagicMock(
        return_value=repo_overrides.get("plays_repo", make_mock_plays_repo())
    )
    uow.get_artist_repository = MagicMock(
        return_value=repo_overrides.get("artist_repo", make_mock_artist_repo())
    )
    uow.get_artist_connector_repository = MagicMock(
        return_value=repo_overrides.get(
            "artist_connector_repo", make_mock_artist_connector_repo()
        )
    )
    uow.get_artist_favorite_repository = MagicMock(
        return_value=repo_overrides.get(
            "artist_favorite_repo", make_mock_artist_favorite_repo()
        )
    )
    uow.get_artist_alias_repository = MagicMock(
        return_value=repo_overrides.get(
            "artist_alias_repo", make_mock_artist_alias_repo()
        )
    )
    uow.get_artist_minter = MagicMock(
        return_value=repo_overrides.get("artist_minter", make_mock_artist_minter())
    )
    uow.get_artist_enrichment_provider = MagicMock(
        return_value=repo_overrides.get(
            "artist_enrichment_provider", make_mock_artist_enrichment_provider()
        )
    )
    uow.get_metrics_repository = MagicMock(
        return_value=repo_overrides.get("metrics_repo", make_mock_metrics_repo())
    )
    uow.get_workflow_repository = MagicMock(
        return_value=repo_overrides.get("workflow_repo", make_mock_workflow_repo())
    )
    uow.get_workflow_run_repository = MagicMock(
        return_value=repo_overrides.get(
            "workflow_run_repo", make_mock_workflow_run_repo()
        )
    )
    default_version_repo = protocol_mock(WorkflowVersionRepositoryProtocol)
    default_version_repo.get_max_version_number.return_value = 0
    uow.get_workflow_version_repository = MagicMock(
        return_value=repo_overrides.get("workflow_version_repo", default_version_repo)
    )

    uow.get_playlist_link_repository = MagicMock(
        return_value=repo_overrides.get(
            "playlist_link_repo", make_mock_playlist_link_repo()
        )
    )
    uow.get_playlist_sync_base_repository = MagicMock(
        return_value=repo_overrides.get("sync_base_repo", make_mock_sync_base_repo())
    )
    uow.get_connector_playlist_repository = MagicMock(
        return_value=repo_overrides.get(
            "connector_playlist_repo", make_mock_connector_playlist_repo()
        )
    )
    uow.get_match_review_repository = MagicMock(
        return_value=repo_overrides.get(
            "match_review_repo", make_mock_match_review_repo()
        )
    )
    uow.get_preference_repository = MagicMock(
        return_value=repo_overrides.get("preference_repo", make_mock_preference_repo())
    )
    uow.get_tag_repository = MagicMock(
        return_value=repo_overrides.get("tag_repo", make_mock_tag_repo())
    )
    uow.get_playlist_assignment_repository = MagicMock(
        return_value=repo_overrides.get(
            "playlist_assignment_repo",
            make_mock_playlist_assignment_repo(),
        )
    )

    uow.get_stats_repository = MagicMock(
        return_value=repo_overrides.get(
            "stats_repo", protocol_mock(StatsRepositoryProtocol)
        )
    )

    uow.get_connector_play_repository = MagicMock(
        return_value=repo_overrides.get(
            "connector_play_repo", protocol_mock(ConnectorPlayRepositoryProtocol)
        )
    )

    uow.get_schedule_repository = MagicMock(
        return_value=repo_overrides.get(
            "schedule_repo", protocol_mock(ScheduleRepositoryProtocol)
        )
    )

    uow.get_chat_feedback_repository = MagicMock(
        return_value=repo_overrides.get(
            "chat_feedback_repo", protocol_mock(ChatFeedbackRepositoryProtocol)
        )
    )

    # Identity write seam (v0.10.2). Defaults return "nothing suppressed" so a
    # use-case test that does not care about the negative cache behaves exactly
    # as it did before the seam existed.
    uow.get_resolution_event_repository = MagicMock(
        return_value=repo_overrides.get(
            "resolution_event_repo", protocol_mock(ResolutionEventRepositoryProtocol)
        )
    )
    uow.get_resolution_negative_repository = MagicMock(
        return_value=repo_overrides.get(
            "resolution_negative_repo",
            protocol_mock(ResolutionNegativeRepositoryProtocol),
        )
    )
    uow.get_resolution_recorder = MagicMock(
        return_value=repo_overrides.get(
            "resolution_recorder", make_mock_resolution_recorder()
        )
    )

    # Connector provider (optional override)
    uow.get_service_connector_provider = MagicMock(
        return_value=repo_overrides.get(
            "connector_provider", make_mock_connector_provider()
        )
    )

    # Async context manager protocol
    uow.commit = AsyncMock()
    uow.commit_batch = AsyncMock()
    uow.rollback = AsyncMock()

    # No-op savepoint scope mirroring UnitOfWorkProtocol.savepoint(): item
    # loops enter it around tolerated per-item writes; in unit tests there is
    # no transaction to protect, so it only needs to be enterable.
    @asynccontextmanager
    async def _savepoint():
        yield

    uow.savepoint = MagicMock(side_effect=_savepoint)
    uow.__aenter__ = AsyncMock(return_value=uow)

    async def _aexit(exc_type, _exc_val, _exc_tb):
        # Mirror the real DatabaseUnitOfWork.__aexit__ rollback contract so
        # tests can assert "failure → rollback" without use cases needing an
        # explicit (redundant) rollback call. The clean-exit auto-commit half
        # is deliberately NOT emulated: tests assert exact commit counts.
        if exc_type is not None:
            await uow.rollback()

    uow.__aexit__ = AsyncMock(side_effect=_aexit)

    return uow


def make_mock_uow_with_connector(
    get_playlist_return=None,
    **uow_overrides,
) -> tuple[MagicMock, AsyncMock]:
    """Build a mock UoW wired with an AsyncMock playlist connector.

    Returns ``(uow, connector)``. The connector is specced against
    ``PlaylistConnector`` so ``resolve_playlist_connector``'s ``isinstance``
    narrowing accepts it — an unspecced mock builds its members lazily via
    ``__getattr__``, which protocol runtime checks deliberately do not see.
    Additional uow_overrides are forwarded to ``make_mock_uow``.
    """
    connector = AsyncMock(spec=PlaylistConnector)
    if get_playlist_return is not None:
        connector.get_playlist.return_value = get_playlist_return

    uow = make_mock_uow(
        connector_provider=make_mock_connector_provider(connector), **uow_overrides
    )
    return uow, connector
