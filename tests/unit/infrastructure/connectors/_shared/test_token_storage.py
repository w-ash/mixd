"""Tests for TokenStorage protocol implementations."""

import json
from pathlib import Path

import pytest

from src.infrastructure.connectors._shared.token_storage import (
    FileTokenStorage,
    StoredToken,
)


class TestFileTokenStorage:
    """Tests for FileTokenStorage (file-backed credential persistence)."""

    @pytest.fixture
    def storage(self, tmp_path: Path) -> FileTokenStorage:
        return FileTokenStorage(cache_dir=tmp_path)

    async def test_load_returns_none_when_no_file(self, storage: FileTokenStorage):
        result = await storage.load_token("spotify")
        assert result is None

    async def test_save_and_load_spotify_token(self, storage: FileTokenStorage):
        token: StoredToken = {
            "access_token": "access-123",
            "refresh_token": "refresh-456",
            "token_type": "Bearer",
            "expires_in": 3600,
            "expires_at": 1999999999,
            "scope": "playlist-read-private",
        }
        await storage.save_token("spotify", token)
        loaded = await storage.load_token("spotify")

        assert loaded is not None
        assert loaded["access_token"] == "access-123"
        assert loaded["refresh_token"] == "refresh-456"
        assert loaded["expires_at"] == 1999999999

    async def test_save_uses_spotify_cache_filename(
        self, storage: FileTokenStorage, tmp_path: Path
    ):
        await storage.save_token("spotify", StoredToken(access_token="test"))
        assert (tmp_path / ".spotify_cache").exists()

    async def test_save_uses_service_name_for_other_services(
        self, storage: FileTokenStorage, tmp_path: Path
    ):
        await storage.save_token("lastfm", StoredToken(session_key="sk-123"))
        assert (tmp_path / ".lastfm_cache").exists()

    async def test_save_and_load_lastfm_session_key(self, storage: FileTokenStorage):
        token: StoredToken = {
            "session_key": "session-key-abc",
            "token_type": "session",
            "account_name": "testuser",
        }
        await storage.save_token("lastfm", token)
        loaded = await storage.load_token("lastfm")

        assert loaded is not None
        assert loaded["session_key"] == "session-key-abc"
        assert loaded["account_name"] == "testuser"

    async def test_delete_removes_file(
        self, storage: FileTokenStorage, tmp_path: Path
    ):
        await storage.save_token("spotify", StoredToken(access_token="test"))
        assert (tmp_path / ".spotify_cache").exists()

        await storage.delete_token("spotify")
        assert not (tmp_path / ".spotify_cache").exists()

    async def test_delete_nonexistent_is_noop(self, storage: FileTokenStorage):
        await storage.delete_token("spotify")  # Should not raise

    async def test_load_returns_none_on_malformed_json(
        self, storage: FileTokenStorage, tmp_path: Path
    ):
        (tmp_path / ".spotify_cache").write_text("not valid json{{{")
        result = await storage.load_token("spotify")
        assert result is None

    async def test_save_overwrites_existing(self, storage: FileTokenStorage):
        await storage.save_token("spotify", StoredToken(access_token="old"))
        await storage.save_token("spotify", StoredToken(access_token="new"))

        loaded = await storage.load_token("spotify")
        assert loaded is not None
        assert loaded["access_token"] == "new"

    async def test_backward_compat_with_spotipy_format(
        self, storage: FileTokenStorage, tmp_path: Path
    ):
        """Existing .spotify_cache files from spotipy should load correctly."""
        spotipy_data = {
            "access_token": "BQDo...",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "playlist-read-private",
            "expires_at": 1771272642,
            "refresh_token": "AQCz...",
        }
        (tmp_path / ".spotify_cache").write_text(json.dumps(spotipy_data))

        loaded = await storage.load_token("spotify")
        assert loaded is not None
        assert loaded["access_token"] == "BQDo..."
        assert loaded["refresh_token"] == "AQCz..."
        assert loaded["expires_at"] == 1771272642
