"""Integration tests for the admin reset command.

Verifies that TRUNCATE CASCADE wipes data tables while preserving
oauth_tokens, oauth_states, user_settings, and the externally-managed
users table (not in our schema).
"""

from datetime import UTC, datetime

from sqlalchemy import func, select, table as sa_table, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.preference import PreferenceEvent, TrackPreference
from src.infrastructure.persistence.database.db_models import (
    DBOAuthToken,
    DBTrack,
    DBTrackLike,
)
from src.infrastructure.persistence.repositories.track.preferences import (
    TrackPreferenceRepository,
)
from src.interface.cli.admin_commands import _PRESERVED_TABLES, _data_tables


async def test_truncate_removes_all_data_tables(db_session: AsyncSession):
    """Seed every data table, truncate, verify zero rows remain."""
    track = DBTrack(title="To Be Wiped", artists={"names": ["Test"]})
    db_session.add(track)
    await db_session.flush()

    db_session.add(DBTrackLike(track_id=track.id, service="spotify", is_liked=True))
    await db_session.flush()

    repo = TrackPreferenceRepository(db_session)
    now = datetime.now(UTC)
    await repo.set_preferences(
        [
            TrackPreference(
                user_id="default",
                track_id=track.id,
                state="star",
                source="manual",
                preferred_at=now,
            )
        ],
        user_id="default",
    )
    await repo.add_events(
        [
            PreferenceEvent(
                user_id="default",
                track_id=track.id,
                old_state=None,
                new_state="star",
                source="manual",
                preferred_at=now,
            )
        ],
        user_id="default",
    )
    await db_session.commit()

    assert (
        await db_session.execute(select(func.count()).select_from(DBTrack))
    ).scalar_one() > 0

    truncate_sql = "TRUNCATE TABLE " + ", ".join(_data_tables()) + " CASCADE"
    await db_session.execute(text(truncate_sql))
    await db_session.commit()

    for table in _data_tables():
        count = (
            await db_session.execute(select(func.count()).select_from(sa_table(table)))
        ).scalar_one()
        assert count == 0, f"Table {table} still has {count} rows after TRUNCATE"


async def test_truncate_preserves_oauth_tokens(db_session: AsyncSession):
    """Seed an oauth_tokens row, truncate, verify it survives."""
    token = DBOAuthToken(
        user_id="default",
        service="spotify",
        token_type="oauth2",
        access_token="preserve_me",
        refresh_token="refresh_me",
        expires_at=datetime.now(UTC),
    )
    db_session.add(token)
    await db_session.commit()
    token_id = token.id

    truncate_sql = "TRUNCATE TABLE " + ", ".join(_data_tables()) + " CASCADE"
    await db_session.execute(text(truncate_sql))
    await db_session.commit()

    survivor = (
        await db_session.execute(
            select(DBOAuthToken).where(DBOAuthToken.id == token_id)
        )
    ).scalar_one_or_none()
    assert survivor is not None
    assert survivor.access_token == "preserve_me"


def test_preserved_tables_not_in_data_list():
    """Sanity: the metadata-derived data list must not include preserved tables."""
    data_tables = set(_data_tables())
    assert not (data_tables & _PRESERVED_TABLES)
