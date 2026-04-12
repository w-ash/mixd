"""Sync preferences from existing likes.

After likes are imported from Spotify/Last.fm, this use case creates
preferences automatically. Spotify like → yah, Last.fm love → star.
Respects source priority via the shared ``resolve_preference_change()`` so
manual or playlist_mapping preferences are never overwritten.

Writes are batched: one ``set_preferences`` and one ``add_events`` call
regardless of how many tracks changed.
"""

from datetime import UTC, datetime
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.domain.entities.preference import (
    PREFERENCE_ORDER,
    PreferenceEvent,
    PreferenceState,
    TrackPreference,
    resolve_preference_change,
)
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)

# Last.fm "love" is a deliberate gesture → star. Spotify "like" is default → yah.
SERVICE_PREFERENCE: dict[str, PreferenceState] = {
    "spotify": "yah",
    "lastfm": "star",
}


@define(frozen=True, slots=True)
class SyncPreferencesFromLikesCommand:
    user_id: str


@define(frozen=True, slots=True)
class SyncPreferencesFromLikesResult:
    created: int
    upgraded: int
    skipped: int


@define(slots=True)
class SyncPreferencesFromLikesUseCase:
    async def execute(
        self,
        command: SyncPreferencesFromLikesCommand,
        uow: UnitOfWorkProtocol,
    ) -> SyncPreferencesFromLikesResult:
        async with uow:
            like_repo = uow.get_like_repository()
            pref_repo = uow.get_preference_repository()

            # Build track_id → (state, preferred_at) from likes across services.
            # Higher-preference service wins when the same track appears on both.
            desired: dict[UUID, tuple[PreferenceState, datetime]] = {}
            for service, state in SERVICE_PREFERENCE.items():
                likes = await like_repo.get_all_liked_tracks(
                    service, user_id=command.user_id, is_liked=True
                )
                for like in likes:
                    liked_at = like.liked_at or datetime.now(UTC)
                    prior = desired.get(like.track_id)
                    if (
                        prior is None
                        or PREFERENCE_ORDER[state] > PREFERENCE_ORDER[prior[0]]
                    ):
                        desired[like.track_id] = (state, liked_at)

            if not desired:
                return SyncPreferencesFromLikesResult(created=0, upgraded=0, skipped=0)

            existing_prefs = await pref_repo.get_preferences(
                list(desired), user_id=command.user_id
            )

            preferences_to_write: list[TrackPreference] = []
            events_to_write: list[PreferenceEvent] = []
            created = 0
            upgraded = 0
            skipped = 0

            for track_id, (state, preferred_at) in desired.items():
                existing = existing_prefs.get(track_id)

                if not resolve_preference_change(existing, state, "service_import"):
                    skipped += 1
                    continue

                preferences_to_write.append(
                    TrackPreference(
                        user_id=command.user_id,
                        track_id=track_id,
                        state=state,
                        source="service_import",
                        preferred_at=preferred_at,
                    )
                )
                events_to_write.append(
                    PreferenceEvent(
                        user_id=command.user_id,
                        track_id=track_id,
                        old_state=existing.state if existing else None,
                        new_state=state,
                        source="service_import",
                        preferred_at=preferred_at,
                    )
                )

                if existing is None:
                    created += 1
                else:
                    upgraded += 1

            if preferences_to_write:
                await pref_repo.set_preferences(
                    preferences_to_write, user_id=command.user_id
                )
                await pref_repo.add_events(events_to_write, user_id=command.user_id)

            await uow.commit()

            logger.info(
                "Preference sync from likes complete",
                created=created,
                upgraded=upgraded,
                skipped=skipped,
            )
            return SyncPreferencesFromLikesResult(
                created=created, upgraded=upgraded, skipped=skipped
            )


async def run_sync_preferences_from_likes(
    user_id: str,
) -> SyncPreferencesFromLikesResult:
    """Sync preferences from likes via execute_use_case."""
    from src.application.runner import execute_use_case

    command = SyncPreferencesFromLikesCommand(user_id=user_id)
    return await execute_use_case(
        lambda uow: SyncPreferencesFromLikesUseCase().execute(command, uow),
        user_id=user_id,
    )
