"""The database refuses a match_method or origin outside the domain vocabulary.

Pre-flight 2 made the readers raise on an out-of-vocabulary row, which is late:
by then the row is stored and its writer is gone. Migration 054 moved the check
to the storage boundary, and these tests are what proves the ORM declaration
reaches ``metadata.create_all`` — every integration test builds its schema that
way, so a constraint that lived only in the migration would be absent here.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBMatchReview,
    DBTrack,
    DBTrackMapping,
)
from tests.fixtures import TEST_USER_ID

_USER = "default"


async def _make_track(db_session: AsyncSession) -> UUID:
    uid = uuid4().hex[:8]
    track = DBTrack(
        title=f"Track {uid}", artists={"names": [f"Artist {uid}"]}, user_id=TEST_USER_ID
    )
    db_session.add(track)
    await db_session.flush()
    return track.id


async def _make_connector_track(db_session: AsyncSession) -> UUID:
    uid = uuid4().hex[:8]
    ct = DBConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=f"sp_{uid}",
        title=f"CT {uid}",
        artists={"names": [f"Artist {uid}"]},
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    db_session.add(ct)
    await db_session.flush()
    return ct.id


async def _add_mapping(
    db_session: AsyncSession, *, match_method: str = "isrc", origin: str = "automatic"
) -> None:
    track_id = await _make_track(db_session)
    ct_id = await _make_connector_track(db_session)
    db_session.add(
        DBTrackMapping(
            user_id=_USER,
            track_id=track_id,
            connector_track_id=ct_id,
            connector_name="spotify",
            match_method=match_method,
            confidence=90,
            origin=origin,
        )
    )
    await db_session.flush()


async def _add_review(db_session: AsyncSession, *, match_method: str) -> None:
    track_id = await _make_track(db_session)
    ct_id = await _make_connector_track(db_session)
    db_session.add(
        DBMatchReview(
            user_id=_USER,
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method=match_method,
            confidence=70,
            match_weight=0.7,
        )
    )
    await db_session.flush()


class TestVocabularyMembersAreAccepted:
    async def test_mapping_with_a_vocabulary_method(
        self, db_session: AsyncSession
    ) -> None:
        await _add_mapping(db_session, match_method="isrc_match_stale_id")

    async def test_mapping_with_a_manual_override_origin(
        self, db_session: AsyncSession
    ) -> None:
        await _add_mapping(db_session, origin="manual_override")

    async def test_review_with_a_vocabulary_method(
        self, db_session: AsyncSession
    ) -> None:
        await _add_review(db_session, match_method="isrc_suspect")


class TestOutOfVocabularyRowsAreRefused:
    async def test_mapping_match_method(self, db_session: AsyncSession) -> None:
        with pytest.raises(IntegrityError, match="match_method_vocabulary"):
            await _add_mapping(db_session, match_method="bogus")

    async def test_mapping_origin(self, db_session: AsyncSession) -> None:
        with pytest.raises(IntegrityError, match="origin_vocabulary"):
            await _add_mapping(db_session, origin="whatever")

    async def test_review_match_method(self, db_session: AsyncSession) -> None:
        with pytest.raises(IntegrityError, match="match_method_vocabulary"):
            await _add_review(db_session, match_method="bogus")

    async def test_case_variant_of_a_member_is_still_out(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(IntegrityError, match="match_method_vocabulary"):
            await _add_mapping(db_session, match_method="ISRC")
