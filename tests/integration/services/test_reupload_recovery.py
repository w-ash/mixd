"""Re-uploading a partially-imported export heals its stranded plays.

The incident: a 54-minute import was killed mid-run. Per-chunk commits had made
the connector_tracks, tracks and mappings durable, but every resolution was
accumulated in memory for one end-of-run write-back, so all 15,317 were
discarded — the plays stayed in the ledger with ``resolved_track_id IS NULL``,
outside the canonical projection and invisible to every listening stat.

Per-chunk write-back bounds that loss to a single chunk from here on. This pins
the recovery story for rows already stranded in production: re-upload the same
file. It works because of two independent properties, and neither is obviously
true from its call site:

1. Phase 1 hands Phase 2 every play it *parsed*, not just the rows the ledger's
   ``ON CONFLICT DO NOTHING`` accepted — so a wholly-duplicate re-upload still
   presents all its rows for resolution instead of short-circuiting on "nothing
   new".
2. ``bulk_update_resolution`` matches on the ledger natural key, not entity id
   — so the stamp lands on the row stored by the *first* upload, whose id the
   re-parsed entity does not carry.

Drop either one and stranded rows can never be healed by a re-upload.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from src.application.services.play_import_orchestrator import PlayImportOrchestrator
from src.application.use_cases._shared.batch_commit import commit_batch
from src.domain.entities import ConnectorTrackPlay, TrackPlay
from src.domain.entities.progress import NullProgressEmitter
from src.domain.repositories.play import PlayResolutionOutcome, SpotifyImportParams
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify.play_importer import SpotifyPlayImporter
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBTrackPlay,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track

_TRACK_URI = "spotify:track:2374M0fQpWi3dLnB54qaLX"
_MS = 214_000

# Two listens of the same track a day apart: enough for the projection to build
# two distinct canonical plays, so a re-upload that silently re-resolved only
# one of them would show up as a count.
_EXPORT_TIMESTAMPS = ("2024-04-02T19:21:07Z", "2024-04-03T19:21:07Z")


def _write_export(tmp_path: Path) -> Path:
    """A minimal but real Spotify GDPR export file."""
    file_path = tmp_path / "Streaming_History_Audio_2024.json"
    _ = file_path.write_text(
        json.dumps([
            {
                "ts": ts,
                "spotify_track_uri": _TRACK_URI,
                "master_metadata_track_name": "Achilles Last Stand",
                "master_metadata_album_artist_name": "Led Zeppelin",
                "master_metadata_album_album_name": "Presence",
                "ms_played": _MS,
                "platform": "ios",
                "conn_country": "GB",
                "reason_start": "trackdone",
                "reason_end": "trackdone",
                "shuffle": False,
                "skipped": False,
                "offline": False,
                "incognito_mode": False,
            }
            for ts in _EXPORT_TIMESTAMPS
        ]),
        encoding="utf-8",
    )
    return file_path


class _FixedTrackResolver:
    """Resolves every play to one canonical track, no external API.

    Stands in for the real per-service resolver: this test is about the ledger
    seam between the two phases, not about matching.
    """

    def __init__(self, track_id: UUID) -> None:
        self.track_id = track_id
        self.resolved_plays: list[ConnectorTrackPlay] = []

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_callback: object = None,
    ) -> PlayResolutionOutcome:
        _ = uow, user_id, progress_callback
        self.resolved_plays.extend(connector_plays)
        return PlayResolutionOutcome(
            track_plays=[
                TrackPlay(
                    track_id=self.track_id,
                    service=play.connector_name,
                    played_at=play.played_at,
                    ms_played=play.ms_played,
                    import_source=play.import_source,
                )
                for play in connector_plays
            ],
            metrics={"error_count": 0},
            resolutions=tuple((play, self.track_id) for play in connector_plays),
        )


async def _ledger_state(db_session, user_id: str) -> tuple[int, int]:
    """(ledger rows, of which resolved) for one user."""
    total, resolved = (
        await db_session.execute(
            sa
            .select(
                sa.func.count(),
                sa.func.count(DBConnectorPlay.resolved_track_id),
            )
            .select_from(DBConnectorPlay)
            .where(DBConnectorPlay.user_id == user_id)
        )
    ).one()
    return total, resolved


async def _canonical_play_count(db_session, user_id: str) -> int:
    return (
        await db_session.execute(
            sa
            .select(sa.func.count())
            .select_from(DBTrackPlay)
            .where(DBTrackPlay.user_id == user_id)
        )
    ).scalar_one()


class TestReuploadRecovery:
    """Re-upload is the user-facing recovery for a killed import."""

    @pytest.fixture
    async def stranded(self, db_session, tmp_path):
        """A user whose export is ingested but wholly unresolved.

        Phase 1 plus its commit, then nothing — exactly the durable state a
        process killed before any write-back leaves behind.
        """
        user_id = f"TEST_reupload_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Achilles Last Stand",
                artist="Led Zeppelin",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )
        file_path = _write_export(tmp_path)

        _ = await SpotifyPlayImporter().import_plays(
            uow,
            SpotifyImportParams(file_path=file_path),
            user_id=user_id,
            progress_emitter=NullProgressEmitter(),
        )
        await commit_batch(uow)

        return user_id, uow, track.id, file_path

    async def test_stranded_state_is_what_the_incident_left(self, db_session, stranded):
        user_id, _uow, _track_id, _file_path = stranded

        assert await _ledger_state(db_session, user_id) == (2, 0)
        assert await _canonical_play_count(db_session, user_id) == 0

    async def test_reupload_heals_stranded_unresolved_plays(self, db_session, stranded):
        user_id, uow, track_id, file_path = stranded
        resolver = _FixedTrackResolver(track_id)

        async def resolver_factory(_service: str):
            return resolver

        result = await PlayImportOrchestrator(
            resolver_factory=resolver_factory
        ).import_plays_two_phase(
            SpotifyPlayImporter(),
            uow,
            user_id=user_id,
            params=SpotifyImportParams(file_path=file_path),
            progress_emitter=NullProgressEmitter(),
        )

        # Every row the ledger already held was presented for resolution — a
        # Phase 1 that forwarded only newly-inserted rows would hand over none.
        assert len(resolver.resolved_plays) == 2
        # ...and none of them was inserted a second time.
        assert result.summary_metrics.get("duplicates") == 2
        assert await _ledger_state(db_session, user_id) == (2, 2)
        # The projection ran, so the plays are back in canonical history.
        assert await _canonical_play_count(db_session, user_id) == 2
