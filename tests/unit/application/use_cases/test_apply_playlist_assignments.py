"""Unit tests for ApplyPlaylistAssignmentsUseCase.

Verifies the core engine contract: preferences/tags applied with source
timestamps from the cached ConnectorPlaylist, manual metadata never
overwritten, snapshot-based removal tracking clears only
assignment-sourced rows, and conflicting assignments log warnings while
resolving via preference order.
"""

from datetime import UTC, datetime
from uuid import uuid7

from src.application.use_cases.apply_playlist_assignments import (
    ApplyPlaylistAssignmentsCommand,
    ApplyPlaylistAssignmentsUseCase,
)
from src.domain.entities.playlist_assignment import (
    PlaylistAssignment,
    PlaylistAssignmentMember,
)
from src.domain.entities.preference import TrackPreference
from tests.fixtures import (
    make_connector_playlist,
    make_connector_playlist_item,
    make_mock_uow,
    make_track,
)


def _cp_with_items(db_id, item_track_ids, name="P"):
    items = [
        make_connector_playlist_item(
            tid,
            position=i,
            added_at=f"2025-0{1 + (i % 9)}-01T00:00:00+00:00",
        )
        for i, tid in enumerate(item_track_ids)
    ]
    return make_connector_playlist(
        id=db_id,
        connector_playlist_identifier=f"sp_{db_id.hex[:6]}",
        name=name,
        items=items,
        snapshot_id="snap",
    )


def _assignment(
    cp_id, action_type, action_value, user_id="default"
) -> PlaylistAssignment:
    return PlaylistAssignment.create(
        user_id=user_id,
        connector_playlist_id=cp_id,
        action_type=action_type,
        raw_action_value=action_value,
    )


def _cmd(user="default") -> ApplyPlaylistAssignmentsCommand:
    return ApplyPlaylistAssignmentsCommand(user_id=user)


class TestNoAssignments:
    async def test_empty_assignments_returns_zero_result(self) -> None:
        uow = make_mock_uow()
        uow.get_playlist_assignment_repository().list_for_user.return_value = []

        result = await ApplyPlaylistAssignmentsUseCase().execute(_cmd(), uow)

        assert result.assignments_processed == 0
        assert result.preferences_applied == 0
        assert result.tags_applied == 0
        uow.commit.assert_not_called()


class TestTagApplication:
    async def test_tag_applied_to_all_resolved_tracks(self) -> None:
        cp_id = uuid7()
        track_a = make_track(id=uuid7(), title="A")
        track_b = make_track(id=uuid7(), title="B")

        assignment = _assignment(cp_id, "add_tag", "mood:chill")
        cp = _cp_with_items(cp_id, ["sp_a", "sp_b"], name="Chill")

        uow = make_mock_uow()
        uow.get_playlist_assignment_repository().list_for_user.return_value = [
            assignment
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "sp_a"): track_a,
            ("spotify", "sp_b"): track_b,
        }
        uow.get_tag_repository().add_tags.side_effect = lambda tags, **kw: list(tags)

        result = await ApplyPlaylistAssignmentsUseCase().execute(_cmd(), uow)

        assert result.tags_applied == 2
        # One add_tags call, with both resolved tracks.
        add_tags_call = uow.get_tag_repository().add_tags.call_args
        written = list(add_tags_call.args[0])
        assert {t.tag for t in written} == {"mood:chill"}
        assert {t.track_id for t in written} == {track_a.id, track_b.id}
        # tagged_at preserved from added_at.
        for tag in written:
            assert tag.tagged_at.year == 2025
        uow.commit.assert_awaited_once()


