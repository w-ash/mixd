"""Unit tests for the hand-written Tidal JSON:API boundary models.

PROVISIONAL: fixtures are derived from the vendored ``tidal-api-oas.json``
(schemas ``Tracks_Attributes``, ``Tracks_Resource_Object``, ``Links``,
``UserCollectionTracks_Items_Resource_Identifier``, ``Errors_Document``) and
their examples — not yet from live responses. The v0.11.3 T7 probe replays
real payloads through these models; shapes the probe contradicts get fixed
there, and these fixtures updated to the observed wire truth.

Spec facts the fixtures encode:
- ``duration`` is an ISO-8601 duration STRING (``"PT2M58S"``), not millis.
- ``addedAt`` lives in the collection item identifier's ``meta`` block.
- ``links.next`` is optional — absent means last page.
- ``replacement`` is a to-one relationship whose ``data`` may be null.
"""

from datetime import UTC, datetime

from src.infrastructure.connectors.tidal.oas_models import (
    CursorLinks,
    JsonApiDocument,
    TidalCollectionItemRef,
    TidalErrorDocument,
    TidalTrackResource,
)

# --- Fixtures derived from the vendored spec's examples -----------------

TRACK_WITH_ISRC: dict[str, object] = {
    "id": "12345",
    "type": "tracks",
    "attributes": {
        "title": "Kill Jill",
        "isrc": "QMJMT1701229",
        "duration": "PT2M58S",
        "explicit": False,
        "popularity": 0.56,
        "mediaTags": ["HIRES_LOSSLESS"],
    },
}

TRACK_WITHOUT_ISRC: dict[str, object] = {
    "id": "67890",
    "type": "tracks",
    "attributes": {
        "title": "Untagged Upload",
        "duration": "PT4M20S",
    },
}

TRACK_WITH_REPLACEMENT: dict[str, object] = {
    "id": "12345",
    "type": "tracks",
    "attributes": {
        "title": "Kill Jill",
        "isrc": "QMJMT1701229",
        "duration": "PT2M58S",
    },
    "relationships": {
        "replacement": {
            "data": {"id": "99999", "type": "tracks"},
            "links": {"self": "/tracks/12345/relationships/replacement"},
        },
    },
}

TRACK_WITH_NULL_REPLACEMENT: dict[str, object] = {
    "id": "12345",
    "type": "tracks",
    "attributes": {
        "title": "Kill Jill",
        "duration": "PT2M58S",
    },
    "relationships": {
        "replacement": {
            "data": None,
            "links": {"self": "/tracks/12345/relationships/replacement"},
        },
    },
}

COLLECTION_ITEM: dict[str, object] = {
    "id": "12345",
    "type": "tracks",
    "meta": {"addedAt": "2026-08-01T12:34:56.789Z"},
}

ERROR_DOCUMENT: dict[str, object] = {
    "errors": [
        {
            "id": "b3ad34cb",
            "status": "400",
            "code": "INVALID_QUERY_PARAMETER",
            "detail": "Invalid country code",
            "source": {"parameter": "countryCode"},
        },
    ],
}


# --- Track resource ------------------------------------------------------


def test_track_with_isrc_parses() -> None:
    track = TidalTrackResource.model_validate(TRACK_WITH_ISRC)
    assert track.id == "12345"
    assert track.type == "tracks"
    assert track.attributes.title == "Kill Jill"
    assert track.attributes.isrc == "QMJMT1701229"


def test_duration_is_iso8601_string() -> None:
    """The spec models duration as an ISO-8601 string, not milliseconds."""
    track = TidalTrackResource.model_validate(TRACK_WITH_ISRC)
    assert track.attributes.duration == "PT2M58S"


def test_track_without_isrc_parses_with_none() -> None:
    """Spec marks isrc required, but resolution treats it as optional —
    a missing code must surface as ``None``, never a validation error."""
    track = TidalTrackResource.model_validate(TRACK_WITHOUT_ISRC)
    assert track.attributes.isrc is None


