"""Per-user JSONB settings store."""

from sqlalchemy import (
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    PgJsonb,
)


class DBUserSettings(BaseEntity):
    """User preferences and application settings.

    Per-user JSONB store keyed by (user_id, key). Extensible without
    migrations — new settings are just new keys in the JSONB column.
    """

    __tablename__: str = "user_settings"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    settings: Mapped[JsonDict] = mapped_column(PgJsonb, default=dict)

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("user_id", "key", name="uq_user_settings_user_key"),
    )
