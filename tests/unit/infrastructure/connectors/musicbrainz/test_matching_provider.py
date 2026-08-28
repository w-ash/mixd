"""Tests for the MusicBrainz matching provider's ISRC match payloads
and batch-correlation failure semantics.

FLIPPED characterization (FM1g, fixed by v0.8.18 epic 2): the original pins
recorded ISRC matches created with empty title/artist/duration, scored with
mismatch penalties, and auto-accepted at exactly 98 with the duration-based
suspect check unreachable. Now the provider carries the recording metadata
the /isrc lookup already fetched, missing metadata classifies as neutral
MISSING levels, and an ISRC-grade match whose duration comparison is missing
routes to review instead of auto-accepting.

See docs/backlog/identity-resolution-design-space.md §4 (test 8).
"""

from unittest.mock import AsyncMock, patch

from src.config import create_evaluation_service
from src.domain.matching.types import MatchFailureReason
from src.infrastructure.connectors.musicbrainz.connector import MusicBrainzConnector
from src.infrastructure.connectors.musicbrainz.matching_provider import (
    MusicBrainzProvider,
)
from src.infrastructure.connectors.musicbrainz.models import (
    MusicBrainzArtistCredit,
    MusicBrainzRecording,
)
from tests.fixtures import make_track


def _recording(*, length: int | None) -> MusicBrainzRecording:
    return MusicBrainzRecording(
        id="mbid-rec-0001",
        title="Gold Rush",
        length=length,
        artist_credit=[MusicBrainzArtistCredit(name="Neon Priest")],
    )


class TestIsrcMatchPayload:
    """ISRC raw matches carry the recording metadata the lookup fetched."""

    async def _fetch_raw_match(self, track, *, length: int | None):
        connector = AsyncMock()
        connector.batch_isrc_lookup.return_value = {
            "USNP12400001": _recording(length=length)
        }
        provider = MusicBrainzProvider(connector_instance=connector)
        result = await provider.fetch_raw_matches_for_tracks([track])
        return result.matches[track.id]

    async def test_isrc_raw_match_carries_recording_metadata(self):
        """The /isrc response's title/credits/length reach service_data."""
        track = make_track(
            title="Gold Rush",
            artist="Neon Priest",
            isrc="USNP12400001",
            duration_ms=200_000,
        )
        raw = await self._fetch_raw_match(track, length=200_000)

        assert raw["match_method"] == "isrc"
        assert raw["connector_id"] == "mbid-rec-0001"
        assert raw["service_data"]["title"] == "Gold Rush"
        assert raw["service_data"]["artist"] == "Neon Priest"
        assert raw["service_data"]["artists"] == ["Neon Priest"]
        assert raw["service_data"]["duration_ms"] == 200_000
        assert raw["service_data"]["mbid"] == "mbid-rec-0001"

    async def test_isrc_match_with_duration_auto_accepts(self):
        """With real metadata the suspect check runs and the match accepts."""
        track = make_track(
            title="Gold Rush",
            artist="Neon Priest",
            isrc="USNP12400001",
            duration_ms=200_000,
        )
        raw = await self._fetch_raw_match(track, length=200_000)

        match = create_evaluation_service().evaluate_single_match(
            track, raw, "musicbrainz"
        )

        assert match.success is True
        assert match.review_required is False
        assert match.evidence is not None
        assert match.evidence.isrc_suspect is False
        assert match.evidence.duration_missing is False

    async def test_isrc_match_without_length_routes_to_review(self):
        """A recording with no length cannot auto-accept (suspect unreachable).

        MusicBrainz recordings may lack a length; the duration-based ISRC
        suspect check then has nothing to compare, so the match routes to
        review regardless of its (ISRC-dominated) confidence.
        """
        track = make_track(
            title="Gold Rush",
            artist="Neon Priest",
            isrc="USNP12400001",
            duration_ms=200_000,
        )
        raw = await self._fetch_raw_match(track, length=None)

        match = create_evaluation_service().evaluate_single_match(
            track, raw, "musicbrainz"
        )

        assert match.success is False
        assert match.review_required is True
        assert match.evidence is not None
        assert match.evidence.duration_missing is True
        assert match.evidence.isrc_suspect is False


