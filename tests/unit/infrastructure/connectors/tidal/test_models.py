"""Unit tests for the domain-facing Tidal conversions (``tidal/models.py``).

``models.py`` is the seam that keeps the ``oas_models`` isolation contract
honest: it turns wire-shaped JSON:API resources into thin domain-facing
values (``TidalTrack``, ``TidalCollectionItem``) so nothing outside the
connector package ever touches Tidal's wire format.

Fixtures are T2's PROVISIONAL spec-derived payloads (reused from
``test_oas_models``) — the v0.11.3 T7 probe replays live payloads and
corrects any shape the wire contradicts.

Spec facts under test:
- ``duration`` is an ISO-8601 duration STRING (``"PT2M58S"``) parsed to
  seconds by a small pure parser (PT#H#M#S forms and bare PT#S; malformed
  never raises, it reads as "unknown duration").
- ``isrc`` absent → ``None`` (unresolvable, not an error).
- ``addedAt`` lives in the collection item identifier's ``meta`` block.
"""

from datetime import UTC, datetime

import pytest

from src.infrastructure.connectors.tidal.models import (
    TidalCollectionItem,
    TidalTrack,
    collection_item_from_ref,
    parse_iso8601_duration_seconds,
    tidal_track_detail_from_document,
    tidal_track_from_resource,
)
from src.infrastructure.connectors.tidal.oas_models import (
    JsonApiDocument,
    TidalCollectionItemRef,
    TidalTrackResource,
)
from tests.fixtures import make_tidal_track_document, make_tidal_track_resource
from tests.unit.infrastructure.connectors.tidal.test_oas_models import (
    COLLECTION_ITEM,
    TRACK_WITH_ISRC,
    TRACK_WITHOUT_ISRC,
)


class TestIso8601DurationParser:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("PT2M58S", 178),
            ("PT1H2M3S", 3723),
            ("PT45S", 45),
            ("PT1H", 3600),
            ("PT3M", 180),
            ("PT0S", 0),
        ],
    )
    def test_valid_durations(self, value: str, expected: int):
        assert parse_iso8601_duration_seconds(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "PT",  # no components at all
            "P1DT2S",  # date part unsupported — tracks never span days
            "3:45",
            "178",
            "PTXS",
            "2M58S",  # missing PT prefix
            "PT2M58",  # trailing component without unit
        ],
    )
    def test_malformed_durations_read_as_none(self, value: str):
        assert parse_iso8601_duration_seconds(value) is None


class TestTrackConversion:
    def test_track_with_isrc_converts(self):
        resource = TidalTrackResource.model_validate(TRACK_WITH_ISRC)

        track = tidal_track_from_resource(resource)

        assert track == TidalTrack(
            id="12345",
            title="Kill Jill",
            isrc="QMJMT1701229",
            duration_seconds=178,
        )

    def test_absent_isrc_converts_to_none(self):
        resource = TidalTrackResource.model_validate(TRACK_WITHOUT_ISRC)

        track = tidal_track_from_resource(resource)

        assert track.isrc is None
        assert track.duration_seconds == 260  # PT4M20S

    def test_malformed_duration_converts_to_none_not_error(self):
        payload = dict(TRACK_WITH_ISRC)
        payload["attributes"] = {
            "title": "Kill Jill",
            "isrc": "QMJMT1701229",
            "duration": "garbage",
        }
        resource = TidalTrackResource.model_validate(payload)

        track = tidal_track_from_resource(resource)

        assert track.duration_seconds is None
        assert track.title == "Kill Jill"


class TestCollectionItemConversion:
    def test_added_at_comes_from_identifier_meta(self):
        ref = TidalCollectionItemRef.model_validate(COLLECTION_ITEM)

        item = collection_item_from_ref(ref)

        assert item == TidalCollectionItem(
            track_id="12345",
            added_at=datetime(2026, 8, 1, 12, 34, 56, 789000, tzinfo=UTC),
        )

    def test_missing_meta_reads_as_unknown_added_at(self):
        ref = TidalCollectionItemRef.model_validate({"id": "9", "type": "tracks"})

        item = collection_item_from_ref(ref)

        assert item.track_id == "9"
        assert item.added_at is None


class TestTrackDetailExtraction:
    """``tidal_track_detail_from_document`` — the per-id fetch's seam.

    Artist names ride ``included`` (side-loaded via ``include=artists``),
    ordered by the track's ``artists`` relationship linkage; the
    ``replacement`` relationship surfaces as ``replacement_id``.
    """

    def test_detail_carries_track_artists_and_no_replacement(self):
        document = make_tidal_track_document(
            track_id="12345", artists=("Main Artist", "Featured Artist")
        )

        detail = tidal_track_detail_from_document(document)

        assert detail is not None
        assert detail.track.id == "12345"
        assert detail.track.isrc == "USUM72309818"
        assert detail.artist_names == ("Main Artist", "Featured Artist")
        assert detail.replacement_id is None

    def test_replacement_pointer_surfaces_as_replacement_id(self):
        document = make_tidal_track_document(track_id="old101", replacement_id="new202")

        detail = tidal_track_detail_from_document(document)

        assert detail is not None
        assert detail.replacement_id == "new202"

    def test_empty_document_reads_as_none(self):
        assert (
            tidal_track_detail_from_document(
                JsonApiDocument[TidalTrackResource](data=None)
            )
            is None
        )

    def test_missing_included_yields_no_artist_names(self):
        document = JsonApiDocument[TidalTrackResource](
            data=make_tidal_track_resource(track_id="12345")
        )

        detail = tidal_track_detail_from_document(document)

        assert detail is not None
        assert detail.artist_names == ()
