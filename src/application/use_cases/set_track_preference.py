"""Set or remove a user's preference on a track.

Delegates conflict resolution to ``resolve_preference_change()`` in the
domain layer (shared with the import-time sync use case).
"""

from datetime import UTC, datetime
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.domain.entities.preference import (
    PreferenceEvent,
    PreferenceState,
    TrackPreference,
    resolve_preference_change,
)
from src.domain.entities.sourced_metadata import MetadataSource
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class SetTrackPreferenceCommand:
    user_id: str
    track_id: UUID
    state: PreferenceState | None  # None = remove preference
    source: MetadataSource
    preferred_at: datetime


@define(frozen=True, slots=True)
class SetTrackPreferenceResult:
    track_id: UUID
    state: PreferenceState | None
    changed: bool


@define(slots=True)
class SetTrackPreferenceUseCase:
    async def execute(
        self,
        command: SetTrackPreferenceCommand,
        uow: UnitOfWorkProtocol,
    ) -> SetTrackPreferenceResult:
        async with uow:
            pref_repo = uow.get_preference_repository()

            # Explicit existence check (over relying on the FK constraint) so
            # callers get a clean NotFoundError instead of a DB error.
            await uow.get_track_repository().get_track_by_id(
                command.track_id, user_id=command.user_id
            )

            existing = (
                await pref_repo.get_preferences(
                    [command.track_id], user_id=command.user_id
                )
            ).get(command.track_id)

            # Removal
            if command.state is None:
                if existing is None:
                    return SetTrackPreferenceResult(
                        track_id=command.track_id, state=None, changed=False
                    )
                await pref_repo.remove_preferences(
                    [command.track_id], user_id=command.user_id
                )
                await pref_repo.add_events(
                    [
                        PreferenceEvent(
                            user_id=command.user_id,
                            track_id=command.track_id,
                            old_state=existing.state,
                            new_state=None,
                            source=command.source,
                            preferred_at=command.preferred_at,
                        )
                    ],
                    user_id=command.user_id,
                )
                await uow.commit()
                return SetTrackPreferenceResult(
                    track_id=command.track_id, state=None, changed=True
                )

            # Upsert — delegate conflict resolution to shared domain function
            if not resolve_preference_change(existing, command.state, command.source):
                return SetTrackPreferenceResult(
                    track_id=command.track_id,
                    state=existing.state if existing else None,
                    changed=False,
                )

            await pref_repo.set_preferences(
                [
                    TrackPreference(
                        user_id=command.user_id,
                        track_id=command.track_id,
                        state=command.state,
                        source=command.source,
                        preferred_at=command.preferred_at,
                    )
                ],
                user_id=command.user_id,
            )
            await pref_repo.add_events(
                [
                    PreferenceEvent(
                        user_id=command.user_id,
                        track_id=command.track_id,
                        old_state=existing.state if existing else None,
                        new_state=command.state,
                        source=command.source,
                        preferred_at=command.preferred_at,
                    )
                ],
                user_id=command.user_id,
            )
            await uow.commit()
            return SetTrackPreferenceResult(
                track_id=command.track_id, state=command.state, changed=True
            )


async def run_set_track_preference(
    user_id: str,
    track_id: UUID,
    state: PreferenceState | None,
    source: MetadataSource = "manual",
    preferred_at: datetime | None = None,
) -> SetTrackPreferenceResult:
    """Set or remove a track preference via execute_use_case."""
    from src.application.runner import execute_use_case

    command = SetTrackPreferenceCommand(
        user_id=user_id,
        track_id=track_id,
        state=state,
        source=source,
        preferred_at=preferred_at or datetime.now(UTC),
    )
    return await execute_use_case(
        lambda uow: SetTrackPreferenceUseCase().execute(command, uow),
        user_id=user_id,
    )
