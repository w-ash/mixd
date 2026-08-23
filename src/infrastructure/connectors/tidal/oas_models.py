"""Hand-written Pydantic models for Tidal's JSON:API (v2) response shapes.

PROVISIONAL — derived from the vendored ``tidal-api-oas.json`` (spec version
1.10.106), not yet verified against live responses. The v0.11.3 T7 probe
replays real payloads through these models; any shape the wire contradicts
gets corrected there before the client hardens around it.

Why hand-written instead of generated: the spec is ~1000 schemas of which
mixd consumes a handful; generated code would be a 50k-line dependency on a
spec Tidal revises in place. Instead the spec is vendored and SHA-pinned
(``TIDAL_OAS_SHA256``, enforced by ``test_oas_pin.py``), and a weekly CI job
(``tidal-oas-drift.yml``) runs ``scripts/refresh_tidal_oas.py --check`` so
upstream drift fails visibly. When the spec moves: read the diff, adjust the
consumed shapes if needed, re-pin.

Key design decisions (mirrors ``apple_music/models.py``):
- ``extra="ignore"``: forward-compatible; only consumed fields are declared.
- camelCase wire names ↔ snake_case fields via a ``to_camel`` alias
  generator (``populate_by_name`` keeps test construction easy).
- ``isrc`` is declared optional even though the spec marks it required —
  ISRC-only resolution must see a missing code as "unresolvable", never as a
  parse failure of the whole page.
- ``duration`` is an ISO-8601 duration string (``"PT2M58S"``) per
  ``Tracks_Attributes`` — NOT milliseconds; conversion to seconds happens in
  the conversion layer, not at the boundary.
- ``addedAt`` lives in the collection item identifier's ``meta`` block
  (``UserCollectionTracks_Items_Resource_Identifier_Meta``), not in
  attributes.

Isolation: this module is importable only from within
``src.infrastructure.connectors.tidal`` (the sixth import-linter contract) —
the adapter wraps these shapes; nothing outside the connector couples to
Tidal's wire format.

Spec schema ↔ model map:
- ``Links``                                          → ``CursorLinks``
- ``Tracks_Attributes``                              → ``TidalTrackAttributes``
- ``Tracks_Resource_Object`` / ``Tracks_Relationships`` → ``TidalTrackResource``
- ``Tracks_Replacement_Single_Relationship_Data_Document``
                                                     → ``TidalSingleRelationship``
- ``Tracks_Artists_Multi_Relationship_Data_Document`` → ``TidalManyRelationship``
- ``Artists_Attributes`` / ``Artists_Resource_Object`` → ``TidalArtistResource``
- ``UserCollectionTracks_Items_Resource_Identifier``  → ``TidalCollectionItemRef``
- ``Errors_Document`` / ``Error_Object``              → ``TidalErrorDocument``
"""

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# SHA-256 of the vendored, canonicalized (sorted keys, 2-space indent)
# src/infrastructure/connectors/tidal/tidal-api-oas.json. Printed by
# scripts/refresh_tidal_oas.py after a refresh; enforced by test_oas_pin.py.
TIDAL_OAS_SHA256 = "03522f312d3cc44ac4db230e4257bcd1439ca680eb96d1f3ba97735d3cf2fb32"


