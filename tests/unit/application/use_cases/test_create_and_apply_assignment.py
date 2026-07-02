"""Unit tests for CreateAndApplyAssignmentUseCase — create + apply composition."""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

from src.application.use_cases.apply_playlist_assignments import (
    ApplyPlaylistAssignmentsResult,
    ApplyPlaylistAssignmentsUseCase,
)
from src.application.use_cases.create_and_apply_assignment import (
    CreateAndApplyAssignmentCommand,
    CreateAndApplyAssignmentUseCase,
)
from src.application.use_cases.create_playlist_assignment import (
    CreatePlaylistAssignmentResult,
    CreatePlaylistAssignmentUseCase,
)
from src.domain.entities.playlist_assignment import PlaylistAssignment
from tests.fixtures import make_mock_uow


class TestCreateAndApplyAssignment:
    async def test_creates_then_applies_new_assignment(self):
        uow = make_mock_uow()
        cp_id = uuid7()
        assignment = PlaylistAssignment.create(
            user_id="u",
            connector_playlist_id=cp_id,
            action_type="add_tag",
            raw_action_value="mood:chill",
        )
        apply_result = ApplyPlaylistAssignmentsResult(0, 0, 1, 0, 0, 1)

        with (
            patch.object(
                CreatePlaylistAssignmentUseCase,
                "execute",
                AsyncMock(
                    return_value=CreatePlaylistAssignmentResult(
                        assignment=assignment, created=True
                    )
                ),
            ),
            patch.object(
                ApplyPlaylistAssignmentsUseCase,
                "execute",
                AsyncMock(return_value=apply_result),
            ) as apply_exec,
        ):
            result = await CreateAndApplyAssignmentUseCase().execute(
                CreateAndApplyAssignmentCommand(
                    user_id="u",
                    connector_playlist_id=cp_id,
                    action_type="add_tag",
                    raw_action_value="mood:chill",
                ),
                uow,
            )

        assert result.assignment is assignment
        assert result.apply_result is apply_result
        # The apply leg is scoped to exactly the created assignment's id.
        apply_cmd = apply_exec.await_args.args[0]
        assert apply_cmd.assignment_ids == [assignment.id]
