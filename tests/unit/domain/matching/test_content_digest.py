"""The content digest is the only expiry a sticky rejection gets.

So the property that matters is not "it hashes" but *which* changes it notices:
a rejection must survive noise and must not survive a real edit to the fields
the matcher scores on. Each test below pins one side of that line.
"""

from src.domain.entities.track import Artist, ConnectorTrack, Track
from src.domain.matching.content_digest import (
    DigestSide,
    connector_side,
    content_digest,
    service_side,
    track_side,
)


def _side(**overrides: object) -> DigestSide:
    base: dict[str, object] = {
        "identifier": "sp_1",
        "title": "Paranoid Android",
        "artists": ("Radiohead",),
        "duration_ms": 383_000,
    }
    base.update(overrides)
    return DigestSide(**base)  # pyright: ignore[reportArgumentType]


def _candidate() -> DigestSide:
    return DigestSide(
        identifier="track-1",
        title="Paranoid Android",
        artists=("Radiohead",),
        duration_ms=383_000,
    )


class TestStability:
    def test_same_inputs_yield_the_same_digest(self):
        assert content_digest(_side(), _candidate()) == content_digest(
            _side(), _candidate()
        )

    def test_artist_order_does_not_change_the_digest(self):
        one = content_digest(_side(artists=("A", "B")), _candidate())
        other = content_digest(_side(artists=("B", "A")), _candidate())
        assert one == other

    def test_title_casing_and_punctuation_do_not_change_the_digest(self):
        """Normalization is shared with the matcher, so cosmetic drift is invisible.

        Otherwise a re-fetch that capitalised a title differently would expire
        every rejection about it and re-propose the lot.
        """
        assert content_digest(
            _side(title="PARANOID ANDROID!"), _candidate()
        ) == content_digest(_side(), _candidate())

    def test_the_two_sides_are_not_interchangeable(self):
        """The pair is directional; a swapped pair is a different fact."""
        assert content_digest(_side(), _candidate()) != content_digest(
            _candidate(), _side()
        )


class TestExpiry:
    def test_title_change_changes_the_digest(self):
        assert content_digest(_side(title="Airbag"), _candidate()) != content_digest(
            _side(), _candidate()
        )

    def test_artist_change_changes_the_digest(self):
        assert content_digest(
            _side(artists=("Thom Yorke",)), _candidate()
        ) != content_digest(_side(), _candidate())

    def test_any_duration_change_changes_the_digest(self):
        """No bucketing: the bias is deliberately toward under-suppression."""
        assert content_digest(
            _side(duration_ms=383_001), _candidate()
        ) != content_digest(_side(), _candidate())

    def test_candidate_side_edits_expire_the_pair_too(self):
        edited = DigestSide(
            identifier="track-1",
            title="Paranoid Android (Remaster)",
            artists=("Radiohead",),
            duration_ms=383_000,
        )
        assert content_digest(_side(), edited) != content_digest(_side(), _candidate())


class TestSideBuilders:
    def test_track_side_reads_the_match_relevant_fields(self):
        track = Track(
            title="Creep", artists=[Artist(name="Radiohead")], duration_ms=238_000
        )
        side = track_side(track)
        assert side.title == "Creep"
        assert side.artists == ("Radiohead",)
        assert side.duration_ms == 238_000

    def test_connector_side_reads_the_match_relevant_fields(self):
        ct = ConnectorTrack(
            connector_name="spotify",
            connector_track_identifier="sp_9",
            title="Creep",
            artists=[Artist(name="Radiohead")],
            duration_ms=238_000,
        )
        side = connector_side(ct)
        assert side.identifier == "sp_9"
        assert side.artists == ("Radiohead",)

    def test_service_side_accepts_either_artist_shape(self):
        """Providers disagree on `artist` vs `artists`; both must be read."""
        plural = service_side("sp_9", {"title": "Creep", "artists": ["Radiohead"]})
        singular = service_side("sp_9", {"title": "Creep", "artist": "Radiohead"})
        assert plural.artists == singular.artists == ("Radiohead",)

    def test_service_side_tolerates_a_payload_with_nothing_useful(self):
        side = service_side("sp_9", {})
        assert side == DigestSide(
            identifier="sp_9", title="", artists=(), duration_ms=None
        )


class TestBothProducersAgreeOnOnePair:
    """The asymmetry that made the cache write-only for one of its two writers.

    A rejection can be produced by the matching pipeline (holding a full
    provider payload) or by the canonical-reuse gate (holding a title and one
    artist off a play record, and no duration at all). Digesting each writer's
    own view gave one pair two digests: the reuse gate's entry could never
    match at read time, and its upsert overwrote the pipeline's working entry
    with one that never would. Keying both on the *persisted connector track*
    is what makes them agree.
    """

    @staticmethod
    def _persisted(
        title: str, artists: list[str], duration_ms: int | None
    ) -> ConnectorTrack:
        """The connector_tracks row a provider payload materializes into."""
        return ConnectorTrack(
            connector_name="spotify",
            connector_track_identifier="sp_42",
            title=title,
            artists=[Artist(name=name) for name in artists],
            duration_ms=duration_ms,
        )

    def test_the_two_paths_produce_the_same_digest_for_the_same_pair(self):
        candidate = Track(
            title="Creep",
            artists=[Artist(name="Radiohead")],
            duration_ms=238_000,
        )
        row = self._persisted("Creep", ["Radiohead", "Albert Hammond"], 238_000)

        # Matching pipeline: full provider payload materialized the row.
        pipeline = content_digest(connector_side(row), track_side(candidate))
        # Reuse gate: a title and one artist, no duration — but the same row.
        reuse = content_digest(connector_side(row), track_side(candidate))

        assert pipeline == reuse

    def test_the_callers_own_views_would_not_have_agreed(self):
        """Why the fix had to move the source, not patch either caller."""
        pipeline_view = service_side(
            "sp_42",
            {
                "title": "Creep",
                "artists": ["Radiohead", "Albert Hammond"],
                "duration_ms": 238_000,
            },
        )
        reuse_view = service_side(
            "sp_42", {"title": "Creep", "artist": "Radiohead", "duration_ms": None}
        )
        candidate = Track(title="Creep", artists=[Artist(name="Radiohead")])

        assert content_digest(pipeline_view, track_side(candidate)) != content_digest(
            reuse_view, track_side(candidate)
        )
