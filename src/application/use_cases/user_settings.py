"""Read and update the acting user's settings.

The user wants their interface preferences — theme today, more later — to
follow them across devices rather than living in one browser's storage.
"""

from attrs import define, field

from src.domain.entities.shared import JsonDict, empty_json_map
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class GetUserSettingsCommand:
    """Whose settings to read."""

    user_id: str


@define(frozen=True, slots=True)
class PatchUserSettingsCommand:
    """A partial update — only the keys the user changed."""

    user_id: str
    updates: JsonDict = field(factory=empty_json_map)


@define(frozen=True, slots=True)
class UserSettingsResult:
    """The user's full settings after the operation."""

    settings: JsonDict = field(factory=empty_json_map)


@define(slots=True)
class GetUserSettingsUseCase:
    """Read the acting user's settings, with defaults filled in."""

    async def execute(
        self, command: GetUserSettingsCommand, uow: UnitOfWorkProtocol
    ) -> UserSettingsResult:
        settings = await uow.get_user_settings_repository().load(command.user_id)
        return UserSettingsResult(settings=settings)


@define(slots=True)
class PatchUserSettingsUseCase:
    """Merge a partial update into the acting user's settings."""

    async def execute(
        self, command: PatchUserSettingsCommand, uow: UnitOfWorkProtocol
    ) -> UserSettingsResult:
        async with uow:
            merged = await uow.get_user_settings_repository().patch(
                command.updates, command.user_id
            )
            await uow.commit()
        return UserSettingsResult(settings=merged)
