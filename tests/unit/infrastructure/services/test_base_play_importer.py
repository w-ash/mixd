"""Unit tests for the play-import pipeline's tenancy guard."""

from datetime import UTC, datetime

import pytest

from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.services.base_play_importer import _require_uniform_tenancy


def _make_play(track_name: str, **overrides: str) -> ConnectorTrackPlay:
    """Build a minimal valid connector play.

    ``user_id`` is left to the entity's ``"default"`` attrs default unless an
    override is given — that default is the realistic failure shape a chunked
    importer produces when it forgets to stamp a row.
    """
    return ConnectorTrackPlay(
        artist_name="Test Artist",
        track_name=track_name,
        played_at=datetime(2024, 6, 15, 14, 30, tzinfo=UTC),
        service="lastfm",
        **overrides,
    )


class TestRequireUniformTenancy:
    """Every ledger row must carry the importing user's ID before persistence."""

    def test_uniformly_stamped_batch_passes(self) -> None:
        """A batch stamped end-to-end with the importing user is accepted."""
        track_plays = [
            _make_play("First", user_id="u1"),
            _make_play("Second", user_id="u1"),
            _make_play("Third", user_id="u1"),
        ]

        _require_uniform_tenancy(track_plays, "u1", "LastfmPlayImporter")

    def test_empty_batch_passes(self) -> None:
        """No rows means no tenancy to violate."""
        _require_uniform_tenancy([], "u1", "LastfmPlayImporter")

    def test_mixed_stamp_batch_raises_naming_the_offending_index(self) -> None:
        """A defaulted row past the first is caught, not waved through."""
        track_plays = [
            _make_play("First", user_id="u1"),
            _make_play("Second"),  # takes the "default" attrs default
            _make_play("Third", user_id="u1"),
        ]

        with pytest.raises(RuntimeError) as exc_info:
            _require_uniform_tenancy(track_plays, "u1", "LastfmPlayImporter")

        message = str(exc_info.value)
        assert "play 1" in message
        assert "'default'" in message
        assert "LastfmPlayImporter" in message
