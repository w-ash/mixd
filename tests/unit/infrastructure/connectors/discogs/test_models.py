"""Discogs Pydantic models parse fixture shapes reconciled to a live probe.

Payloads here are REDACTED versions of a real, token-authenticated 2026-08-22
probe (identity, an empty collection page, release 249504, master 96559):
same structure and key set as the wire, values swapped for stable test data.
``release_payload``/``master_payload`` additionally carry the exact
"Never Gonna Give You Up" release/master shape (public catalog data, kept
verbatim) to pin the real field set, including the unconsumed tracklist
``type_`` key. ``empty_collection_page_payload`` is the real empty-collection
capture verbatim — ``items: 0``, ``pages: 1``, ``urls: {}``.
"""

from src.infrastructure.connectors.discogs.models import (
    DiscogsCollectionPage,
    DiscogsIdentity,
    DiscogsMaster,
    DiscogsPagination,
    DiscogsRelease,
)


def collection_release_payload(
    release_id: int = 1477251, instance_id: int = 1000001
) -> dict[str, object]:
    return {
        "id": release_id,
        "instance_id": instance_id,
        "date_added": "2024-04-15T08:00:00-07:00",
        "rating": 4,
        "basic_information": {
            "id": release_id,
            "title": "Discovery",
            "year": 2001,
            "artists": [
                {"name": "Daft Punk", "anv": "", "join": ""},
            ],
            "labels": [{"name": "Virgin", "catno": "7243 8 49606 1 5"}],
            "formats": [{"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Album"]}],
        },
    }


def collection_page_payload(
    page: int = 1, pages: int = 1, items: int = 1, per_page: int = 100
) -> dict[str, object]:
    urls: dict[str, object] = (
        {"next": f"https://api.discogs.com/x?page={page + 1}"} if page < pages else {}
    )
    return {
        "pagination": {
            "page": page,
            "pages": pages,
            "per_page": per_page,
            "items": items,
            "urls": urls,
        },
        "releases": [collection_release_payload()],
    }


def empty_collection_page_payload() -> dict[str, object]:
    """Verbatim real capture: an authenticated user with zero collection items."""
    return {
        "pagination": {"page": 1, "pages": 1, "per_page": 3, "items": 0, "urls": {}},
        "releases": [],
    }


def release_payload(release_id: int = 1477251) -> dict[str, object]:
    return {
        "id": release_id,
        "title": "Discovery",
        "year": 2001,
        "artists": [
            {"name": "Daft Punk", "anv": "Daft Punk (2)", "join": "&"},
        ],
        "labels": [{"name": "Virgin", "catno": "7243 8 49606 1 5"}],
        "formats": [{"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Album"]}],
        "genres": ["Electronic"],
        "styles": ["House", "Disco"],
        "tracklist": [
            {"position": "A1", "title": "One More Time", "duration": "5:20"},
            {"position": "A2", "title": "Aerodynamic", "duration": ""},
        ],
    }


def real_release_payload() -> dict[str, object]:
    """Real captured shape (release 249504) — public catalog data, kept verbatim.

    Confirms the tracklist ``type_`` key (literal trailing underscore on the
    wire) and single-letter, non-numbered ``position`` values ("A", "B").
    """
    return {
        "id": 249504,
        "status": "Accepted",
        "year": 1987,
        "resource_url": "https://api.discogs.com/releases/249504",
        "artists": [{"name": "Rick Astley", "anv": "", "join": "", "id": 72872}],
        "labels": [{"name": "RCA", "catno": "PB 41447", "id": 895}],
        "formats": [
            {
                "name": "Vinyl",
                "qty": "1",
                "descriptions": ['7"', "45 RPM", "Single", "Stereo"],
            }
        ],
        "data_quality": "Correct",
        "title": "Never Gonna Give You Up",
        "country": "UK",
        "genres": ["Electronic", "Pop"],
        "styles": ["Euro-Disco"],
        "tracklist": [
            {
                "position": "A",
                "type_": "track",
                "title": "Never Gonna Give You Up",
                "duration": "3:32",
            },
            {
                "position": "B",
                "type_": "track",
                "title": "Never Gonna Give You Up (Instrumental)",
                "duration": "3:30",
            },
        ],
    }


def real_master_payload() -> dict[str, object]:
    """Real captured shape (master 96559) — public catalog data, kept verbatim."""
    return {
        "id": 96559,
        "main_release": 249504,
        "most_recent_release": 3341754,
        "resource_url": "https://api.discogs.com/masters/96559",
        "genres": ["Electronic", "Pop"],
        "styles": ["Euro-Disco"],
        "year": 1987,
        "title": "Never Gonna Give You Up",
        "data_quality": "Correct",
    }


