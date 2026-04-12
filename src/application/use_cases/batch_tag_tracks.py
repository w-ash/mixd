"""Apply one tag to a batch of tracks atomically.

"Tag all tracks in this playlist as ``mood:chill``" is the central flow
for migrating Spotify playlist folders into Mixd tags. Validation
happens entirely before any DB write — a single invalid input aborts
the whole batch so the user never ends up with half-tagged tracks.

At 15k-track scale this must be one bulk upsert + one bulk event
insert. The cap is enforced in the route (Pydantic ``max_length``);
this use case assumes inputs are already under the cap.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.domain.entities.sourced_metadata import MetadataSource
from src.domain.entities.tag import TagEvent, TrackTag
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class BatchTagTracksCommand:
    user_id: str
    track_ids: Sequence[UUID]
    raw_tag: str
    source: MetadataSource
    tagged_at: datetime


@define(frozen=True, slots=True)
class BatchTagTracksResult:
    tag: str
    requested: int  # unique track IDs after dedup
    tagged: int  # rows actually inserted (duplicates skipped)


@define(slots=True)
class BatchTagTracksUseCase:
    async def execute(
        self,
        command: BatchTagTracksCommand,
        uow: UnitOfWorkProtocol,
    ) -> BatchTagTracksResult:
        # Deduplicate track_ids up front — repeated IDs in a single request
        # would produce multiple identical inserts (all but one skipped by
        # ON CONFLICT DO NOTHING) and inflate the event log if we weren't
        # careful. Dedup client-side so the request is clean end-to-end.
        unique_track_ids = list(dict.fromkeys(command.track_ids))

        if not unique_track_ids:
            # Normalization still runs so invalid tags fail even for empty
            # batches — the alternative is silently accepting bad input.
            tag_entity = TrackTag.create(
                user_id=command.user_id,
                track_id=UUID(int=0),  # placeholder, discarded
                raw_tag=command.raw_tag,
                tagged_at=command.tagged_at,
                source=command.source,
            )
            return BatchTagTracksResult(tag=tag_entity.tag, requested=0, tagged=0)

        # Build all TrackTag entities up-front. ``TrackTag.create`` raises
        # ``ValueError`` on an invalid raw_tag — that propagates to the
        # caller BEFORE any repo write, preserving atomicity.
        tags = [
            TrackTag.create(
                user_id=command.user_id,
                track_id=tid,
                raw_tag=command.raw_tag,
                tagged_at=command.tagged_at,
                source=command.source,
            )
            for tid in unique_track_ids
        ]
        normalized_tag = tags[0].tag

        async with uow:
            tag_repo = uow.get_tag_repository()
            inserted = await tag_repo.add_tags(tags, user_id=command.user_id)

            if inserted:
                await tag_repo.add_events(
                    [
                        TagEvent(
                            user_id=command.user_id,
                            track_id=t.track_id,
                            tag=t.tag,
                            action="add",
                            source=command.source,
                            tagged_at=command.tagged_at,
                        )
                        for t in inserted
                    ],
                    user_id=command.user_id,
                )
                await uow.commit()

            return BatchTagTracksResult(
                tag=normalized_tag,
                requested=len(unique_track_ids),
                tagged=len(inserted),
            )


async def run_batch_tag_tracks(
    user_id: str,
    track_ids: Sequence[UUID],
    raw_tag: str,
    source: MetadataSource = "manual",
    tagged_at: datetime | None = None,
) -> BatchTagTracksResult:
    """Apply one tag to many tracks via execute_use_case."""
    from src.application.runner import execute_use_case

    command = BatchTagTracksCommand(
        user_id=user_id,
        track_ids=track_ids,
        raw_tag=raw_tag,
        source=source,
        tagged_at=tagged_at or datetime.now(UTC),
    )
    return await execute_use_case(
        lambda uow: BatchTagTracksUseCase().execute(command, uow),
        user_id=user_id,
    )
