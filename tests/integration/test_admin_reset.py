"""Integration tests for the admin reset repository.

Verifies that TRUNCATE CASCADE wipes data tables while preserving
oauth_tokens, oauth_states, user_settings, and the externally-managed
users table (not in our schema).
"""

from datetime import UTC, datetime

from sqlalchemy import func, select, table as sa_table
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.reset_database import PRESERVED_TABLES
from src.domain.entities.preference import PreferenceEvent, TrackPreference
from src.infrastructure.persistence.database.db_models import (
    DBOAuthToken,
    DBTrack,
    DBTrackLike,
)
from src.infrastructure.persistence.repositories.admin import AdminRepository
from src.infrastructure.persistence.repositories.track.preferences import (
    TrackPreferenceRepository,
)


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

    truncated = await AdminRepository(db_session).truncate_data_tables(PRESERVED_TABLES)
    await db_session.commit()

    assert truncated, "reset must report the tables it emptied"
    for table in truncated:
        count = (
            await db_session.execute(select(func.count()).select_from(sa_table(table)))
        ).scalar_one()
        assert count == 0, f"Table {table} still has {count} rows after TRUNCATE"


async def test_truncate_preserves_oauth_tokens(db_session: AsyncSession):
    """Seed an oauth_tokens row, truncate, verify it survives.

    This is the point of the preserved set: a reset the user has to
    re-authorise Spotify after is a reset they will not run.
    """
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

    await AdminRepository(db_session).truncate_data_tables(PRESERVED_TABLES)
    await db_session.commit()

    survivor = (
        await db_session.execute(
            select(DBOAuthToken).where(DBOAuthToken.id == token_id)
        )
    ).scalar_one_or_none()
    assert survivor is not None
    assert survivor.access_token == "preserve_me"


async def test_preserved_tables_not_in_data_list(db_session: AsyncSession):
    """Sanity: the metadata-derived data list must not include preserved tables."""
    data_tables = set(AdminRepository(db_session).data_tables(PRESERVED_TABLES))
    assert not (data_tables & PRESERVED_TABLES)
    assert data_tables, "metadata reflection must find the schema's tables"
