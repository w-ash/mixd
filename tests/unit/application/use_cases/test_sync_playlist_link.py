"""Unit tests for SyncPlaylistLinkUseCase — the status-lifecycle wrapper.

The engine's behaviour (fresh fetch, diff, safety, apply) is tested in
test_playlist_reconciliation_engine. Here we only verify the wrapper's job: the
SYNCING → SYNCED / ERROR transitions, that a connector failure routes to ERROR
and re-raises, and that a confirmation-required restores the prior status.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

import pytest

from src.application.services.playlist_reconciliation_engine import (
    PlaylistReconciliationEngine,
    ReconcileResult,
)
from src.application.use_cases.sync_playlist_link import (
    SyncPlaylistLinkCommand,
    SyncPlaylistLinkResult,
    SyncPlaylistLinkUseCase,
    to_operation_result,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import ConfirmationRequiredError, ConnectorSyncError
from tests.fixtures import make_mock_uow

_RESOLVER = "src.application.use_cases._shared.playlist_resolver.require_playlist_link"


def _link(status: SyncStatus = SyncStatus.NEVER_SYNCED) -> PlaylistLink:
    return PlaylistLink(
        id=uuid7(),
        playlist_id=uuid7(),
        connector_name="spotify",
        connector_playlist_identifier="ext1",
        sync_direction=SyncDirection.PULL,
        sync_status=status,
    )


def _command(link: PlaylistLink, *, confirmed: bool = False) -> SyncPlaylistLinkCommand:
    return SyncPlaylistLinkCommand(user_id="u", link_id=link.id, confirmed=confirmed)


class TestStatusLifecycle:
    async def test_success_marks_synced_with_counts(self):
        link = _link()
        uow = make_mock_uow()
        link_repo = uow.get_playlist_link_repository()
        link_repo.get_link = AsyncMock(return_value=link)
        result = ReconcileResult(
            direction=SyncDirection.PULL,
            tracks_added=4,
            tracks_removed=1,
            unresolved=2,
        )

        with (
            patch(_RESOLVER, AsyncMock(return_value=link)),
            patch.object(
                PlaylistReconciliationEngine, "apply", AsyncMock(return_value=result)
            ),
        ):
            out = await SyncPlaylistLinkUseCase().execute(_command(link), uow)

        assert out.tracks_added == 4
        assert out.tracks_removed == 1
        assert out.tracks_unmatched == 2
        statuses = [c.args[1] for c in link_repo.update_sync_status.call_args_list]
        assert SyncStatus.SYNCING in statuses
        assert SyncStatus.SYNCED in statuses

    async def test_connector_failure_marks_error_and_reraises(self):
        link = _link()
        uow = make_mock_uow()
        with (
            patch(_RESOLVER, AsyncMock(return_value=link)),
            patch.object(
                PlaylistReconciliationEngine,
                "apply",
                AsyncMock(side_effect=ConnectorSyncError("spotify", "boom")),
            ),
        ):
            with pytest.raises(ConnectorSyncError):
                await SyncPlaylistLinkUseCase().execute(_command(link), uow)

        statuses = [
            c.args[1]
            for c in uow.get_playlist_link_repository().update_sync_status.call_args_list
        ]
        assert SyncStatus.ERROR in statuses

    async def test_confirmation_required_restores_prior_status(self):
        link = _link(status=SyncStatus.SYNCED)
        uow = make_mock_uow()
        with (
            patch(_RESOLVER, AsyncMock(return_value=link)),
            patch.object(
                PlaylistReconciliationEngine,
                "apply",
                AsyncMock(
                    side_effect=ConfirmationRequiredError(
                        "destructive", removals=99, total=100, remaining=1
                    )
                ),
            ),
        ):
            with pytest.raises(ConfirmationRequiredError):
                await SyncPlaylistLinkUseCase().execute(_command(link), uow)

        statuses = [
            c.args[1]
            for c in uow.get_playlist_link_repository().update_sync_status.call_args_list
        ]
        # Restored to the prior status, never marked ERROR for a confirmation.
        assert SyncStatus.SYNCED in statuses
        assert SyncStatus.ERROR not in statuses


class TestToOperationResult:
    """The SSE-seam mapper flattens a successful sync into audit counts."""

    def test_maps_added_removed_unmatched(self):
        counts = to_operation_result(
            SyncPlaylistLinkResult(
                link=_link(),
                tracks_added=4,
                tracks_removed=1,
                tracks_unmatched=2,
            )
        ).to_counts()
        assert counts["tracks_added"] == 4
        assert counts["tracks_removed"] == 1
        assert counts["tracks_unmatched"] == 2

    def test_omits_unmatched_when_zero_and_not_failure(self):
        op = to_operation_result(
            SyncPlaylistLinkResult(link=_link(), tracks_added=2, tracks_removed=0)
        )
        assert "tracks_unmatched" not in op.to_counts()
        assert op.is_failure is False
