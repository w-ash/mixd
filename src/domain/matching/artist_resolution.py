"""Is a credited artist an existing canonical artist, or a new one?

The artist instantiation of ``plan_resolution``, plus the pure halves of
the import-path minter around it: reading the connector credits a payload
carries into the claims the planner decides (``credited_artists``), and
turning the planner's outcomes into the rows the batch persists
(``artist_writes``). The two callers — the application ingest service and
the connector inward resolvers — do the repository calls in between and
nothing else, so every identity decision is made here, once.

Import mints from the ids it already holds and never from a name: a
description is filed only under a connector artist id (``strong_id``) and
``ArtistResolutionRules.same`` refuses every name comparison. The id is the
credit's own ``connector_artist_identifier`` — the connector-track writer
has already stored the ``connector_artists`` record it names. A credit with
no id (Apple's song payload) stays a pending credit for the enrichment
operation to resolve with alias evidence.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final
from uuid import UUID

from attrs import Factory, define, field

from src.domain.entities.artist import Artist, ConnectorArtist, is_various_artists
from src.domain.entities.track import ConnectorArtistCredit, Track
from src.domain.matching.artist_confidence import calculate_artist_confidence
from src.domain.matching.canonical_resolution import (
    Described,
    Outcome,
    ResolutionEvidence,
    creation_of,
    plan_resolution,
)
from src.domain.matching.config import MatchingConfig
from src.domain.matching.evaluation_service import MatchEvaluationService
from src.domain.matching.text_normalization import normalize_for_comparison
from src.domain.matching.types import ArtistEvidence, MatchZone
from src.domain.repositories.mapping import PrimaryCandidate

# Connectors whose artist identifier is the name string itself. Their
# connector-artist rows are written (the cache is keyed on the name, its only
# identity) but never described to the planner: a Last.fm name is capped
# evidence, never identity — one page serves every same-name artist.
NAME_KEYED_CONNECTORS: Final[frozenset[str]] = frozenset({"lastfm"})


@define(frozen=True, slots=True)
class ArtistDescription:
    """An artist as the identity question sees it: a name and, when known, its MBID."""

    name: str
    mbid: str | None = None


def artist_identity_key(description: ArtistDescription) -> str:
    """The name bucket a description would be filed under, normalized."""
    return normalize_for_comparison(description.name)


def _evaluator_for(rules: ArtistResolutionRules) -> MatchEvaluationService:
    return MatchEvaluationService(rules.config)


@define(frozen=True, slots=True)
class ArtistResolutionRules:
    """The artist rules: a connector artist id is the strong id, names never match.

    Every price is the model's connector-id tier
    (``calculate_artist_confidence("connector_id")``): a strong-id collision
    is the same artist by construction, so it is never suspect, and a
    creation is the description vouching for itself at the same tier. The
    name arm is closed on the import path — matching a credit onto an
    existing artist by name is the enrichment operation's job, with alias
    evidence — so ``same`` prices nothing.
    """

    config: MatchingConfig
    _evaluator: MatchEvaluationService = field(
        init=False, default=Factory(_evaluator_for, takes_self=True)
    )

    def describe(self, entity: Artist) -> ArtistDescription:
        return ArtistDescription(name=entity.name, mbid=entity.mbid)

    def strong_match(
        self, description: ArtistDescription, owner: ArtistDescription
    ) -> tuple[ResolutionEvidence, bool]:
        # The id decided it: neither name bears on the price or the verdict.
        del description, owner
        return self._priced(), False

    def same(
        self, description: ArtistDescription, candidate: ArtistDescription
    ) -> ResolutionEvidence | None:
        del description, candidate
        return None

    def creation(self, description: ArtistDescription) -> ResolutionEvidence:
        del description
        return self._priced()

    def _priced(self) -> ResolutionEvidence:
        evidence: ArtistEvidence = calculate_artist_confidence(
            "connector_id", config=self.config
        )
        confidence = evidence.final_score
        zone: MatchZone = "reject"
        if self._evaluator.should_accept_match(confidence, "direct"):
            zone = "accept"
        elif self._evaluator.should_review_match(confidence, "direct"):
            zone = "review"
        return ResolutionEvidence(
            method="direct",
            confidence=confidence,
            zone=zone,
            match_weight=evidence.match_weight,
            evidence=evidence.as_dict(),
        )


def plan_artist_resolution[TKey](
    descriptions: Sequence[Described[TKey, ArtistDescription]],
    *,
    strong_owners: Mapping[str, Artist],
    config: MatchingConfig,
) -> dict[TKey, Outcome[TKey, Artist]]:
    """``plan_resolution`` under the artist rules: strong id or creation, no names."""
    return plan_resolution(
        descriptions,
        strong_owners=strong_owners,
        name_owners={},
        rules=ArtistResolutionRules(config),
    )


# ---------------------------------------------------------------------------
# The import-path minter's pure halves
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ArtistCreditSource:
    """One connector payload's credits, keyed by the payload's track identifier.

    Each credit carries the service's own artist id (or ``None``) on itself,
    so nothing here is positional with anything else.
    """

    key: str
    credits: tuple[ConnectorArtistCredit, ...] = field(converter=tuple)


@define(frozen=True, slots=True)
class CreditClaim:
    """A credit that names a connector artist the planner may decide on."""

    key: str
    credited_name: str
    identifier: str


@define(frozen=True, slots=True)
class ArtistIntake:
    """What a batch of payloads claims about artists, before any probe.

    ``claims`` are the credits the planner decides — none for a name-keyed
    connector, none for a credit without an id. The connector-artist rows
    they name were written with the connector tracks; the minter reads them
    back by identifier.
    """

    claims: tuple[CreditClaim, ...]

    @property
    def identifiers(self) -> list[str]:
        """The identifiers the claims name, first sighting first."""
        return list(dict.fromkeys(claim.identifier for claim in self.claims))

    @property
    def names(self) -> dict[str, str]:
        """The credited name per identifier — the first sighting's spelling."""
        names: dict[str, str] = {}
        for claim in self.claims:
            names.setdefault(claim.identifier, claim.credited_name)
        return names

    def described(
        self, stored: Mapping[str, ConnectorArtist]
    ) -> list[Described[str, ArtistDescription]]:
        """One description per claimed identifier, filed under its stored row id.

        ``name_key`` stays ``None``: a name never settles an artist on the
        import path. An identifier with no stored row is not described.
        """
        names = self.names
        return [
            Described(
                key=identifier,
                description=ArtistDescription(name=names[identifier]),
                strong_id=str(stored[identifier].id),
                name_key=None,
            )
            for identifier in self.identifiers
            if identifier in stored
        ]


