"""Integration tests for import, checkpoint, and SSE operation endpoints.

Tests request validation, response shapes, checkpoint retrieval, the Spotify
GDPR import queue (v0.10.2.6), and Server-Sent Event streaming through the
real FastAPI app with an isolated test database.
"""

import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import time
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from src.config.constants import SSEConstants
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.interface.api.services import (
    import_queue as import_queue_mod,
    sse_operations as sse_operations_mod,
)
from src.interface.api.services.background import (
    _background_tasks,
    launch_background as real_launch_background,
)
from src.interface.api.services.import_queue import IMPORT_QUEUE_TMPDIR_PREFIX
from src.interface.api.services.progress import (
    SSE_SENTINEL,
    get_operation_registry,
)
from src.interface.api.services.sse_operations import release_operation_slot


@pytest.fixture(autouse=True)
def _reset_import_queue_state():
    """The queue registry and its slot tokens are module-global in-process
    state; with the conftest's launch stub a queue never drains itself, so
    every test must leave the registry (and any held slot) empty."""
    yield
    for queue in list(import_queue_mod._queues.values()):
        release_operation_slot(import_queue_mod._queue_slot_token(queue.queue_id))
        shutil.rmtree(queue.tmpdir, ignore_errors=True)
    import_queue_mod._queues.clear()


@pytest.fixture
async def _sse_subscriber_wired():
    """Subscribe the SSE subscriber to the broker for the duration of a test.

    ``ASGITransport`` never runs the app lifespan, so the wiring that app
    startup does is absent here — without it a stream only ever receives the
    events pushed to its queue directly, and every broker-routed event (the
    whole ``started``/``sub_*`` vocabulary) is silently missing.
    """
    from src.application.services.progress_broker import get_progress_broker
    from src.interface.api.services.progress import SSEProgressSubscriber

    broker = get_progress_broker()
    sub_id = await broker.subscribe(SSEProgressSubscriber(get_operation_registry()))
    try:
        yield
    finally:
        await broker.unsubscribe(sub_id)


@pytest.fixture
def _drain_import_queues_for_real(client, monkeypatch):
    """Opt this test out of the conftest ``launch_background`` stub so the
    queue actually drains, and zero the SSE grace period so each entry's run
    task settles inside the test instead of sleeping out its read window."""
    monkeypatch.setattr(sse_operations_mod, "launch_background", real_launch_background)
    monkeypatch.setattr(import_queue_mod, "launch_background", real_launch_background)
    monkeypatch.setattr(SSEConstants, "GRACE_PERIOD_SECONDS", 0)


def _multipart(
    files: list[tuple[str, bytes]],
) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, content, "application/json")) for name, content in files]


def _queue_tmpdirs() -> set[str]:
    return {
        str(p)
        for p in Path(tempfile.gettempdir()).glob(f"{IMPORT_QUEUE_TMPDIR_PREFIX}*")
    }


