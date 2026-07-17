"""Integration tests for the play projection pipeline (v0.10.0).

Exercises PlayProjectionService against a real database: diff-apply's
insert/update/merge/delete arms, play_sources membership, and the property
the whole milestone exists for — projecting the same ledger again changes
nothing, and opposite arrival orders converge to the same canonical state.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa

from src.application.services.play_projection_service import PlayProjectionService
from src.domain.entities import ConnectorTrackPlay, PlaySource, TrackPlay
from src.infrastructure.persistence.database.db_models import (
    DBPlaySource,
    DBTrackPlay,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track

_START = datetime(2024, 11, 5, 9, 15, 0, tzinfo=UTC)
_MS = 201_000
_WINDOW = (_START - timedelta(days=1), _START + timedelta(days=1))


def _scrobble(user_id: str, *, title: str = "Striptease") -> ConnectorTrackPlay:
    """Last.fm observation stamping the true start."""
    return ConnectorTrackPlay(
        service="lastfm",
        artist_name="Carwash",
        track_name=title,
        played_at=_START + timedelta(seconds=2),
        ms_played=None,
        user_id=user_id,
        service_metadata={"loved": True},
        import_timestamp=datetime.now(UTC),
        import_source="lastfm_api",
        import_batch_id=f"TEST_{uuid4()}",
    )


def _export(user_id: str, *, title: str = "Striptease") -> ConnectorTrackPlay:
    """GDPR export observation stamping the end of the same listen."""
    return ConnectorTrackPlay(
        service="spotify",
        artist_name="Carwash",
        track_name=title,
        played_at=_START + timedelta(milliseconds=_MS),
        ms_played=_MS,
        user_id=user_id,
        service_metadata={
            "track_uri": "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
            "platform": "ios",
        },
        import_timestamp=datetime.now(UTC),
        import_source="spotify_export",
        import_batch_id=f"TEST_{uuid4()}",
    )


async def _seed_resolved(
    uow, user_id: str, entries: list[ConnectorTrackPlay], track_id
):
    connector_repo = uow.get_connector_play_repository()
    _ = await connector_repo.bulk_insert_connector_plays(entries)
    _ = await connector_repo.bulk_update_resolution(
        [(entry, track_id) for entry in entries], resolved_at=datetime.now(UTC)
    )


async def _canonical_state(db_session, user_id: str):
    plays = (
        (
            await db_session.execute(
                sa.select(DBTrackPlay).where(DBTrackPlay.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    sources = (
        (
            await db_session.execute(
                sa.select(DBPlaySource).where(DBPlaySource.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    return plays, sources


class TestProjectionPipeline:
    async def test_two_sources_project_to_one_play_with_membership(self, db_session):
        user_id = f"TEST_proj_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Striptease",
                artist="Carwash",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )
        scrobble, export = _scrobble(user_id), _export(user_id)
        await _seed_resolved(uow, user_id, [scrobble, export], track.id)

        stats = await PlayProjectionService().project_range(
            uow, user_id=user_id, start=_WINDOW[0], end=_WINDOW[1]
        )

        assert stats["groups_created"] == 1
        plays, sources = await _canonical_state(db_session, user_id)
        assert len(plays) == 1
        play = plays[0]
        # Survivorship: export identity, lastfm true start, export ms_played.
        assert play.service == "spotify"
        assert play.played_at == scrobble.played_at
        assert play.ms_played == _MS
        assert play.source_services == ["spotify", "lastfm"]
        assert play.context is not None
        assert play.context["merged_from_lastfm"]["loved"] is True
        assert {s.connector_play_id for s in sources} == {scrobble.id, export.id}
        assert all(s.track_play_id == play.id for s in sources)

    async def test_reprojection_is_a_mechanical_noop(self, db_session):
        user_id = f"TEST_proj_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Striptease",
                artist="Carwash",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )
        await _seed_resolved(
            uow, user_id, [_scrobble(user_id), _export(user_id)], track.id
        )

        service = PlayProjectionService()
        _ = await service.project_range(
            uow, user_id=user_id, start=_WINDOW[0], end=_WINDOW[1]
        )
        plays_before, sources_before = await _canonical_state(db_session, user_id)

        stats = await service.project_range(
            uow, user_id=user_id, start=_WINDOW[0], end=_WINDOW[1]
        )

        assert stats["groups_unchanged"] == 1
        assert stats["groups_created"] == 0
        assert stats["groups_updated"] == 0
        plays_after, sources_after = await _canonical_state(db_session, user_id)
        assert [p.id for p in plays_after] == [p.id for p in plays_before]
        assert {s.id for s in sources_after} == {s.id for s in sources_before}

    async def test_second_source_arriving_later_enriches_not_duplicates(
        self, db_session
    ):
        """The milestone's user story in miniature: Last.fm first, GDPR later
        (or vice versa) — the later source deepens the same play."""
        user_id = f"TEST_proj_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Striptease",
                artist="Carwash",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )
        service = PlayProjectionService()

        scrobble = _scrobble(user_id)
        await _seed_resolved(uow, user_id, [scrobble], track.id)
        _ = await service.project_range(
            uow, user_id=user_id, start=_WINDOW[0], end=_WINDOW[1]
        )
        plays, _sources = await _canonical_state(db_session, user_id)
        assert len(plays) == 1
        assert plays[0].service == "lastfm"
        assert plays[0].ms_played is None
        original_play_id = plays[0].id

        export = _export(user_id)
        await _seed_resolved(uow, user_id, [export], track.id)
        stats = await service.project_range(
            uow, user_id=user_id, start=_WINDOW[0], end=_WINDOW[1]
        )

        assert stats["groups_updated"] == 1
        plays, sources = await _canonical_state(db_session, user_id)
        assert len(plays) == 1
        play = plays[0]
        # Same row enriched in place: id survives, export fields win.
        assert play.id == original_play_id
        assert play.service == "spotify"
        assert play.ms_played == _MS
        assert {s.connector_play_id for s in sources} == {scrobble.id, export.id}

    async def test_order_dependent_doubles_merge_keeping_lowest_id(self, db_session):
        """The F1 defect state (two rows for one listen) converges: lowest id
        survives, membership repoints, the other row is deleted."""
        user_id = f"TEST_proj_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        plays_repo = uow.get_plays_repository()
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Striptease",
                artist="Carwash",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )
        scrobble, export = _scrobble(user_id), _export(user_id)
        await _seed_resolved(uow, user_id, [scrobble, export], track.id)

        # Seed the defective pre-projection state: one canonical row per
        # source, each backed by its own observation.
        double_a = TrackPlay(
            track_id=track.id,
            service="spotify",
            played_at=export.played_at,
            user_id=user_id,
            ms_played=_MS,
            import_source="spotify_export",
        )
        double_b = TrackPlay(
            track_id=track.id,
            service="lastfm",
            played_at=scrobble.played_at,
            user_id=user_id,
            ms_played=None,
            import_source="lastfm_api",
        )
        _ = await plays_repo.bulk_insert_plays([double_a, double_b])
        await plays_repo.bulk_upsert_play_sources([
            PlaySource(
                user_id=user_id,
                track_play_id=double_a.id,
                connector_play_id=export.id,
            ),
            PlaySource(
                user_id=user_id,
                track_play_id=double_b.id,
                connector_play_id=scrobble.id,
            ),
        ])
        await uow.commit()

        stats = await PlayProjectionService().project_range(
            uow, user_id=user_id, start=_WINDOW[0], end=_WINDOW[1]
        )

        assert stats["groups_merged"] == 1
        assert stats["orphaned_deleted"] == 1
        plays, sources = await _canonical_state(db_session, user_id)
        assert len(plays) == 1
        survivor = plays[0]
        assert survivor.id == min(double_a.id, double_b.id)
        assert {s.connector_play_id for s in sources} == {scrobble.id, export.id}
        assert all(s.track_play_id == survivor.id for s in sources)


class TestRebuildPlayHistory:
    async def test_rebuild_converges_doubles_and_deletes_unsourced(self, db_session):
        """Milestone acceptance shape: a defective pre-projection state (one
        double-counted listen + one unsourced stray) converges on rebuild;
        dry-run reports the same diff without writing; a second run is a no-op.
        """
        from src.application.use_cases.rebuild_play_history import (
            RebuildPlayHistoryCommand,
            RebuildPlayHistoryUseCase,
        )

        user_id = f"TEST_rebuild_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        plays_repo = uow.get_plays_repository()
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Striptease",
                artist="Carwash",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )
        scrobble, export = _scrobble(user_id), _export(user_id)
        await _seed_resolved(uow, user_id, [scrobble, export], track.id)

        double_a = TrackPlay(
            track_id=track.id,
            service="spotify",
            played_at=export.played_at,
            user_id=user_id,
            ms_played=_MS,
            import_source="spotify_export",
        )
        double_b = TrackPlay(
            track_id=track.id,
            service="lastfm",
            played_at=scrobble.played_at,
            user_id=user_id,
            ms_played=None,
            import_source="lastfm_api",
        )
        # A stray canonical play no observation backs (pre-projection debris).
        stray = TrackPlay(
            track_id=track.id,
            service="lastfm",
            played_at=_START + timedelta(days=30),
            user_id=user_id,
            ms_played=None,
            import_source="lastfm_api",
        )
        _ = await plays_repo.bulk_insert_plays([double_a, double_b, stray])
        await plays_repo.bulk_upsert_play_sources([
            PlaySource(
                user_id=user_id,
                track_play_id=double_a.id,
                connector_play_id=export.id,
            ),
            PlaySource(
                user_id=user_id,
                track_play_id=double_b.id,
                connector_play_id=scrobble.id,
            ),
        ])
        await uow.commit()

        use_case = RebuildPlayHistoryUseCase()

        preview = await use_case.execute(
            RebuildPlayHistoryCommand(user_id=user_id, dry_run=True), uow
        )
        assert preview.stats["groups_merged"] == 1
        assert preview.stats["unsourced_deleted"] == 1
        plays, _sources = await _canonical_state(db_session, user_id)
        assert len(plays) == 3  # dry run wrote nothing

        applied = await use_case.execute(
            RebuildPlayHistoryCommand(user_id=user_id), uow
        )
        assert applied.stats["groups_merged"] == 1
        assert applied.stats["orphaned_deleted"] == 1
        assert applied.stats["unsourced_deleted"] == 1
        plays, sources = await _canonical_state(db_session, user_id)
        assert len(plays) == 1
        assert plays[0].id == min(double_a.id, double_b.id)
        assert {s.connector_play_id for s in sources} == {scrobble.id, export.id}

        rerun = await use_case.execute(RebuildPlayHistoryCommand(user_id=user_id), uow)
        assert rerun.stats["groups_unchanged"] == 1
        assert rerun.stats["groups_merged"] == 0
        assert rerun.stats["unsourced_deleted"] == 0
