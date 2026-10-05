"""Unit tests for the hand-written Tidal JSON:API boundary models.

PROVISIONAL: fixtures are derived from the vendored ``tidal-api-oas.json``
(schemas ``Tracks_Attributes``, ``Tracks_Resource_Object``,
``UserCollectionTracks_Items_Resource_Identifier``) and their examples — not
yet from live responses. The v0.11.3 T7 probe replays real payloads through
these models; shapes the probe contradicts get fixed there, and these
fixtures updated to the observed wire truth.

The track and collection-item fixtures are the payloads ``test_models.py``
converts; that conversion (and the client's pagination and ``get_track``
tests) is what pins the ordinary parse. The cases here are the wire shapes no
other test reaches: a ``replacement`` relationship whose ``data`` is null, and
a heterogeneous ``included`` array.
"""

from src.infrastructure.connectors.tidal.oas_models import (
    JsonApiDocument,
    TidalCollectionItemRef,
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


def test_replacement_null_data() -> None:
    """A live track: the relationship exists but points at nothing."""
    track = TidalTrackResource.model_validate(TRACK_WITH_NULL_REPLACEMENT)
    assert track.relationships is not None
    assert track.relationships.replacement is not None
    assert track.relationships.replacement.data is None


def test_document_included_resources_parse_generically() -> None:
    document = JsonApiDocument[list[TidalCollectionItemRef]].model_validate({
        "data": [COLLECTION_ITEM],
        "included": [TRACK_WITH_ISRC],
        "links": {"self": "/userCollections/me/relationships/tracks"},
    })
    assert len(document.included) == 1
    assert document.included[0].id == "12345"
