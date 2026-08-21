"""Integration tests for the play-history endpoints (v0.10.4)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import httpx2

from src.application.runner import execute_use_case
from src.domain.entities.operations import TrackPlay
from src.domain.entities.track import Artist, Track

_BASE = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def _seed_track_with_plays(offsets_minutes: list[int]) -> UUID:
    async def _save(uow):
        async with uow:
            track = await uow.get_track_repository().save_track(
                Track(id=None, title="Played", artists=[Artist(name="Artist")])
            )
            plays = [
                TrackPlay(
                    track_id=track.id,
                    user_id="default",
                    played_at=_BASE + timedelta(minutes=offset),
                    service="spotify",
                )
                for offset in offsets_minutes
            ]
            _ = await uow.get_plays_repository().bulk_insert_plays(plays)
            await uow.commit()
            return track.id

    return await execute_use_case(_save)


class TestListPlaysEndpoint:
    async def test_empty_history(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/v1/plays")

        assert response.status_code == 200
        body = response.json()
        assert body == {"data": [], "limit": 50, "next_cursor": None}

    async def test_rows_carry_track_metadata(self, client: httpx2.AsyncClient) -> None:
        track_id = await _seed_track_with_plays([0, 10])

        response = await client.get("/api/v1/plays")

        body = response.json()
        assert len(body["data"]) == 2
        first = body["data"][0]
        assert first["track_id"] == str(track_id)
        assert first["title"] == "Played"
        assert first["artists"] == "Artist"
        assert first["service"] == "spotify"

    async def test_cursor_round_trip(self, client: httpx2.AsyncClient) -> None:
        await _seed_track_with_plays([0, 10, 20])

        page1 = (await client.get("/api/v1/plays?limit=2")).json()
        assert page1["next_cursor"] is not None

        page2 = (
            await client.get(f"/api/v1/plays?limit=2&cursor={page1['next_cursor']}")
        ).json()
        assert len(page2["data"]) == 1
        assert page2["next_cursor"] is None
        ids = {p["id"] for p in page1["data"]} | {p["id"] for p in page2["data"]}
        assert len(ids) == 3

    async def test_track_filter(self, client: httpx2.AsyncClient) -> None:
        track_id = await _seed_track_with_plays([0])
        _ = await _seed_track_with_plays([10])

        response = await client.get(f"/api/v1/plays?track_id={track_id}")

        body = response.json()
        assert [p["track_id"] for p in body["data"]] == [str(track_id)]

    async def test_reading_plays_triggers_refresh(
        self, client: httpx2.AsyncClient
    ) -> None:
        with patch(
            "src.application.services.play_freshness.spawn_ensure_fresh_plays"
        ) as spawn:
            response = await client.get("/api/v1/plays")

        assert response.status_code == 200
        spawn.assert_called_once()
        assert spawn.call_args.kwargs.get("trigger_detail") == "web"


class TestPlaysHistogramEndpoint:
    async def test_bins_and_bucket(self, client: httpx2.AsyncClient) -> None:
        await _seed_track_with_plays([0, 30, 24 * 60])

        response = await client.get(
            "/api/v1/plays/histogram?since=2026-06-01T00:00:00Z&until=2026-06-05T00:00:00Z"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["bucket"] == "day"
        assert [(b["bucket_start"][:10], b["count"]) for b in body["bins"]] == [
            ("2026-06-01", 2),
            ("2026-06-02", 1),
        ]

    async def test_unknown_timezone_is_rejected_not_a_500(
        self, client: httpx2.AsyncClient
    ) -> None:
        """``tz`` reaches SQL as ``timezone(:tz, played_at)``. A zone Postgres
        does not recognize raises ``invalid_parameter_value`` there, which
        surfaces as an unhandled 500 — a stale browser zone should be a 4xx."""
        response = await client.get("/api/v1/plays/histogram?tz=Not/AZone")

        assert response.status_code == 422

    async def test_known_timezone_is_accepted(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/v1/plays/histogram?tz=Europe/London")

        assert response.status_code == 200

    async def test_naive_since_does_not_error(self, client: httpx2.AsyncClient) -> None:
        """A ``datetime`` query param without an offset is valid input; the
        use case compares it against ``datetime.now(UTC)``."""
        response = await client.get("/api/v1/plays/histogram?since=2026-06-01T00:00:00")

        assert response.status_code == 200
