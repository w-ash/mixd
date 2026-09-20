"""Is a credited artist an existing canonical artist, or a new one?

The artist instantiation of ``plan_resolution``, plus the pure halves of
the import-path minter around it: reading the credits a connector payload
carries into connector-artist records and claims (``credited_artists``),
and turning the planner's outcomes into the rows the batch persists
(``artist_writes``). The two callers — the application ingest service and
the connector inward resolvers — do the repository calls in between and
nothing else, so every identity decision is made here, once.

Import mints from the ids it already holds and never from a name: a
description is filed only under a connector artist id (``strong_id``) and
``ArtistResolutionRules.same`` refuses every name comparison. A credit with
no id — Apple's ``[None]``, a Last.fm name — writes its connector record
where it has one and otherwise stays a pending credit for the enrichment
operation to resolve with alias evidence.
"""

from collections.abc import Awaitable, Mapping, Sequence
from datetime import datetime
from typing import Final, Protocol, TypeIs, runtime_checkable
from uuid import UUID

from attrs import Factory, define, field

from src.domain.entities.artist import Artist, ConnectorArtist, is_various_artists
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.track import ArtistCredit, Track
from src.domain.entities.track_mapping import SupersessionReason
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
    """One connector payload's credits, with the ids the payload carried for them.

    ``artist_ids`` is positional with ``artists`` — ``None`` where the service
    sent no id — and ``artist_dumps`` is the per-artist payload at the same
    positions where the connector supplies one (Spotify's ``artists[i]``),
    else empty.
    """

    key: str
    artists: tuple[ArtistCredit, ...] = field(converter=tuple)
    artist_ids: tuple[str | None, ...] = field(converter=tuple)
    artist_dumps: tuple[JsonDict | None, ...] = field(converter=tuple, default=())


def credit_source(
    key: str, artists: Sequence[ArtistCredit], raw_metadata: Mapping[str, JsonValue]
) -> ArtistCreditSource:
    """Read a payload's positional ``artist_ids`` (and ``artists`` dumps) into a source.

    Defensive on shape: an ``artist_ids`` that is not a list reads as all
    ``None``, and a dump is kept only where it is a mapping whose ``id`` is
    the id at that position, so a misaligned payload never attaches one
    artist's record to another.
    """
    ids_value = raw_metadata.get("artist_ids")
    ids: list[str | None] = [None] * len(artists)
    if isinstance(ids_value, list):
        for position, item in enumerate(ids_value[: len(artists)]):
            ids[position] = item if isinstance(item, str) and item else None

    dumps: list[JsonDict | None] = [None] * len(artists)
    dumps_value = raw_metadata.get("artists")
    if isinstance(dumps_value, list):
        for position, item in enumerate(dumps_value[: len(artists)]):
            if isinstance(item, dict) and item.get("id") == ids[position]:
                dumps[position] = dict(item)
    return ArtistCreditSource(
        key=key, artists=artists, artist_ids=ids, artist_dumps=dumps
    )


def dumped_credits(raw_metadata: Mapping[str, JsonValue]) -> tuple[ArtistCredit, ...]:
    """The credits a payload's ``artists`` dumps name, for a mapping written
    without a payload of its own (a cross-discovered Spotify match)."""
    dumps = raw_metadata.get("artists")
    if not isinstance(dumps, list):
        return ()
    names: list[str] = []
    for item in dumps:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name:
            names.append(name)
    return tuple(ArtistCredit(credited_name=name) for name in names)


@define(frozen=True, slots=True)
class CreditClaim:
    """A credit that names a connector artist the planner may decide on."""

    key: str
    credited_name: str
    identifier: str


@define(frozen=True, slots=True)
class ArtistIntake:
    """What a batch of payloads says about artists, before any probe.

    ``connector_artists`` is one record per identifier (first sighting's
    name and dump win); ``claims`` are the credits the planner decides,
    which is none for a name-keyed connector.
    """

    connector_artists: tuple[ConnectorArtist, ...]
    claims: tuple[CreditClaim, ...]

    @property
    def identifiers(self) -> list[str]:
        """The identifiers the claims name, first sighting first."""
        return list(dict.fromkeys(claim.identifier for claim in self.claims))

    def described(
        self, stored: Mapping[str, ConnectorArtist]
    ) -> list[Described[str, ArtistDescription]]:
        """One description per claimed identifier, filed under its stored row id.

        ``name_key`` stays ``None``: a name never settles an artist on the
        import path. An identifier the upsert did not return is not described.
        """
        names = {a.connector_artist_identifier: a.name for a in self.connector_artists}
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
    """The connector-artist records and claims a batch of payloads carries.

    A credit with no id is skipped outright; a Various Artists credit is never
    a record (the sentinel is a compilation flag, not an artist). A name-keyed
    connector's credits become records but no claims.
    """
    records: dict[str, ConnectorArtist] = {}
    claims: list[CreditClaim] = []
    describable = connector not in NAME_KEYED_CONNECTORS
    for source in sources:
        dumps = source.artist_dumps or (None,) * len(source.artists)
        for credit, identifier, dump in zip(
            source.artists, source.artist_ids, dumps, strict=False
        ):
            if identifier is None or is_various_artists(credit.credited_name):
                continue
            if identifier not in records:
                records[identifier] = ConnectorArtist(
                    connector_name=connector,
                    connector_artist_identifier=identifier,
                    name=credit.credited_name,
                    raw_metadata=dump if dump is not None else {},
                )
            if describable:
                claims.append(
                    CreditClaim(
                        key=source.key,
                        credited_name=credit.credited_name,
                        identifier=identifier,
                    )
                )
    return ArtistIntake(connector_artists=tuple(records.values()), claims=tuple(claims))


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
    names = {a.connector_artist_identifier: a.name for a in intake.connector_artists}
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


@runtime_checkable
class ArtistMappingSeam[TAssertion](Protocol):
    """The batch assert and its event recording, as the concrete artist
    connector repository exposes them.

    Not on the repository protocol — the assertion's type is the generic
    mapping mechanism's own — so a minter narrows to this seam at runtime
    (``mapping_seam_of``) and passes the assertion back unchanged.
    """

    def assert_mappings(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        reason: SupersessionReason = "rematch",
    ) -> Awaitable[TAssertion]: ...

    def record_assertion(self, assertion: TAssertion) -> Awaitable[None]: ...


def has_mapping_seam(repository: object) -> TypeIs[ArtistMappingSeam[object]]:
    """Whether a repository exposes the assert-and-record seam."""
    return isinstance(repository, ArtistMappingSeam)


def mapping_seam_of(repository: object) -> ArtistMappingSeam[object]:
    """The seam, or a ``TypeError`` naming the repository that lacks it."""
    if not has_mapping_seam(repository):
        raise TypeError(
            f"{type(repository).__name__} does not expose assert_mappings/"
            "record_assertion; artist minting needs the concrete mapping seam"
        )
    return repository


__all__ = [
    "NAME_KEYED_CONNECTORS",
    "ArtistCreditSource",
    "ArtistDescription",
    "ArtistIntake",
    "ArtistMappingSeam",
    "ArtistResolutionRules",
    "ArtistWrites",
    "CreditClaim",
    "artist_identity_key",
    "artist_writes",
    "credit_source",
    "credited_artists",
    "dumped_credits",
    "has_mapping_seam",
    "mapping_seam_of",
    "plan_artist_resolution",
]
