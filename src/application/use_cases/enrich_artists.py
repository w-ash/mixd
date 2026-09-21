"""Identify canonical artists against MusicBrainz and seed what it knows.

An import mints artists from the ids it already holds; this operation is the
slow half. It runs at MusicBrainz' 1 req/s, so it is resumable by design: the
artist's own ``updated_at`` is the marker, moved whether the lookup identified
the artist or not, and the candidate query always hands back the least
recently touched rows. A run that is interrupted, capped by ``limit`` or
killed resumes by simply being run again.

One MusicBrainz response pays for four services. Its ``url-rels`` carry the
artist's Spotify, Discogs, Apple and Tidal ids, so a single lookup seeds
``connector_artists`` rows and id-tier mappings for services Mixd has never
called — which is why the alternative (four name searches) is not on the
table at all. Only a lookup by MBID carries them, so an artist identified by
name search is re-read through its new MBID before anything is written.

That seeding is additive and never corrective. An id the user already
resolves keeps the mapping it has — the import path's first-hand ``direct``
outranks an inferred rel — and is merely stamped as seen. When the live
mapping names a different canonical artist, the two disagree about who owns
the id; the mapping still stands, and the disagreement is reported as an
issue for its owner to settle.

A name search never decides anything on its own. A hit counts only when one
of its spellings — the artist's name or any alias MusicBrainz states — is
exactly the name Mixd holds once normalized, and exactly one hit may do so:
Justice, Jungle and Tourist collide on every service, and a collision leaves
the artist unidentified and merely touched.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from attrs import define, field

from src.application.use_cases._shared.batch_commit import commit_batch
from src.config import create_evaluation_service, create_matching_config, get_logger
from src.config.constants import BusinessLimits
from src.domain.entities.artist import Artist, ArtistAlias, ConnectorArtist
from src.domain.entities.operations import (
    RESOLUTION_FAILURES_KEY,
    RESOLUTION_FAILURES_TRUNCATED_KEY,
    OperationResult,
)
from src.domain.entities.progress import (
    NullProgressEmitter,
    ProgressEmitter,
    create_progress_event,
    tracked_operation,
)
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.matching.artist_confidence import calculate_artist_confidence
from src.domain.matching.artist_enrichment import (
    ArtistEnrichmentProviderProtocol,
    ArtistLookup,
    ArtistUrlRel,
)
from src.domain.matching.config import MatchingConfig
from src.domain.matching.evaluation_service import MatchEvaluationService
from src.domain.matching.text_normalization import normalize_for_comparison
from src.domain.matching.types import ArtistEvidence, ArtistEvidenceLevel
from src.domain.repositories.artist import (
    ArtistAliasRepositoryProtocol,
    ArtistConnectorRepositoryProtocol,
    ArtistRepositoryProtocol,
)
from src.domain.repositories.mapping import PrimaryCandidate
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)

MUSICBRAINZ: Final = "musicbrainz"

# Artists per commit. Small because each one costs a second of wall clock at
# MusicBrainz' rate limit: a wider batch buys nothing and loses more work to
# an interruption.
_COMMIT_EVERY: Final = 25

# Candidates one run will look at when the caller names no limit. At 1 req/s
# this is roughly eight minutes of MusicBrainz budget.
_DEFAULT_LIMIT: Final = 500

# Per-artist failures kept in the result. The audit row's ``issues`` is a
# JSONB column, not a log — the rest are counted and left to the logs.
_MAX_ISSUES: Final = 50


@define(frozen=True, slots=True)
class EnrichArtistsCommand:
    """Selectors for one artist-enrichment pass."""

    user_id: str
    limit: int | None = None
    refresh_older_than_days: int = 30
    dry_run: bool = False


@define(frozen=True, slots=True)
class EnrichArtistsResult:
    """Enrichment outcome.

    Every count rides in ``result.summary_metrics`` — every caller either
    renders that table or forwards the ``OperationResult`` whole.
    """

    result: OperationResult


@define(frozen=True, slots=True)
class _ScoredHit:
    """One search hit, judged against the name Mixd holds."""

    lookup: ArtistLookup
    # Whether some spelling of this hit normalizes to the artist's name.
    agrees: bool
    # Whether that spelling was an alias rather than the primary name.
    via_alias: bool


@define(slots=True)
class _Tally:
    """Counters and per-artist failures accumulated across the run."""

    processed: int = 0
    identified: int = 0
    aliases: int = 0
    mappings: int = 0
    unresolved: int = 0
    issues: list[JsonDict] = field(factory=list)
    issues_dropped: int = 0

    def record_issue(self, artist: Artist, reason: str) -> None:
        """Keep one artist's failure, or count it as dropped past the cap."""
        if len(self.issues) < _MAX_ISSUES:
            self.issues.append({"artist": artist.name, "reason": reason[:300]})
        else:
            self.issues_dropped += 1


