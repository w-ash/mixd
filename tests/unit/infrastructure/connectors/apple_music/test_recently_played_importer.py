"""Unit tests for the Apple Music recently-played importer (v0.11.x).

Apple's feed reports WHAT played but never WHEN, so this importer's whole
job is honest timestamping: a fingerprint of the previous window turns the
next poll into a prefix-diff (only the items that appeared since last time
are new), and every new item is stamped with the midpoint of the interval it
must have happened in. The first poll seeds the fingerprint and emits
nothing — inventing timestamps for historical items would be dishonest.

Timestamp-sensitive cases drive the pipeline steps directly with a fixed
``import_timestamp`` (the run's poll time); the end-to-end flow goes through
``import_plays`` like the scheduler does.
"""

from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.operations import SyncCheckpoint
from src.domain.repositories.play import (
    AppleRecentImportParams,
    SpotifyRecentImportParams,
)
from src.infrastructure.connectors.apple_music.models import (
    AppleMusicRecentlyPlayedResponse,
    AppleMusicSong,
    AppleMusicSongAttributes,
)
from src.infrastructure.connectors.apple_music.recently_played_importer import (
    MAX_TURNOVER_PAGES,
    AppleMusicRecentlyPlayedImporter,
)
from tests.fixtures.mocks import make_mock_uow

_PREV_POLL = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
_NOW = datetime(2026, 8, 20, 13, 0, tzinfo=UTC)
_MIDPOINT = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)

# Batch identity the pipeline threads into the fetch and the conversion.
# ``import_timestamp`` doubles as the poll time — it is the run's "now".
_RUN = {"batch_id": "batch-1", "import_timestamp": _NOW}


def _song(song_id: str, *, name: str = "Striptease") -> AppleMusicSong:
    return AppleMusicSong(
        id=song_id,
        attributes=AppleMusicSongAttributes(
            name=name,
            artist_name="Carwash",
            album_name="Shimmer",
            duration_in_millis=201_000,
            isrc="GBAYE1234567",
        ),
    )


def _window(*song_ids: str) -> list[AppleMusicSong]:
    return [_song(song_id) for song_id in song_ids]


def _client(*pages: list[AppleMusicSong], has_next: bool = True) -> MagicMock:
    """A client double returning one response per ``get_recently_played`` call.

    Every page carries a ``next`` cursor except, when ``has_next`` is False,
    the final one — mirroring an exhausted feed.
    """
    responses = [
        AppleMusicRecentlyPlayedResponse(
            data=page,
            next=None
            if (not has_next and i == len(pages) - 1)
            else "/v1/me/recent/played/tracks?offset=next",
        )
        for i, page in enumerate(pages)
    ]
    client = MagicMock()
    client.get_recently_played = AsyncMock(side_effect=responses)
    client.aclose = AsyncMock()
    return client


def _cursor(fingerprint: list[str], polled_at: datetime = _PREV_POLL) -> str:
    return json.dumps({
        "fingerprint": fingerprint,
        "polled_at": polled_at.isoformat(),
    })


def _uow_with_checkpoint(checkpoint: SyncCheckpoint | None):
    """A mock UoW whose checkpoint repo returns ``checkpoint`` on read."""
    uow = make_mock_uow()
    uow.get_connector_play_repository().bulk_insert_connector_plays = AsyncMock(
        side_effect=lambda plays: (len(list(plays)), 0)
    )
    repo = uow.get_checkpoint_repository()
    repo.get_sync_checkpoint = AsyncMock(return_value=checkpoint)
    repo.get_or_create_sync_checkpoint = AsyncMock(
        return_value=checkpoint
        or SyncCheckpoint(user_id="user-1", service="apple", entity_type="plays")
    )
    repo.save_sync_checkpoint = AsyncMock(side_effect=lambda cp: cp)
    return uow, repo


def _checkpoint(fingerprint: list[str], polled_at: datetime = _PREV_POLL):
    return SyncCheckpoint(
        user_id="user-1",
        service="apple",
        entity_type="plays",
        cursor=_cursor(fingerprint, polled_at),
    )


async def _poll(
    importer: AppleMusicRecentlyPlayedImporter,
    uow,
    params: AppleRecentImportParams | None = None,
    *,
    user_id: str = "user-1",
):
    """One poll as the pipeline runs it, at the fixed poll time ``_NOW``.

    Mirrors ``BasePlayImporter._run_import_pipeline``: an empty fetch skips
    ``_process_data`` but still checkpoints.
    """
    params = params or AppleRecentImportParams()
    raw = await importer._fetch_data(params, uow=uow, user_id=user_id, **_RUN)
    plays = await importer._process_data(raw, user_id=user_id, **_RUN) if raw else []
    await importer._handle_checkpoints(raw, params, uow, user_id=user_id)
    return plays


