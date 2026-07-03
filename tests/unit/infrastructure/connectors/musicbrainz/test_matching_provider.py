"""Characterization tests for the MusicBrainz matching provider.

Characterization (FM1g): pins CURRENT (buggy) behavior — a MusicBrainz ISRC
match is created with empty title/artist and no duration, scores empty
metadata as MISMATCH penalties that the ISRC evidence overwhelms, and
auto-accepts with the duration-based suspect check structurally unreachable.
Flipped by: Confidence integrity repair (v0.8.18 epic 2 — MISSING comparison
levels make empty metadata neutral, the provider populates service_data from
the recording lookup, and an ISRC match without duration cannot auto-accept).

See docs/backlog/identity-resolution-design-space.md §4 (test 8).
"""

from unittest.mock import AsyncMock

from src.config import create_evaluation_service
from src.infrastructure.connectors.musicbrainz.matching_provider import (
    MusicBrainzProvider,
)
from tests.fixtures import make_track


class TestIsrcMatchWithEmptyMetadata:
    """Characterization (FM1g): empty-metadata ISRC match auto-accepts at 98."""

    async def _fetch_raw_match(self, track):
        connector = AsyncMock()
        connector.batch_isrc_lookup.return_value = {"USNP12400001": "mbid-rec-0001"}
        provider = MusicBrainzProvider(connector_instance=connector)
        result = await provider.fetch_raw_matches_for_tracks([track])
        return result.matches[track.id]

    async def test_isrc_raw_match_carries_empty_metadata(self):
        """The provider discards the recording metadata the lookup fetched.

        Characterization (FM1g): pins that service_data arrives empty — the
        /isrc/{isrc} response includes title/artist-credit/length, but only
        the MBID survives. Flipped by: Confidence integrity repair (provider
        populates service_data from the MusicBrainzRecording).
        """
        track = make_track(
            title="Gold Rush",
            artist="Neon Priest",
            isrc="USNP12400001",
            duration_ms=200_000,
        )
        raw = await self._fetch_raw_match(track)

        assert raw["match_method"] == "isrc"
        assert raw["connector_id"] == "mbid-rec-0001"
        assert raw["service_data"]["title"] == ""
        assert raw["service_data"]["artist"] == ""
        assert raw["service_data"]["duration_ms"] is None

    async def test_empty_metadata_isrc_match_cannot_auto_accept(self):
        """Empty metadata is neutral; no duration → review, never auto-accept.

        FLIPPED characterization (FM1g, fixed by Confidence integrity repair):
        the original pin recorded confidence 98 with empty title/artist scored
        as MISMATCH (-2.4849/-2.6391) and auto-accept with the duration-based
        suspect check unreachable. Now missing title/artist classify as
        MISSING (LLR exactly 0, so ISRC_EXACT alone → confidence 100) and an
        ISRC-grade match whose duration comparison is missing routes to
        review — a human confirms what the suspect check cannot.
        """
        track = make_track(
            title="Gold Rush",
            artist="Neon Priest",
            isrc="USNP12400001",
            duration_ms=200_000,
        )
        raw = await self._fetch_raw_match(track)

        match = create_evaluation_service().evaluate_single_match(
            track, raw, "musicbrainz"
        )

        assert match.confidence == 100
        assert match.success is False
        assert match.review_required is True
        assert match.evidence is not None
        assert match.evidence.isrc_suspect is False
        assert match.evidence.duration_missing is True
        # Missing title/artist are neutral, not penalties.
        assert match.evidence.title_score == 0.0
        assert match.evidence.artist_score == 0.0
        assert match.evidence.duration_score == 0.0
