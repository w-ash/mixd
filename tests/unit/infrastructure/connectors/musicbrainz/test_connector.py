"""Tests for the MusicBrainz connector's batch ISRC lookup result contract.

A code mapped to ``None`` is an answered catalog miss; a code whose lookup
raised is omitted from the result so the matching provider can classify it
as ``API_ERROR`` instead of ``NO_RESULTS``. The API client is the mocked
boundary.
"""

from unittest.mock import AsyncMock, call

from src.infrastructure.connectors.musicbrainz.connector import MusicBrainzConnector
from src.infrastructure.connectors.musicbrainz.models import (
    MusicBrainzArtistCredit,
    MusicBrainzRecording,
)

ERRORED = "USNP12400001"
FOUND = "USNP12400002"
MISSED = "USNP12400003"


def _recording() -> MusicBrainzRecording:
    return MusicBrainzRecording(
        id="mbid-rec-0001",
        title="Gold Rush",
        length=200_000,
        artist_credit=[MusicBrainzArtistCredit(name="Neon Priest")],
    )


async def _lookup(isrc: str) -> MusicBrainzRecording | None:
    if isrc == ERRORED:
        raise ConnectionError("MusicBrainz unreachable")
    return _recording() if isrc == FOUND else None


async def _connector_over(client: AsyncMock) -> MusicBrainzConnector:
    """A connector whose API client is ``client`` (its real pool closed)."""
    connector = MusicBrainzConnector()
    await connector.aclose()
    connector._client = client
    return connector


class TestBatchIsrcLookupContract:
    """Errored codes are omitted; answered codes (hit or miss) are kept."""

    async def test_errored_code_omitted_answered_codes_kept(self):
        """A raised lookup drops its code without failing the rest of the batch."""
        client = AsyncMock()
        client.get_recording_by_isrc.side_effect = _lookup
        connector = await _connector_over(client)

        results = await connector.batch_isrc_lookup([ERRORED, FOUND, MISSED])

        assert ERRORED not in results
        found = results[FOUND]
        assert found is not None
        assert found.id == "mbid-rec-0001"
        assert MISSED in results
        assert results[MISSED] is None
        assert client.get_recording_by_isrc.await_args_list == [
            call(ERRORED),
            call(FOUND),
            call(MISSED),
        ]

    async def test_empty_input_returns_empty_result(self):
        """No ISRCs means no lookups and an empty result."""
        client = AsyncMock()
        connector = await _connector_over(client)

        results = await connector.batch_isrc_lookup([])

        assert results == {}
        client.get_recording_by_isrc.assert_not_awaited()
