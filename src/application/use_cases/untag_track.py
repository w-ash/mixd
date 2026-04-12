"""Remove a tag from a track.

Normalizes the raw tag then removes via ``remove_tags``. An event is
only written when a row actually existed — repeat-removes are silent.
"""

from datetime import UTC, datetime
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.domain.entities.sourced_metadata import MetadataSource
from src.domain.entities.tag import TagEvent, normalize_tag
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class UntagTrackCommand:
    user_id: str
    track_id: UUID
    raw_tag: str
    source: MetadataSource
    tagged_at: datetime


@define(frozen=True, slots=True)
class UntagTrackResult:
    track_id: UUID
    tag: str
    changed: bool  # False if the tag wasn't there to remove


@define(slots=True)
class UntagTrackUseCase:
    async def execute(
        self,
        command: UntagTrackCommand,
        uow: UnitOfWorkProtocol,
    ) -> UntagTrackResult:
        async with uow:
            await uow.get_track_repository().get_track_by_id(
                command.track_id, user_id=command.user_id
            )

            tag = normalize_tag(command.raw_tag)
            tag_repo = uow.get_tag_repository()
            removed = await tag_repo.remove_tags(
                [(command.track_id, tag)], user_id=command.user_id
            )

            if not removed:
                return UntagTrackResult(
                    track_id=command.track_id, tag=tag, changed=False
                )

            await tag_repo.add_events(
                [
                    TagEvent(
                        user_id=command.user_id,
                        track_id=command.track_id,
                        tag=tag,
                        action="remove",
                        source=command.source,
                        tagged_at=command.tagged_at,
                    )
                ],
                user_id=command.user_id,
            )
            await uow.commit()
            return UntagTrackResult(track_id=command.track_id, tag=tag, changed=True)


async def run_untag_track(
    user_id: str,
    track_id: UUID,
    raw_tag: str,
    source: MetadataSource = "manual",
    tagged_at: datetime | None = None,
) -> UntagTrackResult:
    """Remove a tag from a track via execute_use_case."""
    from src.application.runner import execute_use_case

    command = UntagTrackCommand(
        user_id=user_id,
        track_id=track_id,
        raw_tag=raw_tag,
        source=source,
        tagged_at=tagged_at or datetime.now(UTC),
    )
    return await execute_use_case(
        lambda uow: UntagTrackUseCase().execute(command, uow),
        user_id=user_id,
    )
