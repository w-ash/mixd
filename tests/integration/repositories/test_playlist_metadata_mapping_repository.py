"""Integration tests for PlaylistMetadataMappingRepository.

Covers UNIQUE enforcement on (connector_playlist_id, action_type,
action_value), CASCADE from DBConnectorPlaylist, member-snapshot
replacement semantics, and the Epic 7 decoupling guarantee: a mapping
can exist for a ConnectorPlaylist that has NO canonical Playlist or
PlaylistLink.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.domain.entities.playlist_metadata_mapping import (
    PlaylistMappingMember,
    PlaylistMetadataMapping,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBTrack,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


async def _setup_connector_playlist(db_session, *, user_id: str = "default") -> UUID:
    """Create a bare DBConnectorPlaylist (no canonical Playlist, no link)."""
    uid = uuid4().hex[:8]
    db_cp = DBConnectorPlaylist(
        connector_name="spotify",
        connector_playlist_identifier=f"sp_{uid}",
        name=f"CP {uid}",
        description=None,
        owner="testuser",
        owner_id="user123",
        is_public=True,
        collaborative=False,
        follower_count=0,
        items=[],
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    db_session.add(db_cp)
    await db_session.flush()
    await db_session.commit()
    return db_cp.id


async def _create_track(db_session, *, user_id: str = "default") -> UUID:
    uid = uuid4().hex[:8]
    track = DBTrack(title=f"T {uid}", user_id=user_id)
    db_session.add(track)
    await db_session.flush()
    await db_session.commit()
    return track.id


class TestCreateMappings:
    async def test_persists_tag_mapping_without_canonical_playlist(self, db_session):
        """Validates Epic 7 decoupling: mapping works with only a
        ConnectorPlaylist — no canonical Playlist or PlaylistLink needed."""
        cp_id = await _setup_connector_playlist(db_session)

        uow = get_unit_of_work(db_session)
        async with uow:
            mapping = PlaylistMetadataMapping.create(
                user_id="default",
                connector_playlist_id=cp_id,
                action_type="add_tag",
                raw_action_value="mood:chill",
            )
            repo = uow.get_playlist_metadata_mapping_repository()
            created = await repo.create_mappings([mapping], user_id="default")
            await uow.commit()

        assert len(created) == 1
        assert created[0].action_value == "mood:chill"

    async def test_unique_constraint_skips_duplicate(self, db_session):
        cp_id = await _setup_connector_playlist(db_session)

        m1 = PlaylistMetadataMapping.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="set_preference",
            raw_action_value="star",
        )
        m2 = PlaylistMetadataMapping.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="set_preference",
            raw_action_value="star",
        )

        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            first = await repo.create_mappings([m1], user_id="default")
            await uow.commit()
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            second = await repo.create_mappings([m2], user_id="default")
            await uow.commit()

        assert len(first) == 1
        assert len(second) == 0  # ON CONFLICT DO NOTHING

    async def test_multiple_action_types_on_same_playlist(self, db_session):
        """'Workout Starred' → preference=star AND tag context:workout."""
        cp_id = await _setup_connector_playlist(db_session)

        preference_mapping = PlaylistMetadataMapping.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="set_preference",
            raw_action_value="star",
        )
        tag_mapping = PlaylistMetadataMapping.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="add_tag",
            raw_action_value="context:workout",
        )

        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            created = await repo.create_mappings(
                [preference_mapping, tag_mapping], user_id="default"
            )
            await uow.commit()
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            listed = await repo.list_for_connector_playlist(cp_id, user_id="default")

        assert len(created) == 2
        assert len(listed) == 2


class TestCascadeDelete:
    async def test_deleting_connector_playlist_cascades_to_mappings(self, db_session):
        cp_id = await _setup_connector_playlist(db_session)

        mapping = PlaylistMetadataMapping.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="add_tag",
            raw_action_value="mood:chill",
        )
        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            await repo.create_mappings([mapping], user_id="default")
            await uow.commit()

        # Delete the ConnectorPlaylist; mapping should cascade.
        async with uow:
            cp = await db_session.get(DBConnectorPlaylist, cp_id)
            await db_session.delete(cp)
            await uow.commit()

        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            remaining = await repo.list_for_user(user_id="default")
        assert all(m.connector_playlist_id != cp_id for m in remaining)


class TestReplaceMembers:
    async def test_replace_swaps_full_set(self, db_session):
        cp_id = await _setup_connector_playlist(db_session)
        track1 = await _create_track(db_session)
        track2 = await _create_track(db_session)
        track3 = await _create_track(db_session)

        mapping = PlaylistMetadataMapping.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="add_tag",
            raw_action_value="mood:chill",
        )
        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            await repo.create_mappings([mapping], user_id="default")
            await uow.commit()

        # First snapshot: tracks 1 + 2.
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            await repo.replace_members(
                mapping.id,
                [
                    PlaylistMappingMember(
                        user_id="default", mapping_id=mapping.id, track_id=track1
                    ),
                    PlaylistMappingMember(
                        user_id="default", mapping_id=mapping.id, track_id=track2
                    ),
                ],
                user_id="default",
            )
            await uow.commit()

        # Replace with tracks 2 + 3 (track1 should be removed).
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            await repo.replace_members(
                mapping.id,
                [
                    PlaylistMappingMember(
                        user_id="default", mapping_id=mapping.id, track_id=track2
                    ),
                    PlaylistMappingMember(
                        user_id="default", mapping_id=mapping.id, track_id=track3
                    ),
                ],
                user_id="default",
            )
            await uow.commit()

        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            members = await repo.get_members(mapping.id, user_id="default")

        track_ids = {m.track_id for m in members}
        assert track_ids == {track2, track3}

    async def test_empty_replacement_clears_members(self, db_session):
        cp_id = await _setup_connector_playlist(db_session)
        track = await _create_track(db_session)

        mapping = PlaylistMetadataMapping.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="add_tag",
            raw_action_value="mood:chill",
        )
        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            await repo.create_mappings([mapping], user_id="default")
            await repo.replace_members(
                mapping.id,
                [
                    PlaylistMappingMember(
                        user_id="default", mapping_id=mapping.id, track_id=track
                    )
                ],
                user_id="default",
            )
            await uow.commit()

        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            await repo.replace_members(mapping.id, [], user_id="default")
            await uow.commit()

        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            members = await repo.get_members(mapping.id, user_id="default")
        assert members == []


class TestDeleteMapping:
    async def test_delete_returns_true_and_removes(self, db_session):
        cp_id = await _setup_connector_playlist(db_session)

        mapping = PlaylistMetadataMapping.create(
            user_id="default",
            connector_playlist_id=cp_id,
            action_type="add_tag",
            raw_action_value="mood:chill",
        )
        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            await repo.create_mappings([mapping], user_id="default")
            await uow.commit()

        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            removed = await repo.delete_mapping(mapping.id, user_id="default")
            await uow.commit()
        assert removed is True

        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            found = await repo.find_by_id(mapping.id, user_id="default")
        assert found is None

    async def test_delete_missing_returns_false(self, db_session):
        uow = get_unit_of_work(db_session)
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            removed = await repo.delete_mapping(uuid4(), user_id="default")
        assert removed is False