class TestPreferenceApplication:
    async def test_preference_skipped_when_manual_exists(self) -> None:
        """source priority: manual must never be overwritten by playlist_assignment."""
        cp_id = uuid7()
        track = make_track(id=uuid7(), title="T")
        assignment = _assignment(cp_id, "set_preference", "star")
        cp = _cp_with_items(cp_id, ["sp_t"])

        existing_manual = TrackPreference(
            user_id="default",
            track_id=track.id,
            state="nah",
            source="manual",
            preferred_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

        uow = make_mock_uow()
        uow.get_playlist_assignment_repository().list_for_user.return_value = [
            assignment
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "sp_t"): track,
        }
        uow.get_preference_repository().get_preferences.return_value = {
            track.id: existing_manual
        }

        result = await ApplyPlaylistAssignmentsUseCase().execute(_cmd(), uow)

        assert result.preferences_applied == 0
        uow.get_preference_repository().set_preferences.assert_not_called()

    async def test_preference_applied_when_no_existing(self) -> None:
        cp_id = uuid7()
        track = make_track(id=uuid7())
        assignment = _assignment(cp_id, "set_preference", "star")
        cp = _cp_with_items(cp_id, ["sp_t"])

        uow = make_mock_uow()
        uow.get_playlist_assignment_repository().list_for_user.return_value = [
            assignment
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "sp_t"): track,
        }
        uow.get_preference_repository().get_preferences.return_value = {}

        result = await ApplyPlaylistAssignmentsUseCase().execute(_cmd(), uow)

        assert result.preferences_applied == 1
        written_call = uow.get_preference_repository().set_preferences.call_args
        written_prefs = list(written_call.args[0])
        assert written_prefs[0].state == "star"
        assert written_prefs[0].source == "playlist_assignment"
        # Event should also be written.
        uow.get_preference_repository().add_events.assert_awaited_once()


class TestConflictDetection:
    async def test_conflicting_preferences_resolve_by_order(self) -> None:
        """Two assignments on one playlist → ``star`` AND ``nah`` → star wins."""
        cp_id = uuid7()
        track = make_track(id=uuid7())
        assignment_star = _assignment(cp_id, "set_preference", "star")
        assignment_nah = _assignment(cp_id, "set_preference", "nah")
        cp = _cp_with_items(cp_id, ["sp_t"])

        uow = make_mock_uow()
        uow.get_playlist_assignment_repository().list_for_user.return_value = [
            assignment_nah,  # logged first but lower priority
            assignment_star,
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "sp_t"): track,
        }
        uow.get_preference_repository().get_preferences.return_value = {}

        result = await ApplyPlaylistAssignmentsUseCase().execute(_cmd(), uow)

        assert result.conflicts_logged == 1
        written = uow.get_preference_repository().set_preferences.call_args.args[0]
        assert written[0].state == "star"


class TestRemovalTracking:
    async def test_tracks_removed_from_playlist_clear_assignment_sourced(
        self,
    ) -> None:
        cp_id = uuid7()
        # Only track A remains in the playlist.
        track_a = make_track(id=uuid7(), title="A")
        old_track_b_id = uuid7()

        assignment = _assignment(cp_id, "add_tag", "mood:chill")
        cp = _cp_with_items(cp_id, ["sp_a"])

        uow = make_mock_uow()
        assignment_repo = uow.get_playlist_assignment_repository()
        assignment_repo.list_for_user.return_value = [assignment]
        # Prior snapshot included track B, which is no longer in the playlist.
        assignment_repo.get_members_for_assignments.return_value = {
            assignment.id: [
                PlaylistAssignmentMember(
                    user_id="default",
                    assignment_id=assignment.id,
                    track_id=old_track_b_id,
                ),
                PlaylistAssignmentMember(
                    user_id="default",
                    assignment_id=assignment.id,
                    track_id=track_a.id,
                ),
            ]
        }
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "sp_a"): track_a,
        }
        tag_repo = uow.get_tag_repository()
        tag_repo.add_tags.side_effect = lambda tags, **kw: list(tags)
        tag_repo.remove_tags.side_effect = lambda pairs, **kw: list(pairs)

        result = await ApplyPlaylistAssignmentsUseCase().execute(_cmd(), uow)

        # The removed track had its assignment-sourced tag cleared, with source filter.
        tag_repo.remove_tags.assert_awaited_once()
        remove_call = tag_repo.remove_tags.call_args
        removed_pairs = list(remove_call.args[0])
        assert (old_track_b_id, "mood:chill") in removed_pairs
        assert remove_call.kwargs["source"] == "playlist_assignment"
        assert result.tags_cleared == 1

    async def test_preference_removal_uses_source_filter(self) -> None:
        cp_id = uuid7()
        track_a = make_track(id=uuid7())
        old_track_id = uuid7()

        assignment = _assignment(cp_id, "set_preference", "star")
        cp = _cp_with_items(cp_id, ["sp_a"])

        uow = make_mock_uow()
        assignment_repo = uow.get_playlist_assignment_repository()
        assignment_repo.list_for_user.return_value = [assignment]
        assignment_repo.get_members_for_assignments.return_value = {
            assignment.id: [
                PlaylistAssignmentMember(
                    user_id="default",
                    assignment_id=assignment.id,
                    track_id=old_track_id,
                ),
                PlaylistAssignmentMember(
                    user_id="default",
                    assignment_id=assignment.id,
                    track_id=track_a.id,
                ),
            ]
        }
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "sp_a"): track_a,
        }
        uow.get_preference_repository().get_preferences.return_value = {}
        uow.get_preference_repository().remove_preferences.return_value = 1

        _ = await ApplyPlaylistAssignmentsUseCase().execute(_cmd(), uow)

        remove_call = uow.get_preference_repository().remove_preferences.call_args
        removed_ids = list(remove_call.args[0])
        assert old_track_id in removed_ids
        assert remove_call.kwargs["source"] == "playlist_assignment"