async def _poll_queue_until_drained(
    client: httpx2.AsyncClient, timeout_seconds: float = 30.0
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = await client.get("/api/v1/imports/spotify/history/queue")
        assert response.status_code == 200
        data = response.json()
        if all(e["status"] not in ("queued", "running") for e in data["entries"]):
            return data
        await asyncio.sleep(0.05)
    raise AssertionError("import queue did not drain within the timeout")


async def _settle_background_tasks(timeout_seconds: float = 10.0) -> None:
    """Wait for the drain + per-run tasks so nothing outlives the test loop."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _background_tasks:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("background tasks did not settle within the timeout")


def _connected_storage() -> AsyncMock:
    """A TokenStorage mock that reports a stored token for every service."""
    storage = AsyncMock()
    storage.load_token = AsyncMock(
        return_value=StoredToken(account_name="connected", session_key="sk")
    )
    return storage


def _parse_sse_events(raw: str) -> list[dict[str, str]]:
    """Parse raw SSE text into a list of event dicts with 'id', 'event', 'data' keys.

    Skips comment-only lines (keep-alive pings) and blank separators.
    """
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        if line.startswith(":"):
            # SSE comment (e.g. keep-alive ping) — skip
            continue
        if not line:
            # Blank line = event boundary
            if current:
                events.append(current)
                current = {}
            continue
        if ": " in line:
            field, _, value = line.partition(": ")
            current[field] = value
        elif line.endswith(":"):
            current[line[:-1]] = ""
    # Trailing event without final blank line
    if current:
        events.append(current)
    return events


class TestImportEndpoints:
    """Tests that import endpoints return operation_id responses."""

    @pytest.fixture(autouse=True)
    def _connected(self):
        # The import routes 409 when the connector has no stored token (6c). These
        # happy-path tests assert the trigger contract, so report a connected state.
        with patch(
            "src.interface.api.deps.get_token_storage",
            return_value=_connected_storage(),
        ):
            yield

    async def test_import_lastfm_history_returns_operation_id(
        self, client: httpx2.AsyncClient
    ):
        response = await client.post(
            "/api/v1/imports/lastfm/history",
            json={"mode": "recent"},
        )

        # May fail due to missing credentials, but the endpoint itself should
        # accept the request and return 200 with an operation_id
        assert response.status_code == 200
        data = response.json()
        assert "operation_id" in data
        assert isinstance(data["operation_id"], str)
        assert len(data["operation_id"]) > 0

    async def test_import_lastfm_history_default_mode(self, client: httpx2.AsyncClient):
        response = await client.post(
            "/api/v1/imports/lastfm/history",
            json={},
        )

        assert response.status_code == 200
        assert "operation_id" in response.json()

    async def test_import_lastfm_history_invalid_mode(self, client: httpx2.AsyncClient):
        response = await client.post(
            "/api/v1/imports/lastfm/history",
            json={"mode": "invalid_mode"},
        )

        assert response.status_code == 422  # Validation error

    async def test_import_spotify_likes_returns_operation_id(
        self, client: httpx2.AsyncClient
    ):
        response = await client.post(
            "/api/v1/imports/spotify/likes",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert "operation_id" in data

    async def test_import_spotify_likes_with_params(self, client: httpx2.AsyncClient):
        response = await client.post(
            "/api/v1/imports/spotify/likes",
            json={"limit": 10, "max_imports": 5},
        )

        assert response.status_code == 200
        assert "operation_id" in response.json()

    async def test_export_lastfm_likes_returns_operation_id(
        self, client: httpx2.AsyncClient
    ):
        response = await client.post(
            "/api/v1/imports/lastfm/likes",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert "operation_id" in data

    async def test_export_lastfm_likes_with_params(self, client: httpx2.AsyncClient):
        response = await client.post(
            "/api/v1/imports/lastfm/likes",
            json={"batch_size": 10, "max_exports": 5},
        )

        assert response.status_code == 200
        assert "operation_id" in response.json()


class TestLastfmUsernameResolution:
    """6b cross-tenant proof: a Last.fm import resolves the CONNECTED account, not env.

    Seeds a real ``oauth_tokens`` row (cleaned up by the ``client`` fixture's
    truncation) and asserts the importer resolves the stored ``account_name`` scoped
    to the mixd user — never ``LASTFM_USERNAME`` env, never another tenant's account.
    """

    async def test_resolves_connected_account_never_env_or_other_tenant(
        self, client: httpx2.AsyncClient, monkeypatch
    ):
        from src.config import settings
        from src.infrastructure.connectors.lastfm.play_importer import (
            LastfmPlayImporter,
        )
        from src.infrastructure.persistence.repositories.token_storage import (
            DatabaseTokenStorage,
        )

        # No env username — a web user must never read it.
        monkeypatch.setattr(settings.credentials, "lastfm_username", "")

        storage = DatabaseTokenStorage()
        await storage.save_token(
            "lastfm",
            "default",
            StoredToken(session_key="sk-a", token_type="session", account_name="alice"),
        )
        await storage.save_token(
            "lastfm",
            "other_user",
            StoredToken(
                session_key="sk-m", token_type="session", account_name="mallory"
            ),
        )

        importer = LastfmPlayImporter(token_storage=DatabaseTokenStorage())

        # Each mixd user resolves THEIR connected account — per-user scoping, no env.
        assert await importer._resolve_username(None, "default") == "alice"
        assert await importer._resolve_username(None, "other_user") == "mallory"

    async def test_no_token_and_no_env_raises_clean_error(
        self, client: httpx2.AsyncClient, monkeypatch
    ):
        from src.config import settings
        from src.domain.exceptions import LastfmAuthRequiredError
        from src.infrastructure.connectors.lastfm.play_importer import (
            LastfmPlayImporter,
        )
        from src.infrastructure.persistence.repositories.token_storage import (
            DatabaseTokenStorage,
        )

        monkeypatch.setattr(settings.credentials, "lastfm_username", "")
        importer = LastfmPlayImporter(token_storage=DatabaseTokenStorage())
        with pytest.raises(LastfmAuthRequiredError):
            await importer._resolve_username(None, "user-with-no-token")


class TestConnectorConnectPreflight:
    """6c: import routes 409 when the connector has no stored token."""

    @staticmethod
    def _disconnected():
        storage = AsyncMock()
        storage.load_token = AsyncMock(return_value=None)
        return patch("src.interface.api.deps.get_token_storage", return_value=storage)

    async def test_lastfm_history_409_when_not_connected(
        self, client: httpx2.AsyncClient
    ):
        with self._disconnected():
            response = await client.post(
                "/api/v1/imports/lastfm/history", json={"mode": "recent"}
            )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONNECTOR_NOT_CONNECTED"
        assert response.json()["error"]["details"]["connector"] == "lastfm"

    async def test_spotify_likes_409_when_not_connected(
        self, client: httpx2.AsyncClient
    ):
        with self._disconnected():
            response = await client.post("/api/v1/imports/spotify/likes", json={})
        assert response.status_code == 409
        assert response.json()["error"]["details"]["connector"] == "spotify"

    async def test_lastfm_likes_export_409_when_not_connected(
        self, client: httpx2.AsyncClient
    ):
        with self._disconnected():
            response = await client.post("/api/v1/imports/lastfm/likes", json={})
        assert response.status_code == 409

    async def test_spotify_recent_409_when_not_connected(
        self, client: httpx2.AsyncClient
    ):
        with self._disconnected():
            response = await client.post("/api/v1/imports/spotify/recent", json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONNECTOR_NOT_CONNECTED"

    async def test_spotify_recent_409_when_grant_lacks_the_scope(
        self, client: httpx2.AsyncClient
    ):
        """A pre-v0.10.1 grant passes the connected check but not this route.

        409 rather than the RFC 6750 403: the caller's own session is valid —
        what is short is the third-party grant mixd holds for them, which is a
        connected-account state problem with a reconnect remedy.
        """
        storage = AsyncMock()
        storage.load_token = AsyncMock(
            return_value=StoredToken(
                account_name="connected",
                session_key="sk",
                scope="playlist-read-private user-library-read",
            )
        )
        with patch("src.interface.api.deps.get_token_storage", return_value=storage):
            response = await client.post("/api/v1/imports/spotify/recent", json={})

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "CONNECTOR_SCOPE_MISSING"
        assert error["details"]["connector"] == "spotify"
        assert error["details"]["missing_scopes"] == ["user-read-recently-played"]

    async def test_spotify_recent_accepted_when_scope_granted(
        self, client: httpx2.AsyncClient
    ):
        storage = AsyncMock()
        storage.load_token = AsyncMock(
            return_value=StoredToken(
                account_name="connected",
                session_key="sk",
                scope="user-library-read user-read-recently-played",
            )
        )
        with patch("src.interface.api.deps.get_token_storage", return_value=storage):
            response = await client.post(
                "/api/v1/imports/spotify/recent", json={"limit": 50}
            )

        assert response.status_code == 200
        assert response.json()["operation_id"]

    async def test_spotify_recent_accepts_force_like_the_chat_surface(
        self, client: httpx2.AsyncClient
    ):
        """`force` re-reads the whole window; REST and chat must offer the same lever."""
        storage = AsyncMock()
        storage.load_token = AsyncMock(
            return_value=StoredToken(
                account_name="connected",
                session_key="sk",
                scope="user-read-recently-played",
            )
        )
        with patch("src.interface.api.deps.get_token_storage", return_value=storage):
            response = await client.post(
                "/api/v1/imports/spotify/recent", json={"force": True}
            )

        assert response.status_code == 200

    async def test_spotify_history_upload_is_not_gated(
        self, client: httpx2.AsyncClient
    ):
        # The GDPR file upload needs no live token — it must NOT be gated.
        with self._disconnected():
            response = await client.post(
                "/api/v1/imports/spotify/history",
                files=_multipart([("history.json", json.dumps([]).encode())]),
            )
        assert response.status_code == 200


def _patched_limits(
    *,
    max_upload_bytes: int = 100 * 1024 * 1024,
    max_queued_upload_bytes: int = 500 * 1024 * 1024,
    max_queue_entries: int = 25,
):
    """Patch the service module's BusinessLimits with real ints — a bare
    MagicMock attribute would make every ``>`` comparison raise. (The upload
    guards moved from the route into ``import_queue.receive_export_upload``.)"""
    patcher = patch("src.interface.api.services.import_queue.BusinessLimits")
    mock_limits = patcher.start()
    mock_limits.MAX_UPLOAD_BYTES = max_upload_bytes
    mock_limits.MAX_QUEUED_UPLOAD_BYTES = max_queued_upload_bytes
    mock_limits.MAX_QUEUE_ENTRIES = max_queue_entries
    return patcher


class TestSpotifyHistoryUploadCaps:
    """Server-side upload caps reject oversize requests with nothing on disk."""

    async def test_oversized_file_413s_and_nothing_lands(
        self, client: httpx2.AsyncClient, tmp_path, monkeypatch
    ) -> None:
        # Pin this test's queue dirs to a private tempdir: /tmp is shared with
        # concurrent xdist workers, so a global glob would race their queues.
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        patcher = _patched_limits(max_upload_bytes=1024)
        try:
            response = await client.post(
                "/api/v1/imports/spotify/history",
                files=_multipart([("history.json", b"x" * 2048)]),
            )
        finally:
            patcher.stop()

        assert response.status_code == 413
        # The rejected request left no queue dir and started no queue.
        assert _queue_tmpdirs() == set()
        assert (
            await client.get("/api/v1/imports/spotify/history/queue")
        ).status_code == 404

    async def test_total_cap_rejects_before_any_entry_starts(
        self, client: httpx2.AsyncClient, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        patcher = _patched_limits(max_queued_upload_bytes=3 * 1024)
        try:
            response = await client.post(
                "/api/v1/imports/spotify/history",
                files=_multipart([("a.json", b"x" * 2048), ("b.json", b"y" * 2048)]),
            )
        finally:
            patcher.stop()

        assert response.status_code == 413
        assert _queue_tmpdirs() == set()
        assert (
            await client.get("/api/v1/imports/spotify/history/queue")
        ).status_code == 404

    async def test_too_many_files_422s(self, client: httpx2.AsyncClient) -> None:
        patcher = _patched_limits(max_queue_entries=2)
        try:
            response = await client.post(
                "/api/v1/imports/spotify/history",
                files=_multipart([
                    ("a.json", b"[]"),
                    ("b.json", b"[]"),
                    ("c.json", b"[]"),
                ]),
            )
        finally:
            patcher.stop()

        assert response.status_code == 422

    async def test_upload_within_limits_returns_queued_entries(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([("history.json", json.dumps([]).encode())]),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["queue_id"]
        assert [e["status"] for e in data["entries"]] == ["queued"]
        assert data["entries"][0]["filename"] == "history.json"
        # No operation exists yet: ids appear via the queue GET as entries start.
        assert data["entries"][0]["operation_id"] is None
        assert data["entries"][0]["run_id"] is None


class TestSpotifyHistoryQueueContract:
    """Queue endpoints' request/response contract (launcher stubbed — the
    queue holds still, so pre-drain states are observable)."""

    async def test_get_queue_404s_when_none_exists(self, client: httpx2.AsyncClient):
        response = await client.get("/api/v1/imports/spotify/history/queue")
        assert response.status_code == 404

    async def test_delete_404s_when_none_exists(self, client: httpx2.AsyncClient):
        response = await client.delete("/api/v1/imports/spotify/history/queue")
        assert response.status_code == 404

    async def test_get_returns_the_posted_queue(self, client: httpx2.AsyncClient):
        posted = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([("a.json", b"[]"), ("b.json", b"[]")]),
        )
        assert posted.status_code == 200

        fetched = await client.get("/api/v1/imports/spotify/history/queue")
        assert fetched.status_code == 200
        data = fetched.json()
        assert data["queue_id"] == posted.json()["queue_id"]
        assert [e["filename"] for e in data["entries"]] == ["a.json", "b.json"]
        assert [e["position"] for e in data["entries"]] == [0, 1]

    async def test_second_post_while_active_409s(self, client: httpx2.AsyncClient):
        first = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([("a.json", b"[]")]),
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([("b.json", b"[]")]),
        )
        assert second.status_code == 409

    async def test_delete_cancels_pending_and_unlinks_their_files(
        self, client: httpx2.AsyncClient
    ):
        posted = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([("a.json", b"[]"), ("b.json", b"[]")]),
        )
        assert posted.status_code == 200
        queue = import_queue_mod.get_queue("default")
        assert queue is not None
        assert all(entry.path.exists() for entry in queue.entries)

        deleted = await client.delete("/api/v1/imports/spotify/history/queue")

        assert deleted.status_code == 200
        assert [e["status"] for e in deleted.json()["entries"]] == [
            "cancelled",
            "cancelled",
        ]
        assert not any(entry.path.exists() for entry in queue.entries)

        # A cancelled (drained) queue no longer blocks a new upload.
        replacement = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([("c.json", b"[]")]),
        )
        assert replacement.status_code == 200
        assert replacement.json()["queue_id"] != posted.json()["queue_id"]


class TestSpotifyHistoryQueueDrain:
    """End-to-end queue drain through the real launcher and database."""

    @pytest.fixture(autouse=True)
    def _real_drain(self, _drain_import_queues_for_real):
        pass

    async def test_multi_file_upload_writes_one_run_per_file(
        self, client: httpx2.AsyncClient
    ):
        response = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([
                ("first.json", json.dumps([]).encode()),
                ("second.json", json.dumps([]).encode()),
            ]),
        )
        assert response.status_code == 200

        data = await _poll_queue_until_drained(client)
        await _settle_background_tasks()

        assert [e["status"] for e in data["entries"]] == ["complete", "complete"]
        run_ids = [e["run_id"] for e in data["entries"]]
        operation_ids = [e["operation_id"] for e in data["entries"]]
        assert None not in run_ids
        assert len(set(run_ids)) == 2
        assert None not in operation_ids
        assert len(set(operation_ids)) == 2

        # One audit row per file, each carrying its own counts.
        runs = await client.get("/api/v1/operation-runs")
        assert runs.status_code == 200
        rows = {
            row["id"]: row
            for row in runs.json()["data"]
            if row["operation_type"] == "import_spotify_history"
        }
        assert set(rows) == set(run_ids)
        for row in rows.values():
            assert row["status"] == "complete"
            assert isinstance(row["counts"], dict)

    @pytest.mark.usefixtures("_sse_subscriber_wired")
    async def test_one_stream_covers_the_whole_export(self, client: httpx2.AsyncClient):
        """The export streams as ONE operation, files as its sub-operations.

        This is the property the per-file streams could not provide: a client
        keyed to whichever file is running has to re-attach at every handover,
        and is attached to nothing in between — which is a blank view at
        exactly the moment someone checks back on a half-hour import.
        """
        response = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([
                ("first.json", json.dumps([]).encode()),
                ("second.json", json.dumps([]).encode()),
            ]),
        )
        assert response.status_code == 200
        drain_operation_id = response.json()["operation_id"]

        # Attach the way a client does: to the id the POST returned, before any
        # file has started, and never again.
        stream = await get_operation_registry().get_queue(drain_operation_id)
        assert stream is not None

        data = await _poll_queue_until_drained(client)
        await _settle_background_tasks()

        events = []
        while not stream.empty():
            event = stream.get_nowait()
            if event is SSE_SENTINEL:
                break
            events.append(event)

        kinds = [e["event"] for e in events]
        assert kinds[0] == "started"
        assert kinds[-1] == "complete"

        # An item event names the generation the reader renders as a row. For a
        # file that is the file itself; for a phase running inside it, the walk
        # still attributes the event to the file. That is what lets one row own
        # everything happening under it without the client rebuilding the tree.
        def _rows(event_type: str) -> list[dict]:
            return [
                e["data"]
                for e in events
                if e["event"] == event_type
                and e["data"]["item_operation_id"] == e["data"]["operation_id"]
            ]

        assert len(_rows("sub_operation_started")) == 2
        assert len(_rows("sub_operation_completed")) == 2

        # Each file opens before it closes, and closes before the next opens —
        # the strict handover the queue's whole design rests on.
        file_ids = {d["operation_id"] for d in _rows("sub_operation_started")}
        lifecycle = [
            e["event"]
            for e in events
            if e["event"].startswith("sub_operation_")
            and e["data"]["operation_id"] in file_ids
            and e["data"]["item_operation_id"] == e["data"]["operation_id"]
        ]
        assert lifecycle == [
            "sub_operation_started",
            "sub_operation_completed",
            "sub_operation_started",
            "sub_operation_completed",
        ]

        # A finished file carries its own numbers on the drain's stream, so the
        # row keeps them once that file's own stream has closed.
        for data in _rows("sub_operation_completed"):
            assert data["final_status"] == "completed"
            assert isinstance(data["counts"], dict)

        # The phases inside a file are attributed to it, and say which phase
        # they are — the difference between "still going" and "resolving".
        phases = {
            e["data"]["phase"]
            for e in events
            if e["event"] == "sub_operation_started"
            and e["data"]["item_operation_id"] != e["data"]["operation_id"]
        }
        assert "fetch" in phases

        # Overall position is counted in files and never rewinds.
        positions = [e["data"]["current"] for e in events if e["event"] == "progress"]
        assert positions == sorted(positions)
        assert positions[-1] == 2
        assert events[-1]["data"]["counts"] == {"files": 2, "files_complete": 2}

    async def test_queue_entries_carry_sizes_and_settled_counts(
        self, client: httpx2.AsyncClient
    ):
        """A reload an hour in must show what each file actually imported."""
        body = json.dumps([]).encode()
        response = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([("first.json", body), ("second.json", body * 3)]),
        )
        assert response.status_code == 200

        data = await _poll_queue_until_drained(client)
        await _settle_background_tasks()

        # Sizes are the real bytes written, recorded at upload — a client
        # weighting "time left" by work needs them, and a GDPR export's files
        # differ several-fold.
        assert [e["size_bytes"] for e in data["entries"]] == [len(body), len(body) * 3]
        for entry in data["entries"]:
            assert entry["started_at"] is not None
            assert entry["settled_at"] is not None
            assert isinstance(entry["counts"], dict)

    async def test_failing_entry_records_error_and_queue_continues(
        self, client: httpx2.AsyncClient, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        response = await client.post(
            "/api/v1/imports/spotify/history",
            files=_multipart([
                ("good-1.json", json.dumps([]).encode()),
                ("broken.json", b"this is not json"),
                ("good-2.json", json.dumps([]).encode()),
            ]),
        )
        assert response.status_code == 200

        data = await _poll_queue_until_drained(client)
        await _settle_background_tasks()

        statuses = [e["status"] for e in data["entries"]]
        assert statuses[1] == "error"
        assert statuses[0] == "complete"
        assert statuses[2] == "complete"
        # Every temp file — the failing one's included — and the queue dir
        # itself are gone once the drain finishes.
        assert _queue_tmpdirs() == set()


class TestCheckpointEndpoints:
    """Tests the checkpoint status retrieval endpoint."""

    async def test_get_checkpoints_returns_list(self, client: httpx2.AsyncClient):
        response = await client.get("/api/v1/imports/checkpoints")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert (
            len(data) == 4
        )  # spotify/likes, lastfm/likes, lastfm/plays, spotify/plays

    async def test_get_checkpoints_schema(self, client: httpx2.AsyncClient):
        response = await client.get("/api/v1/imports/checkpoints")

        data = response.json()
        for checkpoint in data:
            assert "service" in checkpoint
            assert "entity_type" in checkpoint
            assert "has_previous_sync" in checkpoint
            assert checkpoint["entity_type"] in ("likes", "plays")

    async def test_checkpoints_no_previous_sync_for_fresh_db(
        self, client: httpx2.AsyncClient
    ):
        response = await client.get("/api/v1/imports/checkpoints")

        data = response.json()
        for checkpoint in data:
            assert checkpoint["has_previous_sync"] is False
            assert checkpoint["last_sync_timestamp"] is None


class TestOperationEndpoints:
    """Tests for the operation progress endpoints."""

    async def test_unknown_operation_progress_returns_404(
        self, client: httpx2.AsyncClient
    ):
        response = await client.get("/api/v1/operations/nonexistent-id/progress")

        assert response.status_code == 404


class TestSSEProgressStreaming:
    """Tests that the SSE progress endpoint delivers properly encoded events.

    These tests pre-load events onto the SSE queue before connecting,
    so the generator yields them immediately and terminates on the sentinel.
    """

    async def test_sse_stream_delivers_progress_event(self, client: httpx2.AsyncClient):
        """A single progress event is delivered in SSE wire format."""
        registry = get_operation_registry()
        operation_id = "test-sse-progress"
        queue = await registry.register(operation_id)

        await queue.put({
            "id": "evt_1",
            "event": "progress",
            "data": {"current": 5, "total": 10, "message": "Working..."},
        })
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(f"/api/v1/operations/{operation_id}/progress")

            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            events = _parse_sse_events(response.text)
            progress_events = [e for e in events if e.get("event") == "progress"]
            assert len(progress_events) == 1

            data = json.loads(progress_events[0]["data"])
            assert data["current"] == 5
            assert data["total"] == 10
            assert data["message"] == "Working..."
            assert progress_events[0]["id"] == "evt_1"
        finally:
            await registry.unregister(operation_id)

    async def test_sse_stream_delivers_multiple_events(
        self, client: httpx2.AsyncClient
    ):
        """Multiple events are delivered in sequence."""
        registry = get_operation_registry()
        operation_id = "test-sse-multi"
        queue = await registry.register(operation_id)

        await queue.put({
            "id": "evt_1",
            "event": "started",
            "data": {"operation_id": operation_id, "description": "Test Op"},
        })
        await queue.put({
            "id": "evt_2",
            "event": "progress",
            "data": {"current": 3, "total": 10, "message": "Batch 1..."},
        })
        await queue.put({
            "id": "evt_3",
            "event": "complete",
            "data": {"operation_id": operation_id, "final_status": "completed"},
        })
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(f"/api/v1/operations/{operation_id}/progress")

            events = _parse_sse_events(response.text)
            typed_events = [e for e in events if "event" in e]
            assert len(typed_events) == 3

            assert typed_events[0]["event"] == "started"
            assert typed_events[1]["event"] == "progress"
            assert typed_events[2]["event"] == "complete"

            # IDs are sequential
            assert typed_events[0]["id"] == "evt_1"
            assert typed_events[1]["id"] == "evt_2"
            assert typed_events[2]["id"] == "evt_3"
        finally:
            await registry.unregister(operation_id)

    async def test_sse_reconnection_skips_already_received_events(
        self, client: httpx2.AsyncClient
    ):
        """Last-Event-ID causes events with lower sequence numbers to be skipped."""
        registry = get_operation_registry()
        operation_id = "test-sse-reconnect"
        queue = await registry.register(operation_id)

        # Simulate events where client already saw evt_1 and evt_2
        await queue.put({
            "id": "evt_1",
            "event": "progress",
            "data": {"current": 1, "total": 5, "message": "Old event 1"},
        })
        await queue.put({
            "id": "evt_2",
            "event": "progress",
            "data": {"current": 2, "total": 5, "message": "Old event 2"},
        })
        await queue.put({
            "id": "evt_3",
            "event": "progress",
            "data": {"current": 3, "total": 5, "message": "New event"},
        })
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(
                f"/api/v1/operations/{operation_id}/progress",
                headers={"Last-Event-ID": "evt_2"},
            )

            events = _parse_sse_events(response.text)
            progress_events = [e for e in events if e.get("event") == "progress"]

            # Only evt_3 should come through (evt_1 and evt_2 are skipped)
            assert len(progress_events) == 1
            data = json.loads(progress_events[0]["data"])
            assert data["current"] == 3
            assert data["message"] == "New event"
        finally:
            await registry.unregister(operation_id)

    async def test_sse_data_is_json_encoded(self, client: httpx2.AsyncClient):
        """SSE data field contains valid JSON (auto-serialized by ServerSentEvent)."""
        registry = get_operation_registry()
        operation_id = "test-sse-json"
        queue = await registry.register(operation_id)

        await queue.put({
            "id": "evt_1",
            "event": "progress",
            "data": {
                "operation_id": operation_id,
                "current": 0,
                "total": None,
                "message": "Starting...",
                "items_per_second": None,
            },
        })
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(f"/api/v1/operations/{operation_id}/progress")

            events = _parse_sse_events(response.text)
            progress_events = [e for e in events if e.get("event") == "progress"]
            assert len(progress_events) == 1

            # data field must be valid JSON
            data = json.loads(progress_events[0]["data"])
            assert data["operation_id"] == operation_id
            assert data["total"] is None
        finally:
            await registry.unregister(operation_id)

    async def test_sse_sentinel_closes_stream(self, client: httpx2.AsyncClient):
        """Sentinel without any preceding events produces an empty stream."""
        registry = get_operation_registry()
        operation_id = "test-sse-sentinel"
        queue = await registry.register(operation_id)

        # Just the sentinel — generator should exit immediately
        await queue.put(SSE_SENTINEL)

        try:
            response = await client.get(f"/api/v1/operations/{operation_id}/progress")

            assert response.status_code == 200
            events = _parse_sse_events(response.text)
            # No real events (may have keep-alive comments, but those are filtered)
            typed_events = [e for e in events if "event" in e]
            assert len(typed_events) == 0
        finally:
            await registry.unregister(operation_id)
