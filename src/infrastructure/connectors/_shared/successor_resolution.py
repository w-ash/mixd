"""Platform-asserted successor recording, shared across connectors.

When a provider hands back a *different* id than the one asked about —
Spotify relinking, Apple ``filter[equivalents]``, Tidal's ``replacement``
pointer — the assertion is the platform's, not ours, and every connector
records it the same way: a ``substituted`` event keyed to the requested id,
plus a non-primary stale-id mapping so the old id keeps resolving from cache.

This module closes the design-space §5.3 deferred extraction: seam work was
deferred to the second in-code implementor, and Tidal is the third (after
Spotify relinking and the v0.11.0 Apple equivalents), so the recording moved
here. *Detection* stays per-connector — only the recording is shared. STE;
present-state only.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from attrs import define, field

from src.config.constants import MatchMethod
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue, empty_json_map
from src.domain.repositories.connector import ConnectorMappingSpec
from src.domain.repositories.resolution import (
    ResolutionDecision,
    ResolutionRecorderProtocol,
)


@define(frozen=True, slots=True)
class SuccessorAssertion:
    """One platform assertion that ``returned_id`` succeeds ``requested_id``.

    ``detection`` names the per-connector mechanism that observed the pair —
    ``"id_mismatch"`` (Spotify request/response correlation),
    ``"playparams_catalog_id"`` (Apple equivalents), ``"replacement_pointer"``
    (Tidal). ``extra`` carries whatever makes the assertion interpretable
    later (Spotify's ``market``: the same requested id can relink to
    different returned ids in different markets). ``track_id`` is the
    canonical the requested id resolved to, stamped onto the event.
    """

    requested_id: str
    returned_id: str
    detection: str
    track_id: UUID | None = None
    extra: Mapping[str, JsonValue] = field(factory=empty_json_map)


@runtime_checkable
class SuccessorHook(Protocol):
    """Per-connector consult hook: which of these dead ids have successors?

    Implemented only where the platform exposes a *lookup* for succession —
    Tidal's ``replacement`` relationship answers this question for an id
    that no longer resolves. Spotify and Apple do **not** implement it:
    their detection is live-fetch correlation (the substitute arrives in the
    same response as the fetch that asked), so there is no dead-id consult
    to make — they build ``SuccessorAssertion``s inline and go straight to
    :func:`record_substitutions`.
    """

    async def resolve_successors(
        self, dead_ids: Sequence[str]
    ) -> Mapping[str, SuccessorAssertion]:
        """Successor assertions keyed by requested (dead) id; misses absent."""
        ...


async def record_substitutions(
    recorder: ResolutionRecorderProtocol,
    *,
    connector_name: str,
    assertions: Sequence[SuccessorAssertion],
    user_id: str,
) -> None:
    """Record successor assertions as ``substituted`` events — never supersessions.

    Batched over the chunk: one connector-track lookup and one event insert
    however many ids substituted, because both seams already take collections
    and a substitution is common enough for the per-id form to have cost two
    round trips each.

    The event names the *requested* id's connector track, not the returned
    one. ``substituted`` is a streak-resetting event, and the streak it has
    to reset belongs to the id that kept coming back absent — recording it
    against nothing (or against the substitute) left a substituted id
    accumulating suspicion it had already disproved. An id the lookup does
    not know is still recorded, with no connector track.
    """
    if not assertions:
        return
    requested_ct = await recorder.connector_track_ids(
        [assertion.requested_id for assertion in assertions],
        connector_name=connector_name,
    )
    _ = await recorder.record(
        [
            ResolutionDecision(
                event_type="substituted",
                connector_name=connector_name,
                connector_track_id=requested_ct.get(assertion.requested_id),
                track_id=assertion.track_id,
                payload={
                    "requested_id": assertion.requested_id,
                    "returned_id": assertion.returned_id,
                    "detection": assertion.detection,
                    **assertion.extra,
                },
            )
            for assertion in assertions
        ],
        user_id=user_id,
    )


def stale_id_mapping_spec(
    *,
    track: Track,
    connector: str,
    requested_id: str,
    primary_method: str,
    confidence: int,
    metadata: dict[str, object] | None = None,
) -> ConnectorMappingSpec:
    """The non-primary mapping a substitution owes the *requested* id.

    It exists to make the old id resolve from cache, not to describe the
    track — hence never primary, and the ``*_STALE_ID`` variant of whatever
    method minted the primary mapping, read from the authoritative
    ``MatchMethod.STALE_ID_FOR`` map.
    """
    return ConnectorMappingSpec(
        track=track,
        connector=connector,
        connector_id=requested_id,
        match_method=MatchMethod.STALE_ID_FOR[primary_method],
        confidence=confidence,
        metadata=metadata,
    )