class TestIdentity:
    def test_parses_consumed_fields_and_ignores_extras(self):
        identity = DiscogsIdentity.model_validate({
            "id": 12345,
            "username": "example",
            "resource_url": "https://api.discogs.com/users/example",
            "consumer_name": "Mixd",
        })

        assert identity.id == 12345
        assert identity.username == "example"

    def test_real_identity_capture_shape_parses(self):
        # Redacted real capture: /oauth/identity returns exactly these four
        # keys — id, username, resource_url, consumer_name.
        identity = DiscogsIdentity.model_validate({
            "id": 32007498,
            "username": "example-user",
            "resource_url": "https://api.discogs.com/users/example-user",
            "consumer_name": "example-user",
        })

        assert identity.id == 32007498
        assert identity.username == "example-user"


class TestPagination:
    def test_urls_next_is_optional(self):
        pagination = DiscogsPagination.model_validate({
            "page": 3,
            "pages": 3,
            "per_page": 100,
            "items": 250,
            "urls": {},
        })

        assert pagination.page == 3
        assert pagination.pages == 3
        assert pagination.per_page == 100
        assert pagination.items == 250
        assert pagination.urls.next is None

    def test_urls_next_parses_when_present(self):
        pagination = DiscogsPagination.model_validate({
            "page": 1,
            "pages": 2,
            "per_page": 100,
            "items": 150,
            "urls": {"next": "https://api.discogs.com/x?page=2"},
        })

        assert pagination.urls.next == "https://api.discogs.com/x?page=2"


class TestCollectionPage:
    def test_parses_release_with_basic_information(self):
        page = DiscogsCollectionPage.model_validate(collection_page_payload())

        release = page.releases[0]
        assert release.id == 1477251
        assert release.instance_id == 1000001
        assert release.date_added.startswith("2024-04-15")
        info = release.basic_information
        assert info is not None
        assert info.title == "Discovery"
        assert info.year == 2001
        assert info.artists[0].name == "Daft Punk"
        assert info.labels[0].catno == "7243 8 49606 1 5"
        assert info.formats[0].name == "Vinyl"
        assert info.formats[0].descriptions == ["LP", "Album"]

    def test_item_without_basic_information_still_parses(self):
        # basic_information is observed on every real capture but not owed to
        # us — one item missing it must not fail the whole page's validation
        # (display consumers skip it; the pagination total stays usable).
        payload = collection_page_payload()
        del payload["releases"][0]["basic_information"]

        page = DiscogsCollectionPage.model_validate(payload)

        assert page.releases[0].basic_information is None
        assert page.pagination.items == payload["pagination"]["items"]

    def test_real_empty_collection_capture_parses_clean(self):
        page = DiscogsCollectionPage.model_validate(empty_collection_page_payload())

        assert page.pagination.page == 1
        assert page.pagination.pages == 1
        assert page.pagination.per_page == 3
        assert page.pagination.items == 0
        assert page.pagination.urls.next is None
        assert page.releases == []


class TestRelease:
    def test_parses_anv_and_join_on_artists(self):
        release = DiscogsRelease.model_validate(release_payload())

        artist = release.artists[0]
        assert artist.name == "Daft Punk"
        assert artist.anv == "Daft Punk (2)"
        assert artist.join == "&"

    def test_blank_track_duration_parses_as_empty_string(self):
        release = DiscogsRelease.model_validate(release_payload())

        assert release.tracklist[0].duration == "5:20"
        assert release.tracklist[1].duration == ""
        assert release.tracklist[1].position == "A2"

    def test_genres_and_styles_parse(self):
        release = DiscogsRelease.model_validate(release_payload())

        assert release.genres == ["Electronic"]
        assert release.styles == ["House", "Disco"]

    def test_real_release_capture_parses_and_ignores_type_key(self):
        release = DiscogsRelease.model_validate(real_release_payload())

        assert release.id == 249504
        assert release.title == "Never Gonna Give You Up"
        assert release.year == 1987
        assert release.genres == ["Electronic", "Pop"]
        assert release.formats[0].qty == "1"
        assert release.artists[0].name == "Rick Astley"
        assert release.labels[0].name == "RCA"
        # Single-letter, non-numbered position — a 7" single, not an LP side.
        assert release.tracklist[0].position == "A"
        assert release.tracklist[0].duration == "3:32"
        assert release.tracklist[1].position == "B"
        # The wire's "type_" key is real but unconsumed — model has no such
        # field; extra="ignore" must not choke on it.
        assert not hasattr(release.tracklist[0], "type_")


class TestMaster:
    def test_parses_main_release(self):
        master = DiscogsMaster.model_validate({
            "id": 4422,
            "title": "Discovery",
            "year": 2001,
            "main_release": 1477251,
            "most_recent_release": 999,
        })

        assert master.id == 4422
        assert master.title == "Discovery"
        assert master.year == 2001
        assert master.main_release == 1477251

    def test_real_master_capture_parses_clean(self):
        master = DiscogsMaster.model_validate(real_master_payload())

        assert master.id == 96559
        assert master.title == "Never Gonna Give You Up"
        assert master.year == 1987
        assert master.main_release == 249504