@define(slots=True)
class _Batch:
    """Writes held back until the batch boundary."""

    mapping_rows: list[dict[str, object]] = field(factory=list)
    primaries: list[PrimaryCandidate] = field(factory=list)
    touched: list[UUID] = field(factory=list)
    # Connector artists this batch has already staged a mapping for, by the
    # artist staking the claim. The database cannot answer for rows that are
    # still in ``mapping_rows``, and two of a user's artists naming one
    # Spotify id through their url-rels is exactly the case that needs it.
    claimed: dict[UUID, Artist] = field(factory=dict)

    def clear(self) -> None:
        """Drop everything this batch has already flushed."""
        self.mapping_rows.clear()
        self.primaries.clear()
        self.touched.clear()
        self.claimed.clear()


@define(slots=True)
class EnrichArtistsUseCase:
    """Resolve canonical artists to MusicBrainz and cache what it returns."""

    # Defaults to the unit of work's provider; set only to substitute a
    # source (an integration test's fake), never to pick a different service.
    provider: ArtistEnrichmentProviderProtocol | None = None

    async def execute(
        self,
        command: EnrichArtistsCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
    ) -> EnrichArtistsResult:
        """Run one enrichment pass over the artists due for it."""
        emitter = progress_emitter or NullProgressEmitter()
        config = create_matching_config()
        evaluator = create_evaluation_service()
        tally = _Tally()
        batch = _Batch()

        async with uow:
            provider = self.provider or uow.get_artist_enrichment_provider()
            artists = uow.get_artist_repository()
            connectors = uow.get_artist_connector_repository()
            aliases = uow.get_artist_alias_repository()

            cutoff = datetime.now(UTC) - timedelta(days=command.refresh_older_than_days)
            candidates = await artists.list_needing_enrichment(
                user_id=command.user_id,
                older_than=cutoff,
                limit=command.limit or _DEFAULT_LIMIT,
            )

            async with tracked_operation(
                emitter,
                "Identifying artists against MusicBrainz",
                # None, not 0: nothing due is the steady state, and a zero
                # total is rejected as a malformed operation.
                total_items=len(candidates) or None,
            ) as operation_id:
                for position, artist in enumerate(candidates, start=1):
                    tally.processed += 1
                    lookup, evidence = await self._identify(
                        artist, provider, config, evaluator, tally
                    )
                    if lookup is None or evidence is None:
                        tally.unresolved += 1
                        batch.touched.append(artist.id)
                    else:
                        await self._stage(
                            artist,
                            lookup,
                            evidence,
                            command=command,
                            config=config,
                            artists=artists,
                            connectors=connectors,
                            aliases=aliases,
                            batch=batch,
                            tally=tally,
                        )

                    await emitter.emit_progress(
                        create_progress_event(
                            operation_id,
                            current=position,
                            total=len(candidates),
                            message=f"Looked up {artist.name}",
                        )
                    )

                    if position % _COMMIT_EVERY == 0:
                        await self._flush(
                            batch,
                            uow,
                            command=command,
                            artists=artists,
                            connectors=connectors,
                        )

                await self._flush(
                    batch, uow, command=command, artists=artists, connectors=connectors
                )

        logger.info(
            "Artist enrichment complete",
            user_id=command.user_id,
            dry_run=command.dry_run,
            processed=tally.processed,
            identified=tally.identified,
            unresolved=tally.unresolved,
        )
        return self._build_result(tally, dry_run=command.dry_run)

    async def _identify(
        self,
        artist: Artist,
        provider: ArtistEnrichmentProviderProtocol,
        config: MatchingConfig,
        evaluator: MatchEvaluationService,
        tally: _Tally,
    ) -> tuple[ArtistLookup, ArtistEvidence] | tuple[None, None]:
        """The MusicBrainz statement to act on, or ``(None, None)``.

        An artist already anchored on an MBID is refreshed through it and
        never name-searched: the anchor is the strongest evidence there is,
        and a search would only reopen a collision the anchor closed.

        A provider fault is one artist's problem, not the run's — the client
        has already retried what is worth retrying, so the failure is
        recorded and the loop moves on.
        """
        try:
            if artist.mbid:
                lookup = await provider.lookup_artist(artist.mbid)
                if lookup is None:
                    return None, None
                return lookup, calculate_artist_confidence("mbid", config=config)
            hits = await provider.search_artist(artist.name)
        except Exception as error:
            logger.warning(
                "Artist enrichment lookup failed",
                artist=artist.name,
                artist_id=str(artist.id),
                exc_info=True,
            )
            tally.record_issue(artist, f"{type(error).__name__}: {error}")
            return None, None

        chosen = _choose_hit(artist.name, hits, config, evaluator)
        if chosen is None:
            return None, None
        hit, evidence = chosen
        return await self._full_record(artist, hit, provider, tally), evidence

    async def _full_record(
        self,
        artist: Artist,
        hit: ArtistLookup,
        provider: ArtistEnrichmentProviderProtocol,
        tally: _Tally,
    ) -> ArtistLookup:
        """The search hit re-read by MBID, or the hit itself when that fails.

        A search response states no relations — only a lookup asks for
        ``url-rels`` — so acting on the hit alone seeds no Spotify, Discogs,
        Apple or Tidal ids and leaves all four to a second run. One extra
        request per newly identified artist buys them on the first pass, and
        at MusicBrainz' 1 req/s that is the cost of the second the artist was
        already going to spend.

        A failed re-read never unidentifies the artist: the hit carries the
        aliases, so the identity still lands and the ids come next pass.
        """
        try:
            full = await provider.lookup_artist(hit.mbid)
        except Exception as error:
            logger.warning(
                "Artist lookup after search failed — keeping the search hit",
                artist=artist.name,
                mbid=hit.mbid,
                exc_info=True,
            )
            tally.record_issue(
                artist, f"lookup after search: {type(error).__name__}: {error}"
            )
            return hit
        if full is None:
            tally.record_issue(
                artist, f"lookup after search did not resolve {hit.mbid}"
            )
            return hit
        return full

    async def _stage(
        self,
        artist: Artist,
        lookup: ArtistLookup,
        evidence: ArtistEvidence,
        *,
        command: EnrichArtistsCommand,
        config: MatchingConfig,
        artists: ArtistRepositoryProtocol,
        connectors: ArtistConnectorRepositoryProtocol,
        aliases: ArtistAliasRepositoryProtocol,
        batch: _Batch,
        tally: _Tally,
    ) -> None:
        """Write one identified artist's cache rows and stage its mappings."""
        if artist.mbid is None:
            tally.identified += 1

        url_rels = lookup.url_rels
        id_evidence = calculate_artist_confidence("connector_id", config=config)

        if command.dry_run:
            tally.aliases += 1 + sum(1 for alias in lookup.aliases if alias.name)
            tally.mappings += 1 + len(url_rels)
            return

        stored = await connectors.bulk_upsert_connector_artists(
            MUSICBRAINZ, [_musicbrainz_record(lookup)]
        )
        mb_row = stored[lookup.mbid]

        tally.aliases += await aliases.replace_aliases(
            mb_row.id, _alias_records(lookup, mb_row.id)
        )
        await artists.set_identity(
            artist.id, user_id=artist.user_id, mbid=lookup.mbid, kind=lookup.kind
        )

        batch.mapping_rows.append(
            _mapping_row(artist, mb_row.id, MUSICBRAINZ, "mbid", evidence)
        )
        batch.primaries.append(PrimaryCandidate(artist.id, MUSICBRAINZ, mb_row.id))

        # One MusicBrainz response is four services' worth of ids. Several
        # rels for one service are normal (alias projects carry separate ids
        # and are linked, never merged), so every one is kept and the
        # election picks which the UI shows.
        seeded: dict[str, list[tuple[ArtistUrlRel, ConnectorArtist]]] = {}
        for service, rels in _rels_by_service(lookup).items():
            stored_rels = await connectors.bulk_upsert_connector_artists(
                service,
                [
                    ConnectorArtist(
                        connector_name=service,
                        connector_artist_identifier=rel.identifier,
                        name=artist.name,
                        raw_metadata={"url": rel.url},
                    )
                    for rel in rels
                ],
            )
            seeded[service] = [
                (rel, row)
                for rel in rels
                if (row := stored_rels.get(rel.identifier)) is not None
            ]

        tally.mappings += 1 + await self._seed_rel_mappings(
            artist,
            seeded,
            connectors=connectors,
            batch=batch,
            tally=tally,
            evidence=id_evidence,
        )

    async def _seed_rel_mappings(
        self,
        artist: Artist,
        seeded: Mapping[str, Sequence[tuple[ArtistUrlRel, ConnectorArtist]]],
        *,
        connectors: ArtistConnectorRepositoryProtocol,
        batch: _Batch,
        tally: _Tally,
        evidence: ArtistEvidence,
    ) -> int:
        """Stage mappings for the rels that have no live mapping yet.

        Seeding is additive, never corrective. A url-rel is the weakest way
        Mixd can learn an id: the import path writes the same Spotify id from
        the payload it was handed, at ``direct``, and rewriting that to
        ``mb_url_rel`` would replace first-hand evidence with hearsay. So an
        id this user already resolves is left exactly as it is and only
        stamped as seen — re-encounter is freshness, not evidence.

        When the live mapping belongs to a *different* canonical artist, the
        rel and the existing mapping disagree about who owns the id. The
        existing mapping was written against real listening data and this one
        is an inference from a relation, so the rule is the same: the mapping
        stands, the disagreement is recorded as an issue, and nothing is
        re-pointed. Splitting or merging the two artists is a decision for
        their owner, not for a trickle-scheduled background pass.
        """
        owners = await connectors.find_artists_by_connector_artist_ids(
            [row.id for pairs in seeded.values() for _, row in pairs],
            user_id=artist.user_id,
        )
        staged = 0
        for service, pairs in seeded.items():
            re_seen: list[UUID] = []
            for rel, row in pairs:
                owner = owners.get(row.id) or batch.claimed.get(row.id)
                if owner is not None:
                    if owner.id == artist.id:
                        re_seen.append(row.id)
                    else:
                        tally.record_issue(
                            artist,
                            f"{service} id {rel.identifier} already maps to "
                            f"{owner.name} — mapping left as is",
                        )
                    continue
                batch.claimed[row.id] = artist
                batch.mapping_rows.append(
                    _mapping_row(artist, row.id, service, "mb_url_rel", evidence)
                )
                batch.primaries.append(PrimaryCandidate(artist.id, service, row.id))
                staged += 1
            if re_seen:
                await connectors.touch_last_seen(
                    service, re_seen, user_id=artist.user_id
                )
        return staged

    async def _flush(
        self,
        batch: _Batch,
        uow: UnitOfWorkProtocol,
        *,
        command: EnrichArtistsCommand,
        artists: ArtistRepositoryProtocol,
        connectors: ArtistConnectorRepositoryProtocol,
    ) -> None:
        """Assert the batch's mappings, elect primaries, and commit.

        Order matters: the election names mappings, so they have to exist,
        and the events are recorded from the assert's own rows.
        """
        if command.dry_run:
            batch.clear()
            return

        # Copies, not the batch's own lists: ``clear`` below would otherwise
        # empty a sequence the repository is still holding.
        if batch.touched:
            await artists.touch(list(batch.touched), user_id=command.user_id)

        if batch.mapping_rows:
            assertion = await connectors.assert_mappings(list(batch.mapping_rows))
            # "fill" only: an artist that already has a primary for a service
            # keeps it — an arriving seed is not a mandate to overrule a
            # decision the owner already holds.
            _ = await connectors.ensure_primaries(list(batch.primaries), mode="fill")
            await connectors.record_assertion(assertion)

        if batch.touched or batch.mapping_rows:
            await commit_batch(uow)
        batch.clear()

    def _build_result(self, tally: _Tally, *, dry_run: bool) -> EnrichArtistsResult:
        """Render the run's counters as a reportable result."""
        operation_name = (
            "Artist Enrichment (dry run)" if dry_run else "Artist Enrichment"
        )
        result = OperationResult(operation_name=operation_name, execution_time=0.0)
        result.metadata["dry_run"] = dry_run
        result.summary_metrics.add(
            "artists_processed", tally.processed, "Artists Processed", significance=0
        )
        # Named ``resolved`` because that is the vocabulary
        # ``OperationResult.is_partial_failure`` reads: a run that identified
        # artists and hit a few provider faults is partial, not failed.
        result.summary_metrics.add(
            "resolved", tally.identified, "Artists Identified", significance=1
        )
        result.summary_metrics.add(
            "aliases_written", tally.aliases, "Aliases Cached", significance=2
        )
        result.summary_metrics.add(
            "mappings_seeded", tally.mappings, "Mappings Seeded", significance=3
        )
        result.summary_metrics.add(
            "unresolved", tally.unresolved, "Unresolved", significance=4
        )
        if tally.issues or tally.issues_dropped:
            failures: list[JsonValue] = [dict(issue) for issue in tally.issues]
            result.summary_metrics.add(
                "errors",
                len(tally.issues) + tally.issues_dropped,
                "Errors",
                significance=5,
            )
            result.metadata[RESOLUTION_FAILURES_KEY] = failures
            result.metadata[RESOLUTION_FAILURES_TRUNCATED_KEY] = tally.issues_dropped
        return EnrichArtistsResult(result=result)