def test_unconsumed_fields_are_ignored() -> None:
    """extra='ignore': spec fields we do not consume never break parsing."""
    track = TidalTrackResource.model_validate(TRACK_WITH_ISRC)
    assert not hasattr(track.attributes, "popularity")


# --- Replacement relationship -------------------------------------------


def test_replacement_present() -> None:
    track = TidalTrackResource.model_validate(TRACK_WITH_REPLACEMENT)
    assert track.relationships is not None
    replacement = track.relationships.replacement
    assert replacement is not None
    assert replacement.data is not None
    assert replacement.data.id == "99999"
    assert replacement.data.type == "tracks"


def test_replacement_null_data() -> None:
    """A live track: the relationship exists but points at nothing."""
    track = TidalTrackResource.model_validate(TRACK_WITH_NULL_REPLACEMENT)
    assert track.relationships is not None
    assert track.relationships.replacement is not None
    assert track.relationships.replacement.data is None


def test_replacement_absent() -> None:
    track = TidalTrackResource.model_validate(TRACK_WITH_ISRC)
    assert track.relationships is None or track.relationships.replacement is None


# --- Document envelope + cursor pagination ------------------------------


def test_document_with_next_cursor() -> None:
    document = JsonApiDocument[list[TidalTrackResource]].model_validate({
        "data": [TRACK_WITH_ISRC, TRACK_WITHOUT_ISRC],
        "links": {
            "self": "/tracks?filter[isrc]=QMJMT1701229",
            "next": "/tracks?filter[isrc]=QMJMT1701229&page[cursor]=zyx",
        },
    })
    assert document.data is not None
    assert len(document.data) == 2
    assert document.links is not None
    assert document.links.next == "/tracks?filter[isrc]=QMJMT1701229&page[cursor]=zyx"


def test_document_without_next_cursor_is_last_page() -> None:
    document = JsonApiDocument[list[TidalTrackResource]].model_validate({
        "data": [TRACK_WITH_ISRC],
        "links": {"self": "/tracks?filter[isrc]=QMJMT1701229"},
    })
    assert document.links is not None
    assert document.links.next is None


def test_document_included_resources_parse_generically() -> None:
    document = JsonApiDocument[list[TidalCollectionItemRef]].model_validate({
        "data": [COLLECTION_ITEM],
        "included": [TRACK_WITH_ISRC],
        "links": {"self": "/userCollections/me/relationships/tracks"},
    })
    assert len(document.included) == 1
    assert document.included[0].id == "12345"


def test_cursor_links_standalone() -> None:
    links = CursorLinks.model_validate({
        "self": "/tracks",
        "next": "/tracks?page[cursor]=abc",
    })
    assert links.self_url == "/tracks"
    assert links.next == "/tracks?page[cursor]=abc"


# --- Collection items (favorites) ---------------------------------------


def test_collection_item_added_at_in_meta() -> None:
    """``addedAt`` lives in the item identifier's meta block, tz-aware."""
    item = TidalCollectionItemRef.model_validate(COLLECTION_ITEM)
    assert item.meta is not None
    assert item.meta.added_at == datetime(2026, 8, 1, 12, 34, 56, 789000, tzinfo=UTC)


def test_collection_item_without_meta_parses() -> None:
    item = TidalCollectionItemRef.model_validate({"id": "1", "type": "tracks"})
    assert item.meta is None


# --- Error document ------------------------------------------------------


def test_error_document_parses() -> None:
    document = TidalErrorDocument.model_validate(ERROR_DOCUMENT)
    assert len(document.errors) == 1
    error = document.errors[0]
    assert error.status == "400"
    assert error.code == "INVALID_QUERY_PARAMETER"
    assert error.detail == "Invalid country code"
    assert error.source is not None
    assert error.source.parameter == "countryCode"


def test_error_document_empty_errors() -> None:
    """A degenerate error body still parses instead of raising."""
    document = TidalErrorDocument.model_validate({})
    assert document.errors == []
