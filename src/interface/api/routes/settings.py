"""User settings endpoints.

Lightweight CRUD on a JSONB settings blob — no use case needed
(same pattern as connectors/token_storage for simple operations).
"""

from fastapi import APIRouter

from src.infrastructure.persistence.repositories.user_settings import (
    UserSettingsRepository,
)
from src.interface.api.schemas.settings import (
    UserSettingsPatch,
    UserSettingsResponse,
)

router = APIRouter(prefix="/settings", tags=["settings"])

_repo = UserSettingsRepository()


@router.get("")
async def get_settings() -> UserSettingsResponse:
    """Get all user settings."""
    settings = await _repo.load()
    return UserSettingsResponse(**settings)


@router.patch("")
async def patch_settings(body: UserSettingsPatch) -> UserSettingsResponse:
    """Update user settings (partial merge)."""
    updates = body.model_dump(exclude_none=True)
    merged = await _repo.patch(updates)
    return UserSettingsResponse(**merged)
