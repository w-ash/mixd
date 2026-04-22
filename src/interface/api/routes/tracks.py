"""Track library and detail endpoints.

Merged search+list on GET /tracks (query param `q` triggers search).
All filtering, sorting, and pagination is server-side.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.application.runner import execute_use_case
from src.application.use_cases.batch_tag_tracks import (
    BatchTagTracksCommand,
    BatchTagTracksUseCase,
)
from src.application.use_cases.get_track_details import (
    GetTrackDetailsCommand,
    GetTrackDetailsUseCase,
)
from src.application.use_cases.get_track_playlists import (
    GetTrackPlaylistsCommand,
    GetTrackPlaylistsUseCase,
)
from src.application.use_cases.list_tracks import ListTracksCommand, ListTracksUseCase
from src.application.use_cases.merge_tracks import (
    MergeTracksCommand,
    MergeTracksUseCase,
)
from src.application.use_cases.relink_connector_track import (
    RelinkConnectorTrackCommand,
    RelinkConnectorTrackUseCase,
)
from src.application.use_cases.set_primary_mapping import (
    SetPrimaryMappingCommand,
    SetPrimaryMappingUseCase,
)
from src.application.use_cases.set_track_preference import (
    SetTrackPreferenceCommand,
    SetTrackPreferenceUseCase,
)
from src.application.use_cases.tag_track import TagTrackCommand, TagTrackUseCase
from src.application.use_cases.unlink_connector_track import (
    UnlinkConnectorTrackCommand,
    UnlinkConnectorTrackUseCase,
)
from src.application.use_cases.untag_track import (
    UntagTrackCommand,
    UntagTrackUseCase,
)
from src.config.constants import BusinessLimits
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.tracks import (
    AddTagRequest,
    AddTagResponse,
    BatchTagRequest,
    BatchTagResponse,
    MergeTrackRequest,
    PaginatedLibraryTracksResponse,
    PlaylistBriefSchema,
    RelinkMappingRequest,
    SetPreferenceRequest,
    TrackDetailSchema,
    TrackFacetsSchema,
    UnlinkMappingResponse,
    playlist_to_brief_schema,
    to_library_track,
    to_track_detail,
)

router = APIRouter(prefix="/tracks", tags=["tracks"])


@router.get("")
async def list_tracks(
    user_id: str = Depends(get_current_user_id),
    q: str | None = Query(
        default=None,
        min_length=BusinessLimits.MIN_SEARCH_LENGTH,
        description="Search title/artist/album",
    ),
    liked: bool | None = Query(default=None, description="Filter by liked status"),
    connector: str | None = Query(default=None, description="Filter by connector"),
    preference: str | None = Query(
        default=None, description="Filter by preference state"
    ),
    tag: Annotated[
        list[str] | None,
        Query(
            description="Filter by tag (repeat for multi-tag). Normalized server-side.",
        ),
    ] = None,
    tag_mode: str = Query(
        default="and",
        pattern="^(and|or)$",
        description="Intersection (and) or union (or) when tag has multiple values.",
    ),
    namespace: str | None = Query(
        default=None, description="Filter to tracks carrying any mood:*/energy:* tag."
    ),
    sort: str = Query(
        default="title_asc",
        description="Sort field and direction",
        pattern="^(title|artist|added|duration)_(asc|desc)$",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(
        default=None, description="Opaque cursor for keyset pagination"
    ),
    include_facets: bool = Query(
        default=False,
        description=(
            "When true, return per-facet counts (preference, liked, connector) "
            "scoped to the current filter set."
        ),
    ),
) -> PaginatedLibraryTracksResponse:
    """List tracks with optional search, filters, sorting, and pagination.

    Supports both offset-based and cursor-based (keyset) pagination.
    When ``cursor`` is provided, it takes precedence over ``offset`` for
    O(1) page seeking regardless of depth.
    """
    # Normalize any raw tag values before they reach the filter subquery —
    # a user could hit ?tag=Mood:Chill and still match stored mood:chill.
    # Invalid tags surface as 422 rather than 500.
    from src.domain.entities.tag import normalize_tag

    try:
        normalized_tags = [normalize_tag(t) for t in tag] if tag else None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    command = ListTracksCommand(
        user_id=user_id,
        query=q,
        liked=liked,
        connector=connector,
        preference=preference,
        tags=normalized_tags,
        tag_mode=tag_mode,  # type: ignore[arg-type]  # validated by regex pattern
        namespace=namespace,
        sort_by=sort,  # type: ignore[arg-type]  # validated by FastAPI regex pattern
        limit=limit,
        offset=offset,
        cursor=cursor,
        include_facets=include_facets,
    )
    result = await execute_use_case(
        lambda uow: ListTracksUseCase().execute(command, uow),
        user_id=user_id,
    )
    return PaginatedLibraryTracksResponse(
        data=[
            to_library_track(
                t,
                liked_track_ids=result.liked_track_ids,
                preference_map=result.preference_map,
                tag_map=result.tag_map,
            )
            for t in result.tracks
        ],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
        next_cursor=result.next_cursor,
        facets=TrackFacetsSchema.model_validate(result.facets)
        if result.facets
        else None,
    )


@router.get("/{track_id}")
async def get_track_detail(
    track_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> TrackDetailSchema:
    """Get full track details with metadata, likes, plays, and playlist memberships."""
    command = GetTrackDetailsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return to_track_detail(result)


@router.post("/{track_id}/merge")
async def merge_track(
    track_id: UUID,
    body: MergeTrackRequest,
    user_id: str = Depends(get_current_user_id),
) -> TrackDetailSchema:
    """Merge a duplicate track into this track (winner)."""
    command = MergeTracksCommand(
        user_id=user_id, winner_id=track_id, loser_id=body.loser_id
    )
    await execute_use_case(
        lambda uow: MergeTracksUseCase().execute(command, uow),
        user_id=user_id,
    )
    # Fresh read after merge commit
    detail_cmd = GetTrackDetailsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(detail_cmd, uow),
        user_id=user_id,
    )
    return to_track_detail(result)


@router.patch("/{track_id}/mappings/{mapping_id}")
async def relink_mapping(
    track_id: UUID,
    mapping_id: UUID,
    body: RelinkMappingRequest,
    user_id: str = Depends(get_current_user_id),
) -> TrackDetailSchema:
    """Relink a connector mapping to a different canonical track."""
    command = RelinkConnectorTrackCommand(
        user_id=user_id,
        mapping_id=mapping_id,
        new_track_id=body.new_track_id,
        current_track_id=track_id,
    )
    await execute_use_case(
        lambda uow: RelinkConnectorTrackUseCase().execute(command, uow),
        user_id=user_id,
    )
    # Fresh read after relink
    detail_cmd = GetTrackDetailsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(detail_cmd, uow),
        user_id=user_id,
    )
    return to_track_detail(result)


@router.delete("/{track_id}/mappings/{mapping_id}")
async def unlink_mapping(
    track_id: UUID,
    mapping_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> UnlinkMappingResponse:
    """Unlink a connector mapping from this track."""
    command = UnlinkConnectorTrackCommand(
        user_id=user_id, mapping_id=mapping_id, current_track_id=track_id
    )
    result = await execute_use_case(
        lambda uow: UnlinkConnectorTrackUseCase().execute(command, uow),
        user_id=user_id,
    )
    return UnlinkMappingResponse(
        deleted_mapping_id=result.deleted_mapping_id,
        orphan_track_id=result.orphan_track_id,
    )


@router.patch("/{track_id}/mappings/{mapping_id}/primary")
async def set_primary_mapping(
    track_id: UUID,
    mapping_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> TrackDetailSchema:
    """Set a mapping as the primary for its connector on this track."""
    command = SetPrimaryMappingCommand(
        user_id=user_id, mapping_id=mapping_id, track_id=track_id
    )
    await execute_use_case(
        lambda uow: SetPrimaryMappingUseCase().execute(command, uow),
        user_id=user_id,
    )
    # Fresh read
    detail_cmd = GetTrackDetailsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackDetailsUseCase().execute(detail_cmd, uow),
        user_id=user_id,
    )
    return to_track_detail(result)


@router.get("/{track_id}/playlists")
async def get_track_playlists(
    track_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> list[PlaylistBriefSchema]:
    """Get playlists containing a specific track."""
    command = GetTrackPlaylistsCommand(user_id=user_id, track_id=track_id)
    result = await execute_use_case(
        lambda uow: GetTrackPlaylistsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return [playlist_to_brief_schema(p) for p in result.playlists]


@router.put("/{track_id}/preference")
async def set_track_preference(
    track_id: UUID,
    body: SetPreferenceRequest,
    user_id: str = Depends(get_current_user_id),
) -> SetPreferenceRequest:
    """Set a preference on a track. Source is always 'manual'."""
    command = SetTrackPreferenceCommand(
        user_id=user_id,
        track_id=track_id,
        state=body.state,
        source="manual",
        preferred_at=datetime.now(UTC),
    )
    await execute_use_case(
        lambda uow: SetTrackPreferenceUseCase().execute(command, uow),
        user_id=user_id,
    )
    return body


@router.delete("/{track_id}/preference", status_code=204)
async def delete_track_preference(
    track_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Remove a preference from a track. Idempotent — 204 even if none existed."""
    command = SetTrackPreferenceCommand(
        user_id=user_id,
        track_id=track_id,
        state=None,
        source="manual",
        preferred_at=datetime.now(UTC),
    )
    await execute_use_case(
        lambda uow: SetTrackPreferenceUseCase().execute(command, uow),
        user_id=user_id,
    )
    return Response(status_code=204)