def _saved_state(repo) -> dict:
    return json.loads(repo.save_sync_checkpoint.await_args.args[0].cursor)


class TestFirstPoll:
    """Seeding: no fingerprint yet means no honest timestamp for anything."""

    async def test_first_poll_emits_zero_observations(self):
        importer = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("s1", "s2", "s3"))
        )
        uow, _ = _uow_with_checkpoint(None)

        plays = await _poll(importer, uow)

        assert plays == []

    async def test_first_poll_seeds_fingerprint_and_poll_time(self):
        importer = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("s1", "s2", "s3"))
        )
        uow, repo = _uow_with_checkpoint(None)

        _ = await _poll(importer, uow)

        state = _saved_state(repo)
        assert state["fingerprint"] == ["s1", "s2", "s3"]
        assert datetime.fromisoformat(state["polled_at"]) == _NOW

    async def test_unparseable_cursor_reseeds_instead_of_guessing(self):
        """A corrupt fingerprint must not fabricate a diff — start over."""
        importer = AppleMusicRecentlyPlayedImporter(client=_client(_window("s1", "s2")))
        uow, repo = _uow_with_checkpoint(
            SyncCheckpoint(
                user_id="user-1",
                service="apple",
                entity_type="plays",
                cursor="1753012800000",  # a Spotify-style ms-epoch cursor
            )
        )

        plays = await _poll(importer, uow)

        assert plays == []
        assert _saved_state(repo)["fingerprint"] == ["s1", "s2"]

    async def test_force_reseeds_like_a_first_poll(self):
        """The recovery lever for a fingerprint that no longer matches reality."""
        importer = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("n1", "s1", "s2"))
        )
        uow, repo = _uow_with_checkpoint(_checkpoint(["s1", "s2"]))

        plays = await _poll(importer, uow, AppleRecentImportParams(force=True))

        assert plays == []
        assert _saved_state(repo)["fingerprint"] == ["n1", "s1", "s2"]

    async def test_wrong_params_type_raises(self):
        importer = AppleMusicRecentlyPlayedImporter(client=_client([]))
        uow, _ = _uow_with_checkpoint(None)

        with pytest.raises(TypeError, match="requires AppleRecentImportParams"):
            await importer.import_plays(
                uow, SpotifyRecentImportParams(), user_id="user-1"
            )


class TestPrefixDiff:
    """New plays are the head items in front of the previous window."""

    async def test_three_new_head_items_become_three_observations(self):
        importer = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("n1", "n2", "n3", "s1", "s2", "s3"))
        )
        uow, _ = _uow_with_checkpoint(_checkpoint(["s1", "s2", "s3"]))

        plays = await _poll(importer, uow)

        assert [p.service_metadata["song_id"] for p in plays] == ["n1", "n2", "n3"]

    async def test_new_items_are_stamped_with_the_poll_interval_midpoint(self):
        """All items in one poll share the midpoint — the feed gives no order
        in time, only an interval the plays must fall in."""
        importer = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("n1", "n2", "s1", "s2"))
        )
        uow, _ = _uow_with_checkpoint(_checkpoint(["s1", "s2"]))

        plays = await _poll(importer, uow)

        assert {p.played_at for p in plays} == {_MIDPOINT}

    async def test_field_mapping_onto_the_apple_api_channel(self):
        importer = AppleMusicRecentlyPlayedImporter(client=_client(_window("n1", "s1")))
        uow, _ = _uow_with_checkpoint(_checkpoint(["s1"]))

        plays = await _poll(importer, uow)

        play = plays[0]
        assert play.service == "apple"
        assert play.import_source == "apple_api"
        assert play.track_name == "Striptease"
        assert play.artist_name == "Carwash"
        assert play.album_name == "Shimmer"
        assert play.import_batch_id == "batch-1"
        # Tenancy at construction — the pipeline verifies, never re-stamps.
        assert play.user_id == "user-1"
        # The catalog song id IS the ledger identifier — what the resolver
        # resolves and the projection buckets on.
        assert play.connector_track_identifier == "n1"
        assert play.service_metadata["song_id"] == "n1"
        # No listened duration exists; a synthetic one would win survivorship
        # merges and corrupt listening-time stats.
        assert play.ms_played is None
        assert play.service_metadata["duration_ms"] == 201_000

    async def test_unchanged_window_emits_nothing_but_advances_poll_time(self):
        """Zero new items still narrows the next poll's midpoint interval."""
        importer = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("s1", "s2", "s3"))
        )
        uow, repo = _uow_with_checkpoint(_checkpoint(["s1", "s2", "s3"]))

        plays = await _poll(importer, uow)

        assert plays == []
        state = _saved_state(repo)
        assert datetime.fromisoformat(state["polled_at"]) == _NOW
        assert state["fingerprint"] == ["s1", "s2", "s3"]

    async def test_repeat_of_a_fingerprinted_song_is_a_new_observation(self):
        """A song id from a previous poll appearing at the head again is a new
        listen. Exact repeats inside one poll are left to the ledger's dedup
        constraint (same identifier + midpoint + null ms collide there)."""
        importer = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("s2", "s1", "s2", "s3"))
        )
        uow, _ = _uow_with_checkpoint(_checkpoint(["s1", "s2", "s3"]))

        plays = await _poll(importer, uow)

        assert [p.service_metadata["song_id"] for p in plays] == ["s2"]

    async def test_checkpoint_round_trip_through_the_full_pipeline(self):
        """The fingerprint one run writes is the diff base the next one reads,
        end to end through ``import_plays`` as the scheduler runs it."""
        importer = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("s1", "s2", "s3"))
        )
        uow, repo = _uow_with_checkpoint(None)

        result, plays = await importer.import_plays(
            uow, AppleRecentImportParams(), user_id="user-1"
        )
        assert not result.is_failure
        assert plays == []
        saved = repo.save_sync_checkpoint.await_args.args[0]

        # Second run, one new play at the head of the window.
        repo.get_sync_checkpoint = AsyncMock(return_value=saved)
        importer2 = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("n1", "s1", "s2", "s3"))
        )
        result2, plays2 = await importer2.import_plays(
            uow, AppleRecentImportParams(), user_id="user-1"
        )

        assert not result2.is_failure
        assert [p.service_metadata["song_id"] for p in plays2] == ["n1"]
        prev = json.loads(saved.cursor)
        prev_polled = datetime.fromisoformat(prev["polled_at"])
        # The midpoint sits strictly inside the two runs' poll interval.
        assert prev_polled < plays2[0].played_at
        assert plays2[0].played_at < datetime.now(UTC)


