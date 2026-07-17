"""Regression: a web two-phase import must persist BOTH connector_plays and track_plays.

Pins the SSE-seam data-loss bug (v0.8.5 Story 2). On the *web* path the import runs
under an ``OperationBoundEmitter``. Phase 1 (ingestion) and phase 2 (resolution) each
open a ``tracked_operation`` on that same request-bound emitter. The old emitter
**rebound every operation to one id** and the domain ``OperationLedger`` **never
evicted** completed ops — so phase-2's ``start_operation`` raised
"... already being tracked", which ``ImportTracksUseCase.execute`` swallowed into a
soft-failure result. Phase 1 commits ``connector_plays`` *before* phase 2, so those
rows stranded with ``resolved_track_id IS NULL`` while ``track_plays`` were never
created — and the audit row still said the run finished.

The CLI/scheduler are immune (NullProgressEmitter → a fresh uuid4 per phase, no
coordinator collision), which is exactly why every existing import test passes: they
run with no emitter. The single distinguishing ingredient here is a real
``OperationBoundEmitter``.

On the pre-fix tree this test FAILS (track_plays == 0). After the lifecycle inversion
(stop rebinding → inject ``parent_operation_id``; evict completed ops) it passes.
"""

from datetime import UTC, datetime
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from src.application.services.progress_broker import get_progress_broker
from src.application.use_cases.import_play_history import (
    ImportTracksCommand,
    ImportTracksUseCase,
)
from src.domain.entities import PlayRecord
from src.domain.entities.operations import ConnectorTrackPlay, TrackPlay
from src.domain.repositories.play import PlayResolutionOutcome
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBTrackPlay,
)
from src.interface.api.services.progress import OperationBoundEmitter


class _FakeResolver:
    """Resolves every connector play to one pre-seeded track.

    Keeps phase 2 deterministic and network-free: the only thing under test is
    whether phase 2 *runs at all* under the bound emitter, not the matching logic.
    """

    def __init__(self, track_id: UUID) -> None:
        self._track_id = track_id

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: object,
        *,
        user_id: str,
        progress_callback: object = None,
    ) -> PlayResolutionOutcome:
        plays = [
            TrackPlay(
                track_id=self._track_id,
                service=cp.service,
                played_at=cp.played_at,
                user_id=user_id,
            )
            for cp in connector_plays
        ]
        return PlayResolutionOutcome(
            track_plays=plays,
            metrics={
                "error_count": 0,
                "new_tracks_count": 0,
                "updated_tracks_count": 0,
            },
            resolutions=tuple((cp, self._track_id) for cp in connector_plays),
        )


@pytest.mark.slow
class TestWebImportPersistsBothPlayKinds:
    """The data-loss regression for the v0.8.5 SSE-seam inversion."""

    @pytest.fixture
    def unit_of_work(self, db_session):
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )

        return get_unit_of_work(db_session)

    async def test_two_phase_import_under_bound_emitter_creates_track_plays(
        self, unit_of_work, db_session, test_data_tracker
    ):
        user_id = f"seam_user_{uuid4().hex[:8]}"

        # Seed a real track so the resolved TrackPlay's FK is satisfiable.
        from src.domain.entities import Artist, Track

        track = Track(title="Seam Song", artists=[Artist(name="Seam Artist")])
        seeded = await unit_of_work.get_track_repository().save_track(track)
        await unit_of_work.commit()
        test_data_tracker.add_track(seeded.id)

        async def mock_get_recent_tracks(*_args, **kwargs):
            from_time = kwargs.get("from_time")
            if from_time and from_time.day == 1:
                return [
                    PlayRecord(
                        track_name="Seam Song",
                        artist_name="Seam Artist",
                        played_at=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
                        service="lastfm",
                        service_metadata={},
                    )
                ]
            return []

        async def fake_create_resolver(
            _service: str, _uow: object = None
        ) -> _FakeResolver:
            return _FakeResolver(seeded.id)

        with (
            patch(
                "src.infrastructure.connectors.lastfm.connector.LastFMConnector"
            ) as mock_connector_class,
            patch(
                "src.infrastructure.services.play_import_registry.PlayImportServiceRegistry.create_play_resolver",
                new=fake_create_resolver,
            ),
        ):
            mock_connector = Mock()
            mock_connector.lastfm_username = user_id
            mock_connector.get_recent_tracks = mock_get_recent_tracks
            mock_connector_class.return_value = mock_connector

            # THE web-path ingredient: a request-bound emitter, as the SSE seam builds.
            emitter = OperationBoundEmitter(
                delegate=get_progress_broker(),
                operation_id=str(uuid4()),
            )

            command = ImportTracksCommand(
                service="lastfm",
                mode="incremental",
                user_id=user_id,
                from_date=datetime(2024, 1, 1, tzinfo=UTC),
                to_date=datetime(2024, 1, 2, tzinfo=UTC),
            )

            result = await ImportTracksUseCase().execute(
                command, unit_of_work, progress_emitter=emitter
            )

        # Count all rows in this transaction-isolated test session. We don't filter
        # by user_id: the real importer still saves connector_plays under the
        # entity-default "default" user (it isn't threaded the command's user_id —
        # the 6b bug WS-3 fixes), while resolution keys track_plays by the command
        # user. The data-loss assertion is "both kinds exist", independent of tenant.
        connector_play_count = await db_session.scalar(
            select(func.count()).select_from(DBConnectorPlay)
        )
        # Phase 2 only runs if the bound-emitter collision is fixed.
        track_play_count = await db_session.scalar(
            select(func.count()).select_from(DBTrackPlay)
        )

        assert connector_play_count >= 1, "phase 1 must ingest connector_plays"
        assert track_play_count >= 1, (
            "phase 2 (track resolution) silently never ran under the bound emitter — "
            "the SSE-seam data-loss bug stranded connector_plays with no track_plays"
        )
        # And the run must not be reported as a soft failure.
        assert result.operation_result.summary_metrics.get("errors") == 0
