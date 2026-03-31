"""Integration tests proving playlist update bugs - SIMPLIFIED VERSION.

These tests focus on proving the position-slot overwriting bug WITHOUT relying on added_at timestamps.
The bug: When updating playlists, the repository overwrites DBPlaylistTrack records by position,
losing track identity and causing incorrect behavior with duplicates.

Once bugs are fixed, these tests should pass.
"""

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistUseCase,
)
from src.domain.entities import Artist, Track
from src.domain.entities.track import TrackList
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_mock_metric_config

_MOCK_METRIC_CONFIG = make_mock_metric_config()


class TestPlaylistUpdateRecordIdentityBugs:
    """Tests proving that playlist updates destroy track record identity.

    All tests are xfail: they document a known bug where the repository
    overwrites DBPlaylistTrack records by position instead of by track identity.
    Remove xfail when the record-identity-preserving save_playlist is implemented.
    """

    async def test_dbplaylisttrack_records_follow_tracks_not_positions(
        self, db_session
    ):
        """CRITICAL BUG: DBPlaylistTrack records should follow TRACK IDENTITY, not POSITION.

        EXPECTED BEHAVIOR:
        - Each DBPlaylistTrack record represents "this track's membership in this playlist"
        - When Track A moves from position 0→2, its SAME DBPlaylistTrack record updates sort_key
        - Record ID should remain stable as tracks move

        ACTUAL BUG:
        - Repository treats DBPlaylistTrack as "position slots"
        - Moving Track A from pos 0→2 causes position 0 record to get overwritten with Track B
        - This loses Track A's original record and all its metadata

        PROOF: After reordering [A,B,C] → [B,C,A], check that each track keeps its original DB record ID.
        """
        # Step 1: Create playlist [Track A, Track B, Track C]
        track_a = Track(title="Track A", artists=[Artist(name="Artist A")])
        track_b = Track(title="Track B", artists=[Artist(name="Artist B")])
        track_c = Track(title="Track C", artists=[Artist(name="Artist C")])

        uow = get_unit_of_work(db_session)
        create_use_case = CreateCanonicalPlaylistUseCase(
            metric_config=_MOCK_METRIC_CONFIG
        )

        create_command = CreateCanonicalPlaylistCommand(
            user_id="default",
            name="Test Playlist",
            tracklist=TrackList(tracks=[track_a, track_b, track_c]),
        )

        async with uow:
            create_result = await create_use_case.execute(create_command, uow)
            await uow.commit()

        playlist_id = create_result.playlist.id

        # Step 2: Get original DBPlaylistTrack records
        from sqlalchemy import select

        from src.infrastructure.persistence.database.db_models import DBPlaylistTrack

        async with uow:
            stmt = (
                select(DBPlaylistTrack)
                .where(DBPlaylistTrack.playlist_id == playlist_id)
                .order_by(DBPlaylistTrack.sort_key)
            )
            result = await uow._session.scalars(stmt)
            initial_records = list(result.all())

        assert len(initial_records) == 3

        # Store mapping: track_id → DB record ID
        track_a_id = initial_records[0].track_id
        track_b_id = initial_records[1].track_id
        track_c_id = initial_records[2].track_id

        original_record_ids = {
            track_a_id: initial_records[0].id,
            track_b_id: initial_records[1].id,
            track_c_id: initial_records[2].id,
        }

        print("\nOriginal records:")
        print(
            f"  Track A (track_id={track_a_id}): record_id={original_record_ids[track_a_id]}, position=0"
        )
        print(
            f"  Track B (track_id={track_b_id}): record_id={original_record_ids[track_b_id]}, position=1"
        )
        print(
            f"  Track C (track_id={track_c_id}): record_id={original_record_ids[track_c_id]}, position=2"
        )

        # Step 3: Reorder to [Track B, Track C, Track A]
        async with uow:
            playlist_repo = uow.get_playlist_repository()
            current_playlist = await playlist_repo.get_playlist_by_id(
                playlist_id, user_id="default"
            )

        reordered_tracks = [
            current_playlist.tracks[1],  # Track B: pos 1→0
            current_playlist.tracks[2],  # Track C: pos 2→1
            current_playlist.tracks[0],  # Track A: pos 0→2
        ]

        update_use_case = UpdateCanonicalPlaylistUseCase(
            metric_config=_MOCK_METRIC_CONFIG
        )
        update_command = UpdateCanonicalPlaylistCommand(
            user_id="default",
            playlist_id=str(playlist_id),
            new_tracklist=TrackList(tracks=reordered_tracks),
            append_mode=False,
        )

        async with uow:
            await update_use_case.execute(update_command, uow)
            await uow.commit()

        # Step 4: Get updated records
        async with uow:
            stmt = (
                select(DBPlaylistTrack)
                .where(DBPlaylistTrack.playlist_id == playlist_id)
                .order_by(DBPlaylistTrack.sort_key)
            )
            result = await uow._session.scalars(stmt)
            updated_records = list(result.all())

        assert len(updated_records) == 3

        print("\nUpdated records:")
        print(
            f"  Position 0: track_id={updated_records[0].track_id}, record_id={updated_records[0].id}"
        )
        print(
            f"  Position 1: track_id={updated_records[1].track_id}, record_id={updated_records[1].id}"
        )
        print(
            f"  Position 2: track_id={updated_records[2].track_id}, record_id={updated_records[2].id}"
        )

        # Build map: track_id → new DB record ID
        updated_record_ids = {record.track_id: record.id for record in updated_records}

        # CRITICAL ASSERTIONS: Each track should keep its ORIGINAL record ID
        # This proves records follow tracks, not positions

        assert updated_record_ids[track_a_id] == original_record_ids[track_a_id], (
            f"BUG: Track A lost its DB record! "
            f"Original record_id={original_record_ids[track_a_id]}, "
            f"After update record_id={updated_record_ids[track_a_id]}. "
            f"This proves the repository is overwriting records by position instead of updating them by track_id."
        )

        assert updated_record_ids[track_b_id] == original_record_ids[track_b_id], (
            f"BUG: Track B lost its DB record! "
            f"Original record_id={original_record_ids[track_b_id]}, "
            f"After update record_id={updated_record_ids[track_b_id]}"
        )

        assert updated_record_ids[track_c_id] == original_record_ids[track_c_id], (
            f"BUG: Track C lost its DB record! "
            f"Original record_id={original_record_ids[track_c_id]}, "
            f"After update record_id={updated_record_ids[track_c_id]}"
        )

        # Verify positions are correct (this part should work)
        assert updated_records[0].track_id == track_b_id  # Track B at position 0
        assert updated_records[1].track_id == track_c_id  # Track C at position 1
        assert updated_records[2].track_id == track_a_id  # Track A at position 2

    async def test_duplicate_tracks_create_separate_dbplaylisttrack_records(
        self, db_session
    ):
        """Test that duplicates (same track appearing twice) work correctly.

        SCENARIO: Create playlist, then update to add duplicate of existing track

        EXPECTED BEHAVIOR:
        - First create: [Track A, Track B] → 2 records
        - Then update: [Track A, Track B, Track A] → 3 records (reuse A, reuse B, create new record for second A)
        - Position 0 and position 2 have DIFFERENT record IDs (even though same track_id)
        - Each record can have independent metadata (added_at, sort_key, etc.)

        PROOF: Update existing playlist to add duplicate, verify 3 distinct DB records exist.
        """
        # Step 1: Create initial playlist [Track A, Track B]
        track_a = Track(
            title="Track A", artists=[Artist(name="Artist A")], album="Album 1"
        )
        track_b = Track(title="Track B", artists=[Artist(name="Artist B")])

        uow = get_unit_of_work(db_session)
        create_use_case = CreateCanonicalPlaylistUseCase(
            metric_config=_MOCK_METRIC_CONFIG
        )

        create_command = CreateCanonicalPlaylistCommand(
            user_id="default",
            name="Playlist with Duplicates",
            tracklist=TrackList(tracks=[track_a, track_b]),
        )

        async with uow:
            create_result = await create_use_case.execute(create_command, uow)
            await uow.commit()

        playlist_id = create_result.playlist.id

        # Get initial records
        from sqlalchemy import select

        from src.infrastructure.persistence.database.db_models import DBPlaylistTrack

        async with uow:
            stmt = (
                select(DBPlaylistTrack)
                .where(DBPlaylistTrack.playlist_id == playlist_id)
                .order_by(DBPlaylistTrack.sort_key)
            )
            result = await uow._session.scalars(stmt)
            initial_records = list(result.all())

        assert len(initial_records) == 2, "Should start with 2 tracks"
        track_a_id = initial_records[0].track_id
        track_b_id = initial_records[1].track_id

        print("\nInitial playlist:")
        print(f"  Position 0: track_id={track_a_id}, record_id={initial_records[0].id}")
        print(f"  Position 1: track_id={track_b_id}, record_id={initial_records[1].id}")

        # Step 2: Update playlist to add duplicate of Track A: [A, B, A]
        async with uow:
            playlist_repo = uow.get_playlist_repository()
            current_playlist = await playlist_repo.get_playlist_by_id(
                playlist_id, user_id="default"
            )

        # Add duplicate - same track at position 2
        updated_tracks = [
            current_playlist.tracks[0],  # Track A
            current_playlist.tracks[1],  # Track B
            current_playlist.tracks[0],  # Track A again (duplicate)
        ]

        from src.application.use_cases.update_canonical_playlist import (
            UpdateCanonicalPlaylistCommand,
            UpdateCanonicalPlaylistUseCase,
        )

        update_use_case = UpdateCanonicalPlaylistUseCase(
            metric_config=_MOCK_METRIC_CONFIG
        )
        update_command = UpdateCanonicalPlaylistCommand(
            user_id="default",
            playlist_id=str(playlist_id),
            new_tracklist=TrackList(tracks=updated_tracks),
            append_mode=False,
        )

        async with uow:
            await update_use_case.execute(update_command, uow)
            await uow.commit()

        # Step 3: Verify we now have 3 records
        async with uow:
            stmt = (
                select(DBPlaylistTrack)
                .where(DBPlaylistTrack.playlist_id == playlist_id)
                .order_by(DBPlaylistTrack.sort_key)
            )
            result = await uow._session.scalars(stmt)
            final_records = list(result.all())

        print("\nAfter adding duplicate:")
        for i, record in enumerate(final_records):
            print(f"  Position {i}: track_id={record.track_id}, record_id={record.id}")

        # CRITICAL: Should have 3 SEPARATE records
        assert len(final_records) == 3, (
            f"BUG: Playlist with 3 positions only has {len(final_records)} DB records! "
            f"Each position should have its own record, even for duplicate tracks."
        )

        # Verify positions 0 and 2 are the same track
        assert final_records[0].track_id == final_records[2].track_id, (
            "Positions 0 and 2 should reference the same track"
        )
        assert final_records[0].track_id == track_a_id, "Position 0 should be Track A"
        assert final_records[2].track_id == track_a_id, "Position 2 should be Track A"

        # CRITICAL: Positions 0 and 2 must have DIFFERENT record IDs
        assert final_records[0].id != final_records[2].id, (
            f"BUG: Duplicate Track A instances share the same DB record (id={final_records[0].id})! "
            f"Each playlist position should have its own independent DBPlaylistTrack record."
        )

        # Verify position 0 kept its original record ID (reused)
        assert final_records[0].id == initial_records[0].id, (
            f"Position 0 should reuse original record (id={initial_records[0].id}), "
            f"but got id={final_records[0].id}"
        )

        # Verify position 2 is a NEW record (not one of the original 2)
        original_record_ids = {initial_records[0].id, initial_records[1].id}
        assert final_records[2].id not in original_record_ids, (
            f"Position 2 should be a NEW record for the duplicate, "
            f"but got id={final_records[2].id} which was an original record"
        )

    async def test_removing_track_doesnt_affect_remaining_track_records(
        self, db_session
    ):
        """CRITICAL BUG: Removing Track B from [A,B,C] should NOT change Track A or Track C's DB records.

        EXPECTED BEHAVIOR:
        - Removing Track B deletes its DBPlaylistTrack record
        - Track A and Track C keep their ORIGINAL record IDs
        - Only sort_keys update to reflect new positions

        ACTUAL BUG:
        - Repository may overwrite records during removal/reordering

        PROOF: Remove middle track, verify remaining tracks keep original record IDs.
        """
        # Create playlist [Track A, Track B, Track C]
        track_a = Track(title="Track A", artists=[Artist(name="Artist A")])
        track_b = Track(title="Track B", artists=[Artist(name="Artist B")])
        track_c = Track(title="Track C", artists=[Artist(name="Artist C")])

        uow = get_unit_of_work(db_session)
        create_use_case = CreateCanonicalPlaylistUseCase(
            metric_config=_MOCK_METRIC_CONFIG
        )

        create_command = CreateCanonicalPlaylistCommand(
            user_id="default",
            name="Test Removal",
            tracklist=TrackList(tracks=[track_a, track_b, track_c]),
        )

        async with uow:
            create_result = await create_use_case.execute(create_command, uow)
            await uow.commit()

        playlist_id = create_result.playlist.id

        # Get original records
        from sqlalchemy import select

        from src.infrastructure.persistence.database.db_models import DBPlaylistTrack

        async with uow:
            stmt = (
                select(DBPlaylistTrack)
                .where(DBPlaylistTrack.playlist_id == playlist_id)
                .order_by(DBPlaylistTrack.sort_key)
            )
            result = await uow._session.scalars(stmt)
            initial_records = list(result.all())

        track_a_id = initial_records[0].track_id
        track_c_id = initial_records[2].track_id

        original_track_a_record_id = initial_records[0].id
        original_track_c_record_id = initial_records[2].id

        print("\nBefore removal:")
        print(
            f"  Track A (track_id={track_a_id}): record_id={original_track_a_record_id}"
        )
        print(
            f"  Track C (track_id={track_c_id}): record_id={original_track_c_record_id}"
        )

        # Remove Track B: [A,B,C] → [A,C]
        async with uow:
            playlist_repo = uow.get_playlist_repository()
            current_playlist = await playlist_repo.get_playlist_by_id(
                playlist_id, user_id="default"
            )

        tracks_after_removal = [
            current_playlist.tracks[0],  # Track A
            current_playlist.tracks[2],  # Track C
        ]

        update_use_case = UpdateCanonicalPlaylistUseCase(
            metric_config=_MOCK_METRIC_CONFIG
        )
        update_command = UpdateCanonicalPlaylistCommand(
            user_id="default",
            playlist_id=str(playlist_id),
            new_tracklist=TrackList(tracks=tracks_after_removal),
            append_mode=False,
        )

        async with uow:
            await update_use_case.execute(update_command, uow)
            await uow.commit()

        # Get records after removal
        async with uow:
            stmt = (
                select(DBPlaylistTrack)
                .where(DBPlaylistTrack.playlist_id == playlist_id)
                .order_by(DBPlaylistTrack.sort_key)
            )
            result = await uow._session.scalars(stmt)
            final_records = list(result.all())

        assert len(final_records) == 2, "Should have 2 records after removing Track B"

        print("\nAfter removal:")
        print(
            f"  Position 0: track_id={final_records[0].track_id}, record_id={final_records[0].id}"
        )
        print(
            f"  Position 1: track_id={final_records[1].track_id}, record_id={final_records[1].id}"
        )

        # CRITICAL: Track A and Track C should keep their ORIGINAL record IDs
        assert final_records[0].track_id == track_a_id
        assert final_records[1].track_id == track_c_id

        assert final_records[0].id == original_track_a_record_id, (
            f"BUG: Track A's DB record changed during removal! "
            f"Original record_id={original_track_a_record_id}, "
            f"After removal record_id={final_records[0].id}. "
            f"Track A should keep its original record when Track B is removed."
        )

        assert final_records[1].id == original_track_c_record_id, (
            f"BUG: Track C's DB record changed during removal! "
            f"Original record_id={original_track_c_record_id}, "
            f"After removal record_id={final_records[1].id}. "
            f"Track C should keep its original record when Track B is removed."
        )