def _choose_hit(
    name: str,
    hits: Sequence[ArtistLookup],
    config: MatchingConfig,
    evaluator: MatchEvaluationService,
) -> tuple[ArtistLookup, ArtistEvidence] | None:
    """The one hit a name search may be acted on, or ``None``.

    Agreement is exact on a normalized spelling — the artist's own name or
    any alias MusicBrainz states for it — never a fuzzy score. A ranked
    near-miss is what makes a name search dangerous: "Aphex Twin Tribute
    Band" contains every token of the name it is not, and no threshold over a
    fuzzy ratio separates that from a real rename.

    Exactly one agreeing hit is an answer; two or more is a collision, which
    is Justice, Jungle and Tourist on every service. A collision returns
    nothing rather than the better-ranked of two arbitrary orderings.
    """
    agreeing = [scored for hit in hits if (scored := _score_hit(name, hit)).agrees]
    if len(agreeing) != 1:
        if agreeing:
            logger.debug(
                "Ambiguous artist search — leaving unidentified",
                artist=name,
                hits=[scored.lookup.mbid for scored in agreeing],
            )
        return None

    winner = agreeing[0]
    # An agreement reached through a stated alias is identity-grade: "TEED"
    # and the full name are one act, not two names that look alike.
    level: ArtistEvidenceLevel = "alias_name" if winner.via_alias else "name"
    evidence = calculate_artist_confidence(
        level, name_similarity=config.identical_similarity_score, config=config
    )
    if not evaluator.should_accept_match(evidence.final_score, "artist_title"):
        return None
    return winner.lookup, evidence