def credited_artists(
    connector: str, sources: Sequence[ArtistCreditSource]
) -> ArtistIntake:
    """The claims a batch of payloads carries.

    A credit with no id is skipped outright; a Various Artists credit is never
    a claim (the sentinel is a compilation flag, not an artist). A name-keyed
    connector's credits are never claims.
    """
    if connector in NAME_KEYED_CONNECTORS:
        return ArtistIntake(claims=())
    claims: list[CreditClaim] = []
    for source in sources:
        for credit in source.credits:
            identifier = credit.connector_artist_identifier
            if identifier is None or is_various_artists(credit.credited_name):
                continue
            claims.append(
                CreditClaim(
                    key=source.key,
                    credited_name=credit.credited_name,
                    identifier=identifier,
                )
            )
    return ArtistIntake(claims=tuple(claims))


@define(frozen=True, slots=True)
class ArtistWrites:
    """Everything a decided batch persists, in the order it persists it.

    Only creations assert a mapping: a reused artist was found *through* its
    live mapping, which may carry other provenance (a MusicBrainz url-rel
    seed, a user pin) that a re-assertion at the import tier would rewrite.
    ``reused`` names those artists for the freshness touch instead.
    """

    artists: tuple[Artist, ...]
    mapping_rows: tuple[Mapping[str, object], ...]
    primaries: tuple[PrimaryCandidate, ...]
    assignments: tuple[tuple[UUID, int, UUID], ...]
    reused: tuple[UUID, ...]


def artist_writes(
    plan: Mapping[str, Outcome[str, Artist]],
    intake: ArtistIntake,
    stored: Mapping[str, ConnectorArtist],
    canonicals: Mapping[str, Track],
    *,
    connector: str,
    user_id: str,
    now: datetime,
) -> ArtistWrites:
    """The rows a plan persists: new artists, their mappings, and credit fills.

    A credit is filled on the canonical track its payload resolved to — by
    credited name, not by payload position, because a reused canonical's
    line-up need not be the payload's — and only where the canonical's
    credit has no artist yet.
    """
    names = intake.names
    artist_of: dict[str, Artist] = {}
    created: list[Artist] = []
    reused: dict[UUID, None] = {}
    mapping_rows: list[Mapping[str, object]] = []
    primaries: list[PrimaryCandidate] = []
    for identifier, outcome in plan.items():
        if outcome.kind == "reuse":
            artist = (
                outcome.canonical
                if outcome.canonical is not None
                else artist_of[_leader_of(outcome.leader)]
            )
            if outcome.canonical is not None:
                reused[artist.id] = None
            artist_of[identifier] = artist
            continue
        create = creation_of(outcome)
        if create is None:  # pragma: no cover - every non-reuse outcome creates
            raise ValueError("an outcome that is not a reuse creates")
        artist = Artist(name=names[identifier], user_id=user_id)
        artist_of[identifier] = artist
        created.append(artist)
        connector_artist_id = stored[identifier].id
        mapping_rows.append({
            "user_id": user_id,
            "artist_id": artist.id,
            "connector_artist_id": connector_artist_id,
            "connector_name": connector,
            "match_method": create.evidence.method,
            "confidence": create.evidence.confidence,
            "confidence_evidence": create.evidence.evidence,
            "origin": "automatic",
            "is_primary": True,
            "last_seen_at": now,
        })
        primaries.append(PrimaryCandidate(artist.id, connector, connector_artist_id))

    assignments: dict[tuple[UUID, int], UUID] = {}
    for claim in intake.claims:
        artist = artist_of.get(claim.identifier)
        canonical = canonicals.get(claim.key)
        if artist is None or canonical is None:
            continue
        wanted = normalize_for_comparison(claim.credited_name)
        for position, credit in enumerate(canonical.artists):
            if credit.artist_id is not None:
                continue
            if normalize_for_comparison(credit.credited_name) != wanted:
                continue
            assignments.setdefault((canonical.id, position), artist.id)
    return ArtistWrites(
        artists=tuple(created),
        mapping_rows=tuple(mapping_rows),
        primaries=tuple(primaries),
        assignments=tuple(
            (track_id, position, artist_id)
            for (track_id, position), artist_id in assignments.items()
        ),
        reused=tuple(reused),
    )


def _leader_of(leader: str | None) -> str:
    if leader is None:
        raise ValueError("Reuse names neither a canonical nor a leader")
    return leader


__all__ = [
    "NAME_KEYED_CONNECTORS",
    "ArtistCreditSource",
    "ArtistDescription",
    "ArtistIntake",
    "ArtistResolutionRules",
    "ArtistWrites",
    "CreditClaim",
    "artist_identity_key",
    "artist_writes",
    "credited_artists",
    "plan_artist_resolution",
]
