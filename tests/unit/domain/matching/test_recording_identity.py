"""The shared "are these the same recording?" predicate.

Both canonical-reuse gates — the Spotify inward resolver and
``ingest_external_tracks_bulk`` — ask this one question, so its two
strictnesses are tested here rather than twice at the call sites: normalized
*equality* (never similarity) and an unknown duration refusing reuse.
"""

from src.domain.entities import Artist, Track
from src.domain.matching.isrc_validation import SUSPECT_DURATION_DIFF_MS
from src.domain.matching.recording_identity import (
    RecordingDescription,
    describe_track,
    describes_same_recording,
    identity_key,
)


def _description(
    title: str = "Sylvia Says",
    artist: str = "Ame",
    duration_ms: int | None = 240_000,
) -> RecordingDescription:
    return RecordingDescription(title=title, artist=artist, duration_ms=duration_ms)


class TestNormalizedEqualityNotSimilarity:
    def test_a_remaster_of_the_same_length_is_the_same_recording(self):
        original = _description(duration_ms=245_733)
        remaster = _description(duration_ms=245_733)

        assert describes_same_recording(original, remaster) is True

    def test_normalization_absorbs_smart_quotes_and_case(self):
        assert (
            describes_same_recording(
                _description(title="Kristian‘s Vote", artist="AME"),
                _description(title="Kristian's Vote", artist="Ame"),
            )
            is True
        )

    def test_a_remix_is_never_the_track_it_remixes(self):
        """The probe answers on a parenthetical-stripped form, so a similarity
        bar would pair these — and their lengths are close enough that the
        duration guard would not catch it either."""
        original = _description(title="Ice Ice Baby", duration_ms=257_000)
        remix = _description(
            title="Ice Ice Baby (Wunderbros Dubstep Remix)", duration_ms=251_200
        )

        assert describes_same_recording(original, remix) is False

    def test_a_different_artist_of_the_same_title_is_refused(self):
        assert (
            describes_same_recording(
                _description(artist="Johnny Cash"),
                _description(artist="Nine Inch Nails"),
            )
            is False
        )

    def test_an_empty_title_or_artist_never_matches_another_empty_one(self):
        assert (
            describes_same_recording(_description(title=""), _description(title=""))
            is False
        )
        assert (
            describes_same_recording(_description(artist=""), _description(artist=""))
            is False
        )


class TestDurationGuard:
    def test_a_gap_inside_the_tolerance_is_one_recording(self):
        assert (
            describes_same_recording(
                _description(duration_ms=240_000),
                _description(duration_ms=240_000 + SUSPECT_DURATION_DIFF_MS),
            )
            is True
        )

    def test_one_millisecond_past_the_tolerance_is_two_recordings(self):
        assert (
            describes_same_recording(
                _description(duration_ms=240_000),
                _description(duration_ms=240_001 + SUSPECT_DURATION_DIFF_MS),
            )
            is False
        )

    def test_an_unknown_duration_refuses_reuse(self):
        """Unlike the ISRC path, nothing else vouches for this pair — the
        durations are the whole of the evidence."""
        assert (
            describes_same_recording(
                _description(duration_ms=None), _description(duration_ms=240_000)
            )
            is False
        )
        assert (
            describes_same_recording(
                _description(duration_ms=240_000), _description(duration_ms=None)
            )
            is False
        )
        assert (
            describes_same_recording(
                _description(duration_ms=None), _description(duration_ms=None)
            )
            is False
        )

    def test_a_zero_duration_reads_as_unknown_not_as_a_perfect_match(self):
        assert (
            describes_same_recording(
                _description(duration_ms=0), _description(duration_ms=0)
            )
            is False
        )


class TestIdentityKey:
    def test_the_key_partitions_exactly_what_the_predicate_requires(self):
        """Bucketing by the key can never hide a pair the predicate accepts."""
        a = _description(title="Kristian‘s Vote", artist="AME")
        b = _description(title="Kristian's Vote", artist="Ame")

        assert identity_key(a) == identity_key(b)

    def test_a_stripped_parenthetical_keys_differently(self):
        assert identity_key(_description(title="Ice Ice Baby")) != identity_key(
            _description(title="Ice Ice Baby (Wunderbros Dubstep Remix)")
        )


class TestDescribeTrack:
    def test_a_canonical_is_described_by_its_primary_artist(self):
        track = Track(
            title="Oh The Sunn!",
            artists=[Artist(name="The Avalanches"), Artist(name="Perry Farrell")],
            duration_ms=235_400,
        )

        assert describe_track(track) == RecordingDescription(
            title="Oh The Sunn!", artist="The Avalanches", duration_ms=235_400
        )

    def test_a_canonical_with_a_blank_artist_name_is_unmatchable(self):
        """``Track`` guarantees an artist entry, never that it is named."""
        track = Track(title="Untitled", artists=[Artist(name="")], duration_ms=100_000)

        assert (
            describes_same_recording(
                describe_track(track), _description(title="Untitled", artist="")
            )
            is False
        )