class TestFullTurnover:
    """No overlap found: only the first page ingests (bounded honest floor)."""

    async def test_no_boundary_ingests_first_page_only(self):
        """Items beyond page 1 are near-certainly old window content that the
        lost boundary can no longer prove old — they are not ingested."""
        pages = [
            _window("a1", "a2"),
            _window("b1", "b2"),
            _window("c1", "c2"),
        ]
        importer = AppleMusicRecentlyPlayedImporter(client=_client(*pages))
        uow, _ = _uow_with_checkpoint(_checkpoint(["z1", "z2", "z3"]))

        plays = await _poll(importer, uow)

        assert [p.service_metadata["song_id"] for p in plays] == ["a1", "a2"]

    async def test_turnover_paging_stops_at_the_cap(self):
        pages = [_window(f"p{i}a", f"p{i}b") for i in range(MAX_TURNOVER_PAGES + 2)]
        client = _client(*pages)
        importer = AppleMusicRecentlyPlayedImporter(client=client)
        uow, _ = _uow_with_checkpoint(_checkpoint(["z1", "z2"]))

        _ = await _poll(importer, uow)

        assert client.get_recently_played.await_count == MAX_TURNOVER_PAGES

    async def test_overlap_on_a_later_page_bounds_the_diff(self):
        importer = AppleMusicRecentlyPlayedImporter(
            client=_client(
                _window("n1", "n2"),
                _window("n3", "s1", "s2", "s3"),
            )
        )
        uow, _ = _uow_with_checkpoint(_checkpoint(["s1", "s2", "s3"]))

        plays = await _poll(importer, uow)

        assert [p.service_metadata["song_id"] for p in plays] == ["n1", "n2", "n3"]

    async def test_exhausted_feed_stops_paging_without_the_cap(self):
        """A ``next``-less page is the end of what Apple retains."""
        client = _client(_window("a1", "a2"), has_next=False)
        importer = AppleMusicRecentlyPlayedImporter(client=client)
        uow, _ = _uow_with_checkpoint(_checkpoint(["z1", "z2"]))

        plays = await _poll(importer, uow)

        assert client.get_recently_played.await_count == 1
        assert [p.service_metadata["song_id"] for p in plays] == ["a1", "a2"]

    async def test_turnover_conflict_skips_reordered_fingerprinted_songs(self):
        """A fingerprinted id resurfacing on turnover is a reorder, not
        provably a new listen — re-ingesting it would mint a phantom
        permanent play at a fresh midpoint. It is dropped (a true re-listen
        may be missed — the honest floor); genuinely new page-1 songs still
        ingest."""
        pages = [
            _window("a1", "z2"),
            _window("b1", "b2"),
            _window("z1", "c1"),
        ]
        importer = AppleMusicRecentlyPlayedImporter(client=_client(*pages))
        uow, _ = _uow_with_checkpoint(_checkpoint(["z1", "z2", "z3"]))

        plays = await _poll(importer, uow)

        assert [p.service_metadata["song_id"] for p in plays] == ["a1"]

    async def test_exhausted_feed_turnover_also_conflict_skips_repeats(self):
        """The no-``next`` turnover arm applies the same conflict-skip."""
        client = _client(_window("a1", "z1", "a2"), has_next=False)
        importer = AppleMusicRecentlyPlayedImporter(client=client)
        uow, _ = _uow_with_checkpoint(_checkpoint(["z1", "z2", "z3"]))

        plays = await _poll(importer, uow)

        assert [p.service_metadata["song_id"] for p in plays] == ["a1", "a2"]


