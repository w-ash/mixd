"""The artist planner and the pure halves of the import-path minter.

Pure: owner probes are handed in as maps and outcomes asserted directly.
``plan_artist_resolution`` is exercised against the real connector-id
pricing; ``credited_artists`` and ``artist_writes`` against payloads shaped
like the connectors' ``raw_metadata``.
"""

from datetime import UTC, datetime
from uuid import uuid7

from src.config import create_matching_config
from src.domain.entities import ArtistCredit, Track
from src.domain.entities.artist import Artist, ConnectorArtist
from src.domain.matching.artist_resolution import (
    ArtistCreditSource,
    ArtistDescription,
    ArtistResolutionRules,
    artist_identity_key,
    artist_writes,
    credit_source,
    credited_artists,
    dumped_credits,
    plan_artist_resolution,
)
from src.domain.matching.canonical_resolution import Described
from tests.fixtures import TEST_USER_ID

CONFIG = create_matching_config()
NOW = datetime(2026, 9, 19, tzinfo=UTC)


def _described(
    identifier: str, name: str, strong_id: str
) -> Described[str, ArtistDescription]:
    return Described(
        key=identifier,
        description=ArtistDescription(name=name),
        strong_id=strong_id,
        name_key=None,
    )


def _stored(connector: str, *identifiers: str) -> dict[str, ConnectorArtist]:
    return {
        identifier: ConnectorArtist(
            connector_name=connector,
            connector_artist_identifier=identifier,
            name=identifier.title(),
        )
        for identifier in identifiers
    }


class TestRules:
    def test_identity_key_normalizes_the_name(self):
        assert artist_identity_key(ArtistDescription(name="The Beatles")) == "beatles"

    def test_a_name_never_matches_on_the_import_path(self):
        rules = ArtistResolutionRules(CONFIG)
        assert (
            rules.same(
                ArtistDescription(name="Justice"), ArtistDescription(name="Justice")
            )
            is None
        )

    def test_a_strong_match_is_never_suspect_and_is_priced_at_the_id_tier(self):
        rules = ArtistResolutionRules(CONFIG)
        evidence, suspect = rules.strong_match(
            ArtistDescription(name="Ye"), ArtistDescription(name="Kanye West")
        )
        assert suspect is False
        assert evidence.method == "direct"
        assert evidence.zone == "accept"
        assert evidence.evidence is not None
        assert evidence.evidence["level"] == "connector_id"
        assert (
            rules.creation(ArtistDescription(name="Ye")).confidence
            == evidence.confidence
        )


class TestPlan:
    def test_a_strong_owner_is_reused(self):
        owner = Artist(name="Caribou", user_id=TEST_USER_ID)
        plan = plan_artist_resolution(
            [_described("sp-1", "Caribou", "row-1")],
            strong_owners={"row-1": owner},
            config=CONFIG,
        )
        outcome = plan["sp-1"]
        assert outcome.kind == "reuse"
        assert outcome.canonical is owner

    def test_a_strong_id_without_an_owner_is_created_with_the_id(self):
        plan = plan_artist_resolution(
            [_described("sp-1", "Caribou", "row-1")], strong_owners={}, config=CONFIG
        )
        outcome = plan["sp-1"]
        assert outcome.kind == "create"
        assert outcome.strong_id == "row-1"
        assert outcome.refusal is None

    def test_same_name_different_ids_are_two_creations(self):
        plan = plan_artist_resolution(
            [
                _described("sp-1", "Justice", "row-1"),
                _described("sp-2", "Justice", "row-2"),
            ],
            strong_owners={},
            config=CONFIG,
        )
        assert {outcome.kind for outcome in plan.values()} == {"create"}


class TestCreditedArtists:
    def test_spotify_shaped_payload_yields_records_and_claims(self):
        source = credit_source(
            "t1",
            [
                ArtistCredit(credited_name="Caribou"),
                ArtistCredit(credited_name="Koushik"),
            ],
            {
                "artist_ids": ["sp-1", "sp-2"],
                "artists": [
                    {"id": "sp-1", "name": "Caribou", "uri": "spotify:artist:sp-1"},
                    {"id": "sp-2", "name": "Koushik"},
                ],
            },
        )
        intake = credited_artists("spotify", [source])

        assert [a.connector_artist_identifier for a in intake.connector_artists] == [
            "sp-1",
            "sp-2",
        ]
        assert intake.connector_artists[0].raw_metadata == {
            "id": "sp-1",
            "name": "Caribou",
            "uri": "spotify:artist:sp-1",
        }
        assert [(c.key, c.credited_name, c.identifier) for c in intake.claims] == [
            ("t1", "Caribou", "sp-1"),
            ("t1", "Koushik", "sp-2"),
        ]

    def test_none_ids_and_various_artists_are_skipped(self):
        source = credit_source(
            "t1",
            [
                ArtistCredit(credited_name="Various Artists"),
                ArtistCredit(credited_name="Tycho"),
            ],
            {"artist_ids": [None, "sp-9"]},
        )
        apple = credit_source(
            "t2", [ArtistCredit(credited_name="Tycho")], {"artist_ids": [None]}
        )
        intake = credited_artists("spotify", [source, apple])

        assert [a.connector_artist_identifier for a in intake.connector_artists] == [
            "sp-9"
        ]
        assert [c.key for c in intake.claims] == ["t1"]

    def test_a_various_artists_id_is_never_a_record(self):
        source = credit_source(
            "t1", [ArtistCredit(credited_name="Various")], {"artist_ids": ["sp-va"]}
        )
        assert credited_artists("spotify", [source]).connector_artists == ()

    def test_a_name_keyed_connector_writes_records_but_no_claims(self):
        source = ArtistCreditSource(
            key="a::b",
            artists=[ArtistCredit(credited_name="Bonobo")],
            artist_ids=["Bonobo"],
        )
        intake = credited_artists("lastfm", [source])

        assert intake.connector_artists[0].connector_artist_identifier == "Bonobo"
        assert intake.claims == ()
        assert intake.described({"Bonobo": intake.connector_artists[0]}) == []

    def test_a_misaligned_dump_is_not_attached(self):
        source = credit_source(
            "t1",
            [ArtistCredit(credited_name="Caribou")],
            {"artist_ids": ["sp-1"], "artists": [{"id": "sp-other", "name": "X"}]},
        )
        assert (
            credited_artists("spotify", [source]).connector_artists[0].raw_metadata
            == {}
        )

    def test_dumped_credits_read_names_out_of_a_spotify_dump(self):
        assert dumped_credits({
            "artists": [{"id": "sp-1", "name": "Caribou"}, {"id": "x"}]
        }) == (ArtistCredit(credited_name="Caribou"),)
        assert dumped_credits({"artists": "nope"}) == ()

    def test_described_files_each_identifier_under_its_stored_row_id(self):
        source = credit_source(
            "t1",
            [
                ArtistCredit(credited_name="Caribou"),
                ArtistCredit(credited_name="Caribou"),
            ],
            {"artist_ids": ["sp-1", "sp-1"]},
        )
        intake = credited_artists("spotify", [source])
        stored = _stored("spotify", "sp-1")

        (item,) = intake.described(stored)
        assert item.key == "sp-1"
        assert item.strong_id == str(stored["sp-1"].id)
        assert item.name_key is None


