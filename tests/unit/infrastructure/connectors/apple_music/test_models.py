"""Unit tests for Apple Music JSON:API Pydantic models.

Parses REDACTED live captures (probe 2026-08-22, ``fixtures/``) into the
typed models: catalog song resources, the 1:N ISRC envelope (one ISRC → many
releases), the ISRC miss (200 with empty data — not an error), storefront,
recently-played page with its ``next`` cursor, and the verbatim error
envelopes (403 invalid MUT, 400 limit-over-max). Redaction: artwork URLs are
placeholders and ``previews`` is dropped; all structural keys are real.

Two payloads remain HAND-MADE because reality did not produce them: a song
without ISRC/releaseDate (every probed catalog song carried both), and a
``playParams.catalogId`` divergence (catalog and recent-played playParams are
``{id, kind}`` only — the divergence path stays dormant until v0.13 library
objects).
"""

import json
from pathlib import Path

from src.infrastructure.connectors.apple_music.models import (
    AppleMusicError,
    AppleMusicErrorResponse,
    AppleMusicRecentlyPlayedResponse,
    AppleMusicSong,
    AppleMusicSongsResponse,
    AppleMusicStorefrontResponse,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, object]:
    """A redacted live capture from the fixtures directory."""
    payload = json.loads((FIXTURES / name).read_text())
    assert isinstance(payload, dict)
    return payload


def make_song_payload(**attribute_overrides: object) -> dict[str, object]:
    """HAND-MADE song resource for shapes the live probe did not produce.

    Mirrors the verified live shape (catalog playParams = ``{id, kind}``);
    overrides carve out the unobserved variants (no ISRC, catalogId).
    """
    attributes: dict[str, object] = {
        "name": "Vampire",
        "artistName": "Olivia Rodrigo",
        "albumName": "GUTS",
        "durationInMillis": 219724,
        "isrc": "USUM72309818",
        "releaseDate": "2023-06-30",
        "playParams": {"id": "1613600188", "kind": "song"},
    }
    attributes.update(attribute_overrides)
    return {
        "id": "1613600188",
        "type": "songs",
        "href": "/v1/catalog/us/songs/1613600188",
        "attributes": attributes,
    }


class TestSongResource:
    def test_live_catalog_song_parses_all_consumed_fields(self):
        payload = load_fixture("songs_by_isrc_hit.json")
        response = AppleMusicSongsResponse.model_validate(payload)

        song = response.data[0]
        assert song.id == "6781076960"
        assert song.type == "songs"
        assert song.attributes.name == "Bohemian Rhapsody"
        assert song.attributes.artist_name == "Queen"
        assert song.attributes.album_name == "Greatest Hits In Japan"
        assert song.attributes.duration_in_millis == 355145
        assert song.attributes.isrc == "GBUM71029604"
        assert song.attributes.release_date == "1975-10-31"
        # Catalog playParams carry {id, kind} only — no catalogId (verified).
        assert song.attributes.play_params is not None
        assert song.attributes.play_params.id == song.id
        assert song.attributes.play_params.catalog_id is None

    def test_live_extra_fields_are_ignored(self):
        # Real resources carry artwork, genreNames, hasLyrics, relationships,
        # etc. — extra="ignore" must swallow them all.
        payload = load_fixture("songs_by_ids.json")

        response = AppleMusicSongsResponse.model_validate(payload)

        assert len(response.data) == 1
        assert response.data[0].attributes.name == "Bohemian Rhapsody"
        assert not hasattr(response.data[0].attributes, "genre_names")

    def test_song_without_isrc_or_release_date_defaults_to_none(self):
        # HAND-MADE: every live-probed catalog song carried isrc +
        # releaseDate; the defensive defaults still need cover.
        payload = make_song_payload()
        attributes = payload["attributes"]
        assert isinstance(attributes, dict)
        del attributes["isrc"]
        del attributes["releaseDate"]

        song = AppleMusicSong.model_validate(payload)

        assert song.attributes.isrc is None
        assert song.attributes.release_date is None

    def test_play_params_catalog_id_can_differ_from_resource_id(self):
        # HAND-MADE: library songs report the catalog identity via
        # playParams.catalogId. Dormant until v0.13 library objects — no
        # probed catalog/recent-played resource carried catalogId.
        payload = make_song_payload(
            playParams={"id": "i.library123", "kind": "song", "catalogId": "999888777"}
        )

        song = AppleMusicSong.model_validate(payload)

        assert song.attributes.play_params is not None
        assert song.attributes.play_params.catalog_id == "999888777"
        assert song.attributes.play_params.catalog_id != song.id

    def test_missing_play_params_defaults_to_none(self):
        payload = make_song_payload()
        attributes = payload["attributes"]
        assert isinstance(attributes, dict)
        del attributes["playParams"]

        song = AppleMusicSong.model_validate(payload)

        assert song.attributes.play_params is None


class TestEnvelopes:
    def test_storefront_response_parses_id(self):
        response = AppleMusicStorefrontResponse.model_validate(
            load_fixture("storefront.json")
        )

        assert len(response.data) == 1
        assert response.data[0].id == "us"

    def test_one_isrc_fans_out_to_many_songs(self):
        # Live: ONE ISRC returned 43 songs (many releases of one recording);
        # the fixture is trimmed to 3. Correlate by attributes.isrc, never
        # by position.
        response = AppleMusicSongsResponse.model_validate(
            load_fixture("songs_by_isrc_hit.json")
        )

        assert len(response.data) == 3
        assert {song.attributes.isrc for song in response.data} == {"GBUM71029604"}
        assert len({song.id for song in response.data}) == 3
        assert response.next is None

    def test_isrc_miss_is_empty_data_not_an_error(self):
        response = AppleMusicSongsResponse.model_validate(
            load_fixture("songs_by_isrc_miss.json")
        )

        assert response.data == []

    def test_equivalents_envelope_parses(self):
        response = AppleMusicSongsResponse.model_validate(
            load_fixture("songs_equivalents.json")
        )

        assert [song.id for song in response.data] == ["6781076960"]

    def test_recently_played_page_with_next_cursor(self):
        # Live page: 30 items with offset-based next; fixture trimmed to 3.
        page = AppleMusicRecentlyPlayedResponse.model_validate(
            load_fixture("recently_played_page.json")
        )

        assert len(page.data) == 3
        assert page.next == "/v1/me/recent/played/tracks?offset=30&types=songs"
        assert all(song.attributes.play_params is not None for song in page.data)


class TestErrorEnvelope:
    def test_verbatim_invalid_mut_403_body_parses(self):
        envelope = AppleMusicErrorResponse.model_validate(
            load_fixture("error_403_invalid_mut.json")
        )

        assert len(envelope.errors) == 1
        first = envelope.errors[0]
        assert first.code == "40300"
        assert first.status == "403"
        assert first.title == "Forbidden"
        assert first.detail == "Invalid authentication"

    def test_verbatim_limit_over_max_400_body_parses(self):
        envelope = AppleMusicErrorResponse.model_validate(
            load_fixture("error_400_limit_over_max.json")
        )

        first = envelope.errors[0]
        assert first.code == "40005"
        assert first.status == "400"
        assert first.detail is not None
        assert "less than or equal to 30" in first.detail

    def test_error_fields_are_all_optional(self):
        error = AppleMusicError.model_validate({})

        assert error.code is None
        assert error.status is None
        assert error.title is None
        assert error.detail is None