def _score_hit(name: str, hit: ArtistLookup) -> _ScoredHit:
    """Whether one hit answers to the name, and through which spelling."""
    target = normalize_for_comparison(name)
    for index, spelling in enumerate(hit.alias_names()):
        if target and normalize_for_comparison(spelling) == target:
            return _ScoredHit(lookup=hit, agrees=True, via_alias=index > 0)
    return _ScoredHit(lookup=hit, agrees=False, via_alias=False)


def _musicbrainz_record(lookup: ArtistLookup) -> ConnectorArtist:
    """The ``connector_artists`` row for what MusicBrainz said."""
    return ConnectorArtist(
        connector_name=MUSICBRAINZ,
        connector_artist_identifier=lookup.mbid,
        name=lookup.name,
        raw_metadata={
            "kind": lookup.kind,
            "disambiguation": lookup.disambiguation,
            "aliases": [
                {
                    "name": alias.name,
                    "sort_name": alias.sort_name,
                    "alias_type": alias.alias_type,
                    "locale": alias.locale,
                }
                for alias in lookup.aliases
            ],
            "url_rels": [
                {"service": rel.service, "identifier": rel.identifier, "url": rel.url}
                for rel in lookup.url_rels
            ],
        },
    )


def _alias_records(
    lookup: ArtistLookup, connector_artist_id: UUID
) -> list[ArtistAlias]:
    """Alias rows for one lookup, the primary name first.

    The primary name is stored as an alias of itself so the equivalence cache
    is complete from this table alone: MusicBrainz moves which spelling is
    primary over time ("TEED" is the primary name now, the long form the
    alias), and a reader must not have to join back to find out.
    """
    rows = [
        ArtistAlias(
            connector_artist_id=connector_artist_id,
            name=lookup.name,
            is_primary=True,
        )
    ]
    rows.extend(
        ArtistAlias(
            connector_artist_id=connector_artist_id,
            name=alias.name,
            sort_name=alias.sort_name,
            alias_type=alias.alias_type,
            locale=alias.locale,
        )
        for alias in lookup.aliases
        if alias.name
    )
    return rows