class TestArtistWrites:
    def _intake_and_stored(self):
        source = credit_source(
            "t1",
            [
                ArtistCredit(credited_name="Caribou"),
                ArtistCredit(credited_name="Koushik"),
            ],
            {"artist_ids": ["sp-1", "sp-2"]},
        )
        return credited_artists("spotify", [source]), _stored("spotify", "sp-1", "sp-2")

    def test_creations_write_artists_mappings_primaries_and_credit_fills(self):
        intake, stored = self._intake_and_stored()
        canonical = Track(
            title="Odessa",
            artists=[
                ArtistCredit(credited_name="Caribou"),
                ArtistCredit(credited_name="Koushik"),
            ],
            user_id=TEST_USER_ID,
        )
        plan = plan_artist_resolution(
            intake.described(stored), strong_owners={}, config=CONFIG
        )

        writes = artist_writes(
            plan,
            intake,
            stored,
            {"t1": canonical},
            connector="spotify",
            user_id=TEST_USER_ID,
            now=NOW,
        )

        assert [a.name for a in writes.artists] == ["Caribou", "Koushik"]
        caribou, koushik = writes.artists
        (row_1, row_2) = writes.mapping_rows
        assert row_1["artist_id"] == caribou.id
        assert row_1["connector_artist_id"] == stored["sp-1"].id
        assert row_1["match_method"] == "direct"
        assert row_1["is_primary"] is True
        assert row_1["last_seen_at"] == NOW
        assert row_2["artist_id"] == koushik.id
        assert [p.owner_id for p in writes.primaries] == [caribou.id, koushik.id]
        assert writes.assignments == (
            (canonical.id, 0, caribou.id),
            (canonical.id, 1, koushik.id),
        )
        assert writes.reused == ()

    def test_a_reuse_asserts_nothing_and_is_touched(self):
        intake, stored = self._intake_and_stored()
        owner = Artist(name="Caribou", user_id=TEST_USER_ID)
        canonical = Track(
            title="Odessa",
            artists=[ArtistCredit(credited_name="Caribou")],
            user_id=TEST_USER_ID,
        )
        plan = plan_artist_resolution(
            intake.described(stored),
            strong_owners={str(stored["sp-1"].id): owner},
            config=CONFIG,
        )

        writes = artist_writes(
            plan,
            intake,
            stored,
            {"t1": canonical},
            connector="spotify",
            user_id=TEST_USER_ID,
            now=NOW,
        )

        assert [a.name for a in writes.artists] == ["Koushik"]
        assert len(writes.mapping_rows) == 1
        assert writes.reused == (owner.id,)
        # Caribou's credit fills from the owner; Koushik has no credit on
        # this canonical's line-up, so nothing is invented for it.
        assert writes.assignments == ((canonical.id, 0, owner.id),)

    def test_credits_fill_by_name_not_position_and_never_overwrite(self):
        intake, stored = self._intake_and_stored()
        held = uuid7()
        canonical = Track(
            title="Odessa",
            artists=[
                ArtistCredit(credited_name="Koushik"),
                ArtistCredit(credited_name="Caribou", artist_id=held),
            ],
            user_id=TEST_USER_ID,
        )
        plan = plan_artist_resolution(
            intake.described(stored), strong_owners={}, config=CONFIG
        )

        writes = artist_writes(
            plan,
            intake,
            stored,
            {"t1": canonical},
            connector="spotify",
            user_id=TEST_USER_ID,
            now=NOW,
        )

        koushik = next(a for a in writes.artists if a.name == "Koushik")
        assert writes.assignments == ((canonical.id, 0, koushik.id),)

    def test_a_payload_without_a_canonical_fills_nothing(self):
        intake, stored = self._intake_and_stored()
        plan = plan_artist_resolution(
            intake.described(stored), strong_owners={}, config=CONFIG
        )

        writes = artist_writes(
            plan, intake, stored, {}, connector="spotify", user_id=TEST_USER_ID, now=NOW
        )

        assert len(writes.artists) == 2
        assert writes.assignments == ()
