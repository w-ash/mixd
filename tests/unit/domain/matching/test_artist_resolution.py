"""The artist planner and the pure halves of the import-path minter.

Pure: owner probes are handed in as maps and outcomes asserted directly.
``plan_artist_resolution`` is exercised against the real connector-id
pricing; ``credited_artists`` and ``artist_writes`` against payloads shaped
like the connectors' ``raw_metadata``.
"""

from datetime import UTC, datetime
from uuid import uuid7

from src.config import create_matching_config
from src.domain.entities import ArtistCredit, ConnectorArtistCredit, Track
from src.domain.entities.artist import Artist, ConnectorArtist
from src.domain.matching.artist_resolution import (
    ArtistCreditSource,
    ArtistDescription,
    ArtistResolutionRules,
    artist_identity_key,
    artist_writes,
    credited_artists,
    plan_artist_resolution,
)
from src.domain.matching.canonical_resolution import Described
from src.domain.repositories.mapping import PrimaryCandidate
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


def _credit(name: str, identifier: str | None) -> ConnectorArtistCredit:
    return ConnectorArtistCredit(
        credited_name=name, connector_artist_identifier=identifier
    )


class TestCreditedArtists:
    def test_spotify_shaped_payload_yields_one_claim_per_credited_id(self):
        source = ArtistCreditSource(
            "t1", [_credit("Caribou", "sp-1"), _credit("Koushik", "sp-2")]
        )
        intake = credited_artists("spotify", [source])

        assert [(c.key, c.credited_name, c.identifier) for c in intake.claims] == [
            ("t1", "Caribou", "sp-1"),
            ("t1", "Koushik", "sp-2"),
        ]
        assert intake.identifiers == ["sp-1", "sp-2"]
        assert intake.names == {"sp-1": "Caribou", "sp-2": "Koushik"}

    def test_none_ids_and_various_artists_are_skipped(self):
        source = ArtistCreditSource(
            "t1", [_credit("Various Artists", None), _credit("Tycho", "sp-9")]
        )
        apple = ArtistCreditSource("t2", [_credit("Tycho", None)])
        intake = credited_artists("spotify", [source, apple])

        assert [(c.key, c.identifier) for c in intake.claims] == [("t1", "sp-9")]

    def test_a_various_artists_id_is_never_a_claim(self):
        source = ArtistCreditSource("t1", [_credit("Various", "sp-va")])
        assert credited_artists("spotify", [source]).claims == ()

    def test_a_name_keyed_connector_claims_nothing(self):
        source = ArtistCreditSource("a::b", [_credit("Bonobo", "Bonobo")])
        intake = credited_artists("lastfm", [source])

        assert intake.claims == ()
        assert intake.described(_stored("lastfm", "Bonobo")) == []

    def test_the_first_sighting_names_a_repeated_identifier(self):
        source = ArtistCreditSource(
            "t1", [_credit("Caribou", "sp-1"), _credit("caribou", "sp-1")]
        )
        intake = credited_artists("spotify", [source])

        assert intake.identifiers == ["sp-1"]
        assert intake.names == {"sp-1": "Caribou"}

    def test_described_files_each_identifier_under_its_stored_row_id(self):
        source = ArtistCreditSource(
            "t1", [_credit("Caribou", "sp-1"), _credit("Caribou", "sp-1")]
        )
        intake = credited_artists("spotify", [source])
        stored = _stored("spotify", "sp-1")

        (item,) = intake.described(stored)
        assert item.key == "sp-1"
        assert item.description == ArtistDescription(name="Caribou")
        assert item.strong_id == str(stored["sp-1"].id)
        assert item.name_key is None

    def test_an_identifier_without_a_stored_row_is_not_described(self):
        source = ArtistCreditSource("t1", [_credit("Caribou", "sp-1")])
        intake = credited_artists("spotify", [source])

        assert intake.described({}) == []


class TestArtistWrites:
    def _intake_and_stored(self):
        source = ArtistCreditSource(
            "t1", [_credit("Caribou", "sp-1"), _credit("Koushik", "sp-2")]
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
        assert {a.user_id for a in writes.artists} == {TEST_USER_ID}
        # A connector artist id is identity-grade: ln(0.99/0.0001) = 9.2003,
        # which the sigmoid maps to 100.
        assert writes.mapping_rows == (
            {
                "user_id": TEST_USER_ID,
                "artist_id": caribou.id,
                "connector_artist_id": stored["sp-1"].id,
                "connector_name": "spotify",
                "match_method": "direct",
                "confidence": 100,
                "confidence_evidence": {
                    "level": "connector_id",
                    "final_score": 100,
                    "match_weight": 9.2003,
                },
                "origin": "automatic",
                "is_primary": True,
                "last_seen_at": NOW,
            },
            {
                "user_id": TEST_USER_ID,
                "artist_id": koushik.id,
                "connector_artist_id": stored["sp-2"].id,
                "connector_name": "spotify",
                "match_method": "direct",
                "confidence": 100,
                "confidence_evidence": {
                    "level": "connector_id",
                    "final_score": 100,
                    "match_weight": 9.2003,
                },
                "origin": "automatic",
                "is_primary": True,
                "last_seen_at": NOW,
            },
        )
        assert writes.primaries == (
            PrimaryCandidate(caribou.id, "spotify", stored["sp-1"].id),
            PrimaryCandidate(koushik.id, "spotify", stored["sp-2"].id),
        )
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