class TestIsrcBatchCorrelation:
    """Batch-result correlation keeps miss vs unanswered code distinct."""

    @staticmethod
    def _provider(
        batch_result: dict[str, MusicBrainzRecording | None],
    ) -> MusicBrainzProvider:
        connector = AsyncMock()
        connector.batch_isrc_lookup.return_value = batch_result
        connector.search_recording.return_value = None  # quiet fallback
        return MusicBrainzProvider(connector_instance=connector)

    async def test_isrc_answered_with_none_fails_as_no_results(self):
        """A code the API answered with no recording is a genuine miss."""
        track = make_track(title="Gold Rush", artist="Neon Priest", isrc="USNP12400001")
        provider = self._provider({"USNP12400001": None})

        result = await provider.fetch_raw_matches_for_tracks([track])

        isrc_failures = [f for f in result.failures if f.method == "isrc"]
        assert [f.reason for f in isrc_failures] == [MatchFailureReason.NO_RESULTS]
        assert not result.matches

    async def test_isrc_absent_from_batch_result_fails_as_api_error(self):
        """A code the batch never answered is unanswered, not absent."""
        track = make_track(title="Gold Rush", artist="Neon Priest", isrc="USNP12400001")
        provider = self._provider({})

        result = await provider.fetch_raw_matches_for_tracks([track])

        isrc_failures = [f for f in result.failures if f.method == "isrc"]
        assert [f.reason for f in isrc_failures] == [MatchFailureReason.API_ERROR]
        assert "USNP12400001" in isrc_failures[0].details
        assert not result.matches

    async def test_lookup_error_surfaces_as_api_error_through_real_batch(self):
        """A raised per-ISRC lookup classifies as API_ERROR, not NO_RESULTS.

        Runs the real connector batch (per-ISRC lookup mocked) so the
        omit-errored-codes contract is exercised end to end: the errored
        code's track fails as ``API_ERROR`` with the code in the details,
        while an answered-``None`` code's track stays ``NO_RESULTS``.
        """
        errored = make_track(
            title="Gold Rush", artist="Neon Priest", isrc="USNP12400001"
        )
        missed = make_track(
            title="Silver Ash", artist="Neon Priest", isrc="USNP12400002"
        )

        async def lookup(isrc: str) -> MusicBrainzRecording | None:
            if isrc == "USNP12400001":
                raise ConnectionError("MusicBrainz unreachable")
            return None

        with (
            patch.object(
                MusicBrainzConnector,
                "get_recording_by_isrc",
                AsyncMock(side_effect=lookup),
            ),
            patch.object(
                MusicBrainzConnector,
                "search_recording",
                AsyncMock(return_value=None),  # quiet fallback
            ),
        ):
            connector = MusicBrainzConnector()
            provider = MusicBrainzProvider(connector_instance=connector)
            result = await provider.fetch_raw_matches_for_tracks([errored, missed])
            await connector.aclose()

        isrc_failures = {f.track_id: f for f in result.failures if f.method == "isrc"}
        assert isrc_failures[errored.id].reason == MatchFailureReason.API_ERROR
        assert "USNP12400001" in isrc_failures[errored.id].details
        assert isrc_failures[missed.id].reason == MatchFailureReason.NO_RESULTS
        assert not result.matches

    async def test_duplicate_isrcs_collapse_to_one_lookup(self):
        """Tracks sharing an ISRC share one lookup and both match."""
        tracks = [
            make_track(title="Gold Rush", artist="Neon Priest", isrc="USNP12400001"),
            make_track(
                title="Gold Rush (Live)", artist="Neon Priest", isrc="USNP12400001"
            ),
        ]
        connector = AsyncMock()
        connector.batch_isrc_lookup.return_value = {
            "USNP12400001": _recording(length=200_000)
        }
        provider = MusicBrainzProvider(connector_instance=connector)

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        connector.batch_isrc_lookup.assert_awaited_once_with(["USNP12400001"])
        assert set(result.matches) == {t.id for t in tracks}
