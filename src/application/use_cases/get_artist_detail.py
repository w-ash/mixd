"""Use case for the artist detail view.

Assembles what ``/artists/{id}`` renders in one transaction: the canonical
artist, its live connector mappings (each with the service's own page URL), the
library track count, the favorite flag, and the related projects the connector
payloads state.

Related projects are read out of ``raw_metadata`` rather than a relations table
— this cycle has none (see ``domain/repositories/artist.py::ArtistMappingInfo``).
Three shapes are understood, all optional and all tolerated when absent or
malformed: MusicBrainz ``aliases`` (alternative names), MusicBrainz ``url_rels``
(the same project's id on another service), and Discogs ``aliases`` / ``members``
(side projects and band membership). Anything else in the payload is ignored.
"""

from collections.abc import Sequence
from uuid import UUID

from attrs import define, field

from src.application.use_cases._shared.artist_urls import connector_artist_url
from src.domain.entities.artist import Artist
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import NotFoundError
from src.domain.repositories.artist import ArtistMappingInfo
from src.domain.repositories.uow import UnitOfWorkProtocol

# Which ``raw_metadata`` keys carry related projects, and the relation each
# states. ``url_rels`` is the odd one: its entries name the *same* project on
# another service, not a different project.
type RelationKind = str


@define(frozen=True, slots=True)
class ArtistConnectorMappingInfo:
    """One live mapping on an artist, with the service's own page for it."""

    connector_name: str
    connector_artist_identifier: str
    name: str
    is_primary: bool = False
    match_method: str = ""
    confidence: int = 0
    external_url: str | None = None


@define(frozen=True, slots=True)
class RelatedProject:
    """A name a connector payload links to this artist.

    ``relation`` is one of ``alias`` (another name for the same act),
    ``same_as`` (this act's id on another service) or ``member`` (a person in
    the group, or a group the person is in).
    """

    name: str
    relation: RelationKind
    connector_name: str
    identifier: str | None = None


@define(frozen=True, slots=True)
class GetArtistDetailCommand:
    """Identify the artist to assemble."""

    user_id: str
    artist_id: UUID


@define(frozen=True, slots=True)
class GetArtistDetailResult:
    """Everything the artist detail page renders."""

    artist: Artist
    track_count: int
    is_favorited: bool
    connector_mappings: list[ArtistConnectorMappingInfo] = field(factory=list)
    related: list[RelatedProject] = field(factory=list)


def _as_str(value: JsonValue) -> str | None:
    """A non-empty string, or None — connector payloads are untrusted shapes."""
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _entries(raw: JsonDict, key: str) -> list[JsonValue]:
    value = raw.get(key)
    return list(value) if isinstance(value, list) else []


def _named_relation(
    entry: JsonValue, relation: RelationKind, connector_name: str
) -> RelatedProject | None:
    """Build a relation from a ``{"name": …}`` object or a bare string."""
    if (name := _as_str(entry)) is not None:
        return RelatedProject(
            name=name, relation=relation, connector_name=connector_name
        )
    if not isinstance(entry, dict):
        return None
    name = _as_str(entry.get("name"))
    if name is None:
        return None
    return RelatedProject(
        name=name,
        relation=relation,
        connector_name=connector_name,
        identifier=_as_str(entry.get("id")),
    )


def _url_rel_relation(entry: JsonValue) -> RelatedProject | None:
    """Turn one MusicBrainz ``url_rels`` entry into a ``same_as`` relation."""
    if not isinstance(entry, dict):
        return None
    service = _as_str(entry.get("service"))
    identifier = _as_str(entry.get("identifier"))
    if service is None or identifier is None:
        return None
    return RelatedProject(
        name=service,
        relation="same_as",
        connector_name=service,
        identifier=identifier,
    )


def _related_from_mapping(info: ArtistMappingInfo) -> list[RelatedProject]:
    """Related projects one mapping's connector payload states."""
    raw = info["raw_metadata"]
    connector = info["connector_name"]
    related = [
        relation
        for entry in _entries(raw, "aliases")
        if (relation := _named_relation(entry, "alias", connector)) is not None
    ]
    related += [
        relation
        for entry in _entries(raw, "members")
        if (relation := _named_relation(entry, "member", connector)) is not None
    ]
    related += [
        relation
        for entry in _entries(raw, "url_rels")
        if (relation := _url_rel_relation(entry)) is not None
    ]
    return related


def _dedupe(related: Sequence[RelatedProject]) -> list[RelatedProject]:
    """First occurrence wins — two services can state the same alias."""
    seen: set[tuple[str, str]] = set()
    unique: list[RelatedProject] = []
    for relation in related:
        key = (relation.name.casefold(), relation.relation)
        if key not in seen:
            seen.add(key)
            unique.append(relation)
    return unique


def _to_mapping_info(info: ArtistMappingInfo) -> ArtistConnectorMappingInfo:
    identifier = info["connector_artist_identifier"]
    return ArtistConnectorMappingInfo(
        connector_name=info["connector_name"],
        connector_artist_identifier=identifier,
        name=info["name"],
        is_primary=info["is_primary"],
        match_method=info["match_method"],
        confidence=info["confidence"],
        external_url=connector_artist_url(info["connector_name"], identifier),
    )


@define(slots=True)
class GetArtistDetailUseCase:
    """Assemble one artist's detail view."""

    async def execute(
        self, command: GetArtistDetailCommand, uow: UnitOfWorkProtocol
    ) -> GetArtistDetailResult:
        """Read the artist and everything the detail page hangs off it.

        Raises:
            NotFoundError: The artist does not exist, or belongs to another user.
        """
        async with uow:
            artist_repo = uow.get_artist_repository()
            artist = await artist_repo.get_artist_by_id(
                command.artist_id, user_id=command.user_id
            )
            if artist is None:
                raise NotFoundError(f"Artist {command.artist_id} not found")

            mappings = await uow.get_artist_connector_repository().get_full_mappings_for_artist(
                command.artist_id, user_id=command.user_id
            )
            counts = await artist_repo.count_tracks_by_artist(
                [command.artist_id], user_id=command.user_id
            )
            favorited = (
                await uow.get_artist_favorite_repository().get_favorite_status_batch(
                    [command.artist_id], user_id=command.user_id
                )
            )

            related: list[RelatedProject] = []
            for info in mappings:
                related.extend(_related_from_mapping(info))

            return GetArtistDetailResult(
                artist=artist,
                track_count=counts.get(command.artist_id, 0),
                is_favorited=command.artist_id in favorited,
                connector_mappings=[_to_mapping_info(m) for m in mappings],
                related=_dedupe(related),
            )


async def run_get_artist_detail(
    *, user_id: str, artist_id: UUID
) -> GetArtistDetailResult:
    """Read one artist's detail view via ``execute_use_case`` — the CLI's entry."""
    from src.application.runner import execute_use_case

    command = GetArtistDetailCommand(user_id=user_id, artist_id=artist_id)
    return await execute_use_case(
        lambda uow: GetArtistDetailUseCase().execute(command, uow),
        user_id=user_id,
    )