@router.post("/{track_id}/tags", status_code=201)
async def add_track_tag(
    track_id: UUID,
    body: AddTagRequest,
    user_id: str = Depends(get_current_user_id),
) -> AddTagResponse:
    """Add a tag to a track. Source is always 'manual'.

    Re-tagging an already-tagged track returns 201 with ``changed=false``
    — the response tells the client whether a new row actually inserted.
    """
    command = TagTrackCommand(
        user_id=user_id,
        track_id=track_id,
        raw_tag=body.tag,
        source="manual",
        tagged_at=datetime.now(UTC),
    )
    result = await execute_use_case(
        lambda uow: TagTrackUseCase().execute(command, uow),
        user_id=user_id,
    )
    return AddTagResponse(
        track_id=result.track_id, tag=result.tag, changed=result.changed
    )


@router.delete("/{track_id}/tags/{tag}", status_code=204)
async def delete_track_tag(
    track_id: UUID,
    tag: str,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Remove a tag from a track. Idempotent — 204 even if the tag wasn't present.

    Path segments are URL-decoded by FastAPI before arriving here; the use
    case normalizes the value (lowercase/strip/etc.) so clients that send
    ``Mood%3AChill`` still match the stored ``mood:chill``.
    """
    command = UntagTrackCommand(
        user_id=user_id,
        track_id=track_id,
        raw_tag=tag,
        source="manual",
        tagged_at=datetime.now(UTC),
    )
    try:
        await execute_use_case(
            lambda uow: UntagTrackUseCase().execute(command, uow),
            user_id=user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return Response(status_code=204)


@router.post("/tags/batch", status_code=200)
async def batch_tag_tracks(
    body: BatchTagRequest,
    user_id: str = Depends(get_current_user_id),
) -> BatchTagResponse:
    """Apply one tag to many tracks atomically.

    Batches are capped at 15,000 track_ids (enforced by the request
    schema's ``max_length``). An invalid tag rejects the whole batch —
    Pydantic surfaces the ValueError from ``normalize_tag`` as a 422
    before the use case runs, so the user never ends up with half-tagged
    tracks.
    """
    command = BatchTagTracksCommand(
        user_id=user_id,
        track_ids=body.track_ids,
        raw_tag=body.tag,
        source="manual",
        tagged_at=datetime.now(UTC),
    )
    result = await execute_use_case(
        lambda uow: BatchTagTracksUseCase().execute(command, uow),
        user_id=user_id,
    )
    return BatchTagResponse(
        tag=result.tag, requested=result.requested, tagged=result.tagged
    )
