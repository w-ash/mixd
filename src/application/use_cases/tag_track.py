"""Add a tag to a track.

Takes a raw tag string, normalizes it through the domain layer, and
bulk-upserts via ``add_tags`` + ``add_events``. The repository uses
INSERT ... ON CONFLICT DO NOTHING ... RETURNING so an event is only
written when the row actually inserts — re-tagging an already-tagged
track is a silent no-op.
"""

from datetime import UTC, datetime
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.domain.entities.sourced_metadata import MetadataSource
from src.domain.entities.tag import TagEvent, TrackTag
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class TagTrackCommand:
    user_id: str
    track_id: UUID
    raw_tag: str
    source: MetadataSource
    tagged_at: datetime


@define(frozen=True, slots=True)
class TagTrackResult:
    track_id: UUID
    tag: str
    changed: bool  # False if the tag already existed


@define(slots=True)
class TagTrackUseCase:
    async def execute(
        self,
        command: TagTrackCommand,
        uow: UnitOfWorkProtocol,
    ) -> TagTrackResult:
        async with uow:
            # Explicit existence check so callers get NotFoundError instead
            # of a DB-level FK violation.
            await uow.get_track_repository().get_track_by_id(
                command.track_id, user_id=command.user_id
            )

            tag = TrackTag.create(
                user_id=command.user_id,
                track_id=command.track_id,
                raw_tag=command.raw_tag,
                tagged_at=command.tagged_at,
                source=command.source,
            )

            tag_repo = uow.get_tag_repository()
            inserted = await tag_repo.add_tags([tag], user_id=command.user_id)

            if not inserted:
                return TagTrackResult(
                    track_id=command.track_id, tag=tag.tag, changed=False
                )

            await tag_repo.add_events(
                [
                    TagEvent(
                        user_id=command.user_id,
                        track_id=command.track_id,
                        tag=tag.tag,
                        action="add",
                        source=command.source,
                        tagged_at=command.tagged_at,
                    )
                ],
                user_id=command.user_id,
            )
            await uow.commit()
            return TagTrackResult(track_id=command.track_id, tag=tag.tag, changed=True)


async def run_tag_track(
    user_id: str,
    track_id: UUID,
    raw_tag: str,
    source: MetadataSource = "manual",
    tagged_at: datetime | None = None,
) -> TagTrackResult:
    """Add a tag to a track via execute_use_case."""
    from src.application.runner import execute_use_case

    command = TagTrackCommand(
        user_id=user_id,
        track_id=track_id,
        raw_tag=raw_tag,
        source=source,
        tagged_at=tagged_at or datetime.now(UTC),
    )
    return await execute_use_case(
        lambda uow: TagTrackUseCase().execute(command, uow),
        user_id=user_id,
    )