class TidalOasModel(BaseModel):
    """Base model for all Tidal JSON:API shapes — shared config, declared once."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )


class CursorLinks(TidalOasModel):
    """JSON:API ``links`` object with Tidal's cursor pagination.

    ``next`` present → follow it for the next page; absent → last page.
    The spec requires ``self``, but nothing downstream depends on it, so a
    missing value degrades to ``None`` rather than failing the page.
    """

    self_url: str | None = Field(default=None, alias="self")
    next: str | None = None


class JsonApiResource(TidalOasModel):
    """Minimal JSON:API resource/identifier: ``id`` + ``type``.

    Also the element type of ``included`` — heterogeneous side-loaded
    resources parse to this base shape; callers re-validate entries they
    consume into a concrete resource model.
    """

    id: str
    type: str


class TidalArtistAttributes(TidalOasModel):
    """Consumed attributes of an artist resource (``Artists_Attributes``)."""

    name: str


class TidalArtistResource(JsonApiResource):
    """Artist resource object (``Artists_Resource_Object``).

    Reaches us only side-loaded through ``included`` (``include=artists``) —
    a track's own attributes never carry artist names.
    """

    attributes: TidalArtistAttributes


class JsonApiDocumentMeta(TidalOasModel):
    """Top-level document ``meta`` — the collection size when served.

    The spec's pagination prose: collection sizes, when available, appear as
    ``meta.total`` and may be approximate. No document schema *owes* us the
    block (the relationship documents declare only data/included/links), so
    both the block and the field are optional — absent reads as ``None``,
    never a parse failure.
    """

    total: int | None = None


class JsonApiDocument[DataT](TidalOasModel):
    """Generic JSON:API top-level document: ``data`` + ``included`` + ``links``.

    Parameterize with the concrete data shape, e.g.
    ``JsonApiDocument[list[TidalTrackResource]]`` for ``GET /tracks`` or
    ``JsonApiDocument[list[TidalCollectionItemRef]]`` for collection items.

    ``included`` entries that validate as artist resources keep their
    ``attributes.name``; everything else degrades to the bare identifier
    shape (consumers filter by ``type`` anyway, so a lookalike entry of
    another type is harmless).
    """

    data: DataT | None = None
    included: list[TidalArtistResource | JsonApiResource] = Field(default_factory=list)
    links: CursorLinks | None = None
    meta: JsonApiDocumentMeta | None = None


class TidalSingleRelationship(TidalOasModel):
    """To-one relationship document; ``data`` is null when it points at nothing."""

    data: JsonApiResource | None = None
    links: CursorLinks | None = None


class TidalManyRelationship(TidalOasModel):
    """To-many relationship document; ``data`` holds ordered identifiers."""

    data: list[JsonApiResource] | None = None


class TidalTrackRelationships(TidalOasModel):
    """Consumed track relationships — ``replacement`` and ``artists``.

    ``replacement`` is Tidal's platform-asserted successor pointer (the
    v0.10.2 typed-succession source for dead track IDs). ``artists`` is the
    ordered linkage that keys side-loaded artist names (``include=artists``).
    """

    replacement: TidalSingleRelationship | None = None
    artists: TidalManyRelationship | None = None


class TidalTrackAttributes(TidalOasModel):
    """Consumed attributes of a track resource (``Tracks_Attributes``)."""

    title: str
    isrc: str | None = None
    duration: str


class TidalTrackResource(JsonApiResource):
    """Track resource object (``Tracks_Resource_Object``)."""

    attributes: TidalTrackAttributes
    relationships: TidalTrackRelationships | None = None


class TidalCollectionItemMeta(TidalOasModel):
    """Meta block on a collection item identifier — carries ``addedAt``."""

    added_at: datetime


class TidalCollectionItemRef(JsonApiResource):
    """userCollection items entry: a track identifier + ``meta.addedAt``."""

    meta: TidalCollectionItemMeta | None = None


class TidalErrorSource(TidalOasModel):
    """JSON:API error source (``Error_Object_Source``)."""

    pointer: str | None = None
    parameter: str | None = None
    header: str | None = None


class TidalError(TidalOasModel):
    """Single JSON:API error object (``Error_Object``)."""

    id: str | None = None
    status: str | None = None
    code: str | None = None
    detail: str | None = None
    source: TidalErrorSource | None = None


class TidalErrorDocument(TidalOasModel):
    """JSON:API error body (``Errors_Document``): ``{"errors": [...]}``."""

    errors: list[TidalError] = Field(default_factory=list)
