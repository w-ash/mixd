"""User settings endpoints.

Lightweight CRUD on a JSONB settings blob — no use case needed
(same pattern as connectors/token_storage for simple operations).
"""

from fastapi import APIRouter, Depends

from src.infrastructure.persistence.database.user_context import user_context
from src.infrastructure.persistence.repositories.user_settings import (
    UserSettingsRepository,
)
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.settings import (
    UserSettingsPatch,
    UserSettingsResponse,
)

router = APIRouter(prefix="/settings", tags=["settings"])

_repo = UserSettingsRepository()


@router.get("")
async def get_settings(
    user_id: str = Depends(get_current_user_id),
) -> UserSettingsResponse:
    """Get all user settings."""
    with user_context(user_id):
        settings = await _repo.load(user_id)
    return UserSettingsResponse.model_validate(settings)


@router.patch("")
async def patch_settings(
    body: UserSettingsPatch,
    user_id: str = Depends(get_current_user_id),
) -> UserSettingsResponse:
    """Update user settings (partial merge)."""
    updates = body.model_dump(exclude_none=True)
    with user_context(user_id):
        merged = await _repo.patch(updates, user_id)
    return UserSettingsResponse.model_validate(merged)