def _rels_by_service(lookup: ArtistLookup) -> dict[str, list[ArtistUrlRel]]:
    """Group the lookup's external ids by the service that issued them."""
    grouped: dict[str, list[ArtistUrlRel]] = {}
    for rel in lookup.url_rels:
        grouped.setdefault(rel.service, []).append(rel)
    return grouped


def _mapping_row(
    artist: Artist,
    connector_artist_id: UUID,
    connector_name: str,
    match_method: str,
    evidence: ArtistEvidence,
) -> dict[str, object]:
    """One row for the mapping assert."""
    return {
        "user_id": artist.user_id,
        "artist_id": artist.id,
        "connector_artist_id": connector_artist_id,
        "connector_name": connector_name,
        "match_method": match_method,
        "confidence": evidence.final_score,
        "confidence_evidence": evidence.as_dict(),
    }


async def run_enrich_artists(
    *,
    user_id: str,
    limit: int | None = None,
    refresh_older_than_days: int = 30,
    dry_run: bool = False,
    progress_emitter: ProgressEmitter | None = None,
) -> EnrichArtistsResult:
    """Convenience wrapper mirroring ``run_rebuild`` for CLI, API and chat."""
    from src.application.runner import execute_use_case

    command = EnrichArtistsCommand(
        user_id=user_id,
        limit=limit,
        refresh_older_than_days=refresh_older_than_days,
        dry_run=dry_run,
    )
    return await execute_use_case(
        lambda uow: EnrichArtistsUseCase().execute(
            command, uow, progress_emitter=progress_emitter
        ),
        user_id=user_id,
        statement_timeout=BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT,
    )