class TestEmptyFeedAnomaly:
    """A 200-with-no-items window must not erase an established fingerprint."""

    async def test_empty_window_preserves_established_fingerprint(self):
        """Overwriting the fingerprint with [] would make the NEXT poll read
        the entire (returned) window as new plays. Keep the previous
        fingerprint, advance the poll time, ingest nothing."""
        client = _client([], has_next=False)
        importer = AppleMusicRecentlyPlayedImporter(client=client)
        uow, repo = _uow_with_checkpoint(_checkpoint(["s1", "s2", "s3"]))

        plays = await _poll(importer, uow)

        assert plays == []
        state = _saved_state(repo)
        assert state["fingerprint"] == ["s1", "s2", "s3"]
        assert datetime.fromisoformat(state["polled_at"]) == _NOW

    async def test_next_poll_after_empty_anomaly_diffs_normally(self):
        """The preserved fingerprint keeps working as the next diff base."""
        client = _client([], has_next=False)
        importer = AppleMusicRecentlyPlayedImporter(client=client)
        uow, repo = _uow_with_checkpoint(_checkpoint(["s1", "s2", "s3"]))
        _ = await _poll(importer, uow)
        saved = repo.save_sync_checkpoint.await_args.args[0]

        repo.get_sync_checkpoint = AsyncMock(return_value=saved)
        importer2 = AppleMusicRecentlyPlayedImporter(
            client=_client(_window("n1", "s1", "s2", "s3"))
        )
        plays = await _poll(importer2, uow)

        assert [p.service_metadata["song_id"] for p in plays] == ["n1"]

    async def test_empty_window_with_empty_fingerprint_stays_a_normal_poll(self):
        """A genuinely empty feed for a new user is not anomalous."""
        client = _client([], has_next=False)
        importer = AppleMusicRecentlyPlayedImporter(client=client)
        uow, repo = _uow_with_checkpoint(_checkpoint([]))

        plays = await _poll(importer, uow)

        assert plays == []
        state = _saved_state(repo)
        assert state["fingerprint"] == []
        assert datetime.fromisoformat(state["polled_at"]) == _NOW


class TestTransportFailure:
    async def test_none_response_raises_rather_than_reading_as_empty(self):
        """A suppressed transport failure must not look like 'nothing new' —
        it would advance the poll time while having observed nothing."""
        client = MagicMock()
        client.get_recently_played = AsyncMock(return_value=None)
        client.aclose = AsyncMock()
        importer = AppleMusicRecentlyPlayedImporter(client=client)
        uow, _ = _uow_with_checkpoint(None)

        with pytest.raises(RuntimeError, match="no recently-played response"):
            await importer._fetch_data(
                AppleRecentImportParams(), uow=uow, user_id="user-1", **_RUN
            )


class TestCheckpointKeying:
    async def test_checkpoint_read_and_written_under_the_mixd_user_id(self):
        importer = AppleMusicRecentlyPlayedImporter(client=_client(_window("s1")))
        uow, repo = _uow_with_checkpoint(None)

        _ = await _poll(importer, uow, user_id="mixd-user-42")

        repo.get_sync_checkpoint.assert_awaited_once_with(
            user_id="mixd-user-42", service="apple", entity_type="plays"
        )
        repo.get_or_create_sync_checkpoint.assert_awaited_once_with(
            user_id="mixd-user-42", service="apple", entity_type="plays"
        )


class TestMidpointHonesty:
    async def test_stale_previous_poll_widens_but_never_inverts_the_midpoint(self):
        """A midpoint always sits strictly between the two poll times."""
        stale = _NOW - timedelta(days=3)
        importer = AppleMusicRecentlyPlayedImporter(client=_client(_window("n1", "s1")))
        uow, _ = _uow_with_checkpoint(_checkpoint(["s1"], polled_at=stale))

        plays = await _poll(importer, uow)

        assert plays[0].played_at == stale + (_NOW - stale) / 2
        assert stale < plays[0].played_at < _NOW