class TestMembershipSnapshot:
    async def test_members_replaced_with_current_tracks(self) -> None:
        cp_id = uuid7()
        track = make_track(id=uuid7())

        assignment = _assignment(cp_id, "add_tag", "mood:chill")
        cp = _cp_with_items(cp_id, ["sp_t"])

        uow = make_mock_uow()
        assignment_repo = uow.get_playlist_assignment_repository()
        assignment_repo.list_for_user.return_value = [assignment]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "sp_t"): track,
        }
        uow.get_tag_repository().add_tags.side_effect = lambda tags, **kw: list(tags)

        _ = await ApplyPlaylistAssignmentsUseCase().execute(_cmd(), uow)

        assignment_repo.replace_members_for_assignments.assert_awaited_once()
        replace_call = assignment_repo.replace_members_for_assignments.call_args
        snapshots = replace_call.args[0]
        assert assignment.id in snapshots
        written_members = list(snapshots[assignment.id])
        assert len(written_members) == 1
        assert written_members[0].track_id == track.id


class TestAssignmentIdsFilter:
    async def test_filter_routes_to_list_for_ids(self) -> None:
        """`assignment_ids=[id]` calls list_for_ids (one query), not list_for_user."""
        cp_id = uuid7()
        track = make_track(id=uuid7())

        target = _assignment(cp_id, "add_tag", "mood:chill")
        cp = _cp_with_items(cp_id, ["sp_t"])

        uow = make_mock_uow()
        assignment_repo = uow.get_playlist_assignment_repository()
        assignment_repo.list_for_ids.side_effect = lambda ids, **kw: [target]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "sp_t"): track,
        }
        uow.get_tag_repository().add_tags.side_effect = lambda tags, **kw: list(tags)

        result = await ApplyPlaylistAssignmentsUseCase().execute(
            ApplyPlaylistAssignmentsCommand(
                user_id="default", assignment_ids=[target.id]
            ),
            uow,
        )

        assignment_repo.list_for_user.assert_not_called()
        assignment_repo.list_for_ids.assert_awaited_once()
        assert result.assignments_processed == 1
        assert result.tags_applied == 1


class TestUnresolvedTracksSkipped:
    async def test_unresolved_tracks_do_not_receive_actions(self) -> None:
        """Spotify tracks not in local library are skipped silently."""
        cp_id = uuid7()
        track_a = make_track(id=uuid7())

        assignment = _assignment(cp_id, "add_tag", "mood:chill")
        cp = _cp_with_items(cp_id, ["sp_a", "sp_unknown"])

        uow = make_mock_uow()
        uow.get_playlist_assignment_repository().list_for_user.return_value = [
            assignment
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]
        # Only one track resolves; sp_unknown is missing from the library.
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "sp_a"): track_a,
        }
        uow.get_tag_repository().add_tags.side_effect = lambda tags, **kw: list(tags)

        result = await ApplyPlaylistAssignmentsUseCase().execute(_cmd(), uow)

        assert result.tags_applied == 1
        written = uow.get_tag_repository().add_tags.call_args.args[0]
        assert len(list(written)) == 1
