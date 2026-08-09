"""Spotify-import tenancy against a real database (v0.10.2.9).

Pins the fix for the mistenancy incident: canonicals created by the Spotify
inward resolver must land under the importing tenant, never under
``"default"`` — and, because they do, a re-import must resolve entirely
through the mapping-lookup fast path: no second API fetch, no new rows, and
no ``save_tracks`` deferred-to-per-item warning (the slow shape the
mistenanted rows used to force, ~8 round trips per colliding row).
"""

from uuid import uuid4

from sqlalchemy import func, select

from src.infrastructure.connectors.spotify.client import SpotifyTracksFetch
from src.infrastructure.connectors.spotify.inward_resolver import (
    SpotifyInwardResolver,
)
from src.infrastructure.connectors.spotify.models import SpotifyTrack
from src.infrastructure.persistence.database.db_models import DBTrack, DBTrackMapping
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures.factories import make_spotify_track


class _StubSpotifyConnector:
    """Canned ``GET /tracks?ids=`` answers, counting fetches.

    The count is the fast-path pin: a re-import that reaches the API at all
    has missed the mapping lookup.
    """

    connector_name = "spotify"

    def __init__(self, tracks: dict[str, SpotifyTrack]):
        self._tracks = tracks
        self.fetch_calls = 0

    async def get_tracks_by_ids(self, ids: list[str]) -> SpotifyTracksFetch:
        self.fetch_calls += 1
        return SpotifyTracksFetch(
            tracks={sid: self._tracks[sid] for sid in ids if sid in self._tracks}
        )


def _canned_chunk() -> dict[str, SpotifyTrack]:
    """Three unique canned tracks — one carrying a raw hyphenated ISRC."""
    suffix = uuid4().hex[:10]
    return {
        f"TEST_sp_{suffix}_{n}": make_spotify_track(
            f"TEST_sp_{suffix}_{n}",
            f"TEST Song {suffix} {n}",
            artist_name=f"TEST Artist {suffix} {n}",
            **(
                {"external_ids": {"isrc": f"te-st{suffix[:1]}-{suffix[1:3]}-{n:05d}"}}
                if n == 0
                else {}
            ),
        )
        for n in range(3)
    }


def _resolver(
    tracks: dict[str, SpotifyTrack],
) -> tuple[SpotifyInwardResolver, _StubSpotifyConnector]:
    stub = _StubSpotifyConnector(tracks)
    return SpotifyInwardResolver(spotify_connector=stub), stub


async def _track_and_mapping_counts(db_session, user_id: str) -> tuple[int, int]:
    tracks = (
        await db_session.execute(
            select(func.count()).select_from(DBTrack).where(DBTrack.user_id == user_id)
        )
    ).scalar_one()
    mappings = (
        await db_session.execute(
            select(func.count())
            .select_from(DBTrackMapping)
            .where(DBTrackMapping.user_id == user_id)
        )
    ).scalar_one()
    return tracks, mappings


class TestSpotifyImportTenancy:
    """Created canonicals belong to the tenant; re-imports are zero-delta."""

    async def test_created_tracks_are_owned_by_the_tenant_not_default(
        self, db_session, test_user_id
    ):
        canned = _canned_chunk()
        resolver, stub = _resolver(canned)
        uow = get_unit_of_work(db_session)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            list(canned), uow, user_id=test_user_id
        )

        assert metrics.created == 3
        assert set(result) == set(canned)
        owners = (
            (
                await db_session.execute(
                    select(DBTrack.user_id).where(DBTrack.spotify_id.in_(list(canned)))
                )
            )
            .scalars()
            .all()
        )
        assert owners == [test_user_id] * 3
        mistenanted = (
            await db_session.execute(
                select(func.count())
                .select_from(DBTrack)
                .where(
                    DBTrack.user_id == "default",
                    DBTrack.spotify_id.in_(list(canned)),
                )
            )
        ).scalar_one()
        assert mistenanted == 0
        assert stub.fetch_calls == 1

    async def test_second_chunk_with_same_ids_takes_the_mapping_fast_path(
        self, db_session, test_user_id
    ):
        canned = _canned_chunk()
        uow = get_unit_of_work(db_session)
        first_resolver, _ = _resolver(canned)
        _ = await first_resolver.resolve_to_canonical_tracks(
            list(canned), uow, user_id=test_user_id
        )

        # A later chunk of the same import (or a re-import) carries the same
        # ids — fresh resolver, fresh stub, so the fetch count is its own.
        second_resolver, second_stub = _resolver(canned)
        result, metrics = await second_resolver.resolve_to_canonical_tracks(
            list(canned), uow, user_id=test_user_id
        )

        assert metrics.existing == 3
        assert metrics.created == 0
        assert second_stub.fetch_calls == 0
        assert set(result) == set(canned)

    async def test_two_full_passes_are_zero_delta_and_never_defer(
        self, db_session, test_user_id, capsys
    ):
        canned = _canned_chunk()
        uow = get_unit_of_work(db_session)

        first_resolver, _ = _resolver(canned)
        _ = await first_resolver.resolve_to_canonical_tracks(
            list(canned), uow, user_id=test_user_id
        )
        after_first = await _track_and_mapping_counts(db_session, test_user_id)

        second_resolver, _ = _resolver(canned)
        _ = await second_resolver.resolve_to_canonical_tracks(
            list(canned), uow, user_id=test_user_id
        )
        after_second = await _track_and_mapping_counts(db_session, test_user_id)

        assert after_second == after_first
        # The deferred-to-per-item warning is the mistenancy symptom: rows
        # colliding with canonicals the tenant-scoped lookups cannot see.
        # capsys, not caplog — structlog renders straight to stdout here.
        assert "save_tracks deferred" not in capsys.readouterr().out
