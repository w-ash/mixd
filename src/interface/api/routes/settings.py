"""User settings endpoints."""

from fastapi import APIRouter, Depends

from src.application.runner import execute_use_case
from src.application.use_cases.user_settings import (
    GetUserSettingsCommand,
    GetUserSettingsUseCase,
    PatchUserSettingsCommand,
    PatchUserSettingsUseCase,
)
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.settings import (
    UserSettingsPatch,
    UserSettingsResponse,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(
    user_id: str = Depends(get_current_user_id),
) -> UserSettingsResponse:
    """Get all user settings."""
    command = GetUserSettingsCommand(user_id=user_id)
    result = await execute_use_case(
        lambda uow: GetUserSettingsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return UserSettingsResponse.model_validate(result.settings)


@router.patch("")
async def patch_settings(
    body: UserSettingsPatch,
    user_id: str = Depends(get_current_user_id),
) -> UserSettingsResponse:
    """Update user settings (partial merge)."""
    command = PatchUserSettingsCommand(
        user_id=user_id, updates=body.model_dump(exclude_none=True)
    )
    result = await execute_use_case(
        lambda uow: PatchUserSettingsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return UserSettingsResponse.model_validate(result.settings)
