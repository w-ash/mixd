"""Schemas for user settings endpoints."""

from pydantic import BaseModel


class UserSettingsResponse(BaseModel):
    """Full user settings object."""

    theme_mode: str


class UserSettingsPatch(BaseModel):
    """Partial settings update — only include fields to change."""

    theme_mode: str | None = None
