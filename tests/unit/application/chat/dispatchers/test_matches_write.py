"""Unit tests for the ``manage_track_matches`` two-phase write dispatcher.

Covers the five operations (relink, unlink, set_primary, resolve_review,
unreject) across both phases: ``handle_manage_track_matches`` proposes (stores a
pending action, marking unlink destructive) and ``exec_manage_track_matches``
commits through the matching use case. The pending-action store is swapped for a
fresh instance per test, and ``execute_use_case`` is monkeypatched on the module
under test so the commit path never touches a database; the success paths run
the real factory into a patched use-case ``execute`` to see the Command built.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, matches_write
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.relink_connector_track import (
    RelinkConnectorTrackCommand,
    RelinkConnectorTrackResult,
    RelinkConnectorTrackUseCase,
)
from src.application.use_cases.resolve_match_review import (
    ResolveMatchReviewCommand,
    ResolveMatchReviewResult,
    ResolveMatchReviewUseCase,
)
from src.application.use_cases.unlink_connector_track import (
    UnlinkConnectorTrackCommand,
    UnlinkConnectorTrackResult,
    UnlinkConnectorTrackUseCase,
)
from src.application.use_cases.unreject_mapping_candidate import (
    UnrejectMappingCandidateCommand,
    UnrejectMappingCandidateResult,
    UnrejectMappingCandidateUseCase,
)
from src.domain.entities.match_review import MatchReview
from src.domain.exceptions import NotFoundError, ToolExecutionError
from tests.fixtures import TEST_USER_ID, InMemoryPendingActionStore

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> InMemoryPendingActionStore:
    store = InMemoryPendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _fake_use_case_runner(result: object):
    async def _run(factory, user_id: str | None = None):  # matches runner signature
        return result

    return _run


def _capture(
    monkeypatch: pytest.MonkeyPatch, use_case: type, result: object
) -> dict[str, object]:
    """Run the dispatcher's real factory into ``use_case``; record its Command.

    Also records the ``user_id`` the runner received, so a commit that drops the
    caller's tenant fails.
    """
    seen: dict[str, object] = {}

    async def _execute(self: object, command: object, uow: object) -> object:
        seen["command"] = command
        return result

    async def _run(factory, user_id: str | None = None):  # runner signature
        seen["user_id"] = user_id
        return await factory(object())

    monkeypatch.setattr(use_case, "execute", _execute)
    monkeypatch.setattr(_common, "execute_use_case", _run)
    return seen


async def _action(details: dict[str, object]) -> PendingAction:
    return await InMemoryPendingActionStore().create(
        user_id="default",
        tool_name="manage_track_matches",
        tool_input={},
        description="Manage match",
        details=details,
    )


class TestManageTrackMatchesPropose:
    async def test_relink_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        mapping_id, new_id, current_id = uuid4(), uuid4(), uuid4()

        result = await matches_write.handle_manage_track_matches(
            {
                "operation": "relink",
                "mapping_id": str(mapping_id),
                "new_track_id": str(new_id),
                "current_track_id": str(current_id),
            },
            _CTX,
        )

        assert result["status"] == "pending_confirmation"
        details = result["details"]
        assert details["operation"] == "relink"
        assert details["mapping_id"] == str(mapping_id)
        assert details["new_track_id"] == str(new_id)
        assert details["changes"]
        # relink is not destructive.
        assert "severity" not in details

        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_track_matches"

    async def test_unlink_is_destructive(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        result = await matches_write.handle_manage_track_matches(
            {
                "operation": "unlink",
                "mapping_id": str(uuid4()),
                "current_track_id": str(uuid4()),
            },
            _CTX,
        )

        details = result["details"]
        assert details["operation"] == "unlink"
        assert details["severity"] == "destructive"
        assert details["warning"] == "severs the connector mapping"

    async def test_resolve_review_carries_action(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        review_id = uuid4()
        result = await matches_write.handle_manage_track_matches(
            {
                "operation": "resolve_review",
                "review_id": str(review_id),
                "action": "accept",
            },
            _CTX,
        )

        assert result["details"]["operation"] == "resolve_review"
        assert result["details"]["action"] == "accept"
        assert "Accept" in result["description"]

    async def test_unreject_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        connector_track_id, candidate_track_id = uuid4(), uuid4()

        result = await matches_write.handle_manage_track_matches(
            {
                "operation": "unreject",
                "connector_track_id": str(connector_track_id),
                "candidate_track_id": str(candidate_track_id),
            },
            _CTX,
        )

        assert result["status"] == "pending_confirmation"
        details = result["details"]
        assert details["operation"] == "unreject"
        assert details["connector_track_id"] == str(connector_track_id)
        assert details["candidate_track_id"] == str(candidate_track_id)
        assert details["changes"]
        # unreject is not destructive — it withdraws a suppression.
        assert "severity" not in details

        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_track_matches"

    async def test_unreject_missing_field_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="candidate_track_id"):
            await matches_write.handle_manage_track_matches(
                {
                    "operation": "unreject",
                    "connector_track_id": str(uuid4()),
                },
                _CTX,
            )

    async def test_unknown_operation_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="operation"):
            await matches_write.handle_manage_track_matches(
                {"operation": "bogus"}, _CTX
            )

    async def test_relink_missing_field_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="new_track_id"):
            await matches_write.handle_manage_track_matches(
                {
                    "operation": "relink",
                    "mapping_id": str(uuid4()),
                    "current_track_id": str(uuid4()),
                },
                _CTX,
            )


class TestExecManageTrackMatches:
    async def test_relink_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mapping_id, old_id, new_id = uuid4(), uuid4(), uuid4()
        seen = _capture(
            monkeypatch,
            RelinkConnectorTrackUseCase,
            RelinkConnectorTrackResult(old_track_id=old_id, new_track_id=new_id),
        )
        action = await _action({
            "operation": "relink",
            "mapping_id": str(mapping_id),
            "new_track_id": str(new_id),
            "current_track_id": str(old_id),
        })

        result = await matches_write.exec_manage_track_matches(action, "user-7")

        command = seen["command"]
        assert isinstance(command, RelinkConnectorTrackCommand)
        assert command.mapping_id == mapping_id
        assert command.new_track_id == new_id
        assert command.current_track_id == old_id
        assert command.user_id == "user-7"
        assert seen["user_id"] == "user-7"
        assert result["status"] == "confirmed"
        assert result["operation"] == "relink"
        assert result["old_track_id"] == str(old_id)
        assert result["new_track_id"] == str(new_id)

    @pytest.mark.parametrize("orphaned", [True, False], ids=["orphan", "no-orphan"])
    async def test_unlink_commits_and_reports_the_orphan(
        self, monkeypatch: pytest.MonkeyPatch, orphaned: bool
    ) -> None:
        mapping_id, current_id, orphan_id = uuid4(), uuid4(), uuid4()
        seen = _capture(
            monkeypatch,
            UnlinkConnectorTrackUseCase,
            UnlinkConnectorTrackResult(
                deleted_mapping_id=mapping_id,
                orphan_track_id=orphan_id if orphaned else None,
            ),
        )
        action = await _action({
            "operation": "unlink",
            "mapping_id": str(mapping_id),
            "current_track_id": str(current_id),
        })

        result = await matches_write.exec_manage_track_matches(action, "user-7")

        command = seen["command"]
        assert isinstance(command, UnlinkConnectorTrackCommand)
        assert command.mapping_id == mapping_id
        assert command.current_track_id == current_id
        assert command.user_id == "user-7"
        assert result == {
            "status": "confirmed",
            "operation": "unlink",
            "description": "Manage match",
            "deleted_mapping_id": str(mapping_id),
            "orphan_track_id": str(orphan_id) if orphaned else None,
        }

    async def test_set_primary_commits_without_result_object(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # SetPrimaryMappingUseCase.execute returns None — the confirmation echoes
        # the committed ids.
        mapping_id, track_id = uuid4(), uuid4()
        monkeypatch.setattr(_common, "execute_use_case", _fake_use_case_runner(None))
        action = await _action({
            "operation": "set_primary",
            "mapping_id": str(mapping_id),
            "track_id": str(track_id),
        })

        result = await matches_write.exec_manage_track_matches(action, "default")

        assert result["status"] == "confirmed"
        assert result["operation"] == "set_primary"
        assert result["mapping_id"] == str(mapping_id)
        assert result["track_id"] == str(track_id)

    @pytest.mark.parametrize("review_action", ["accept", "reject"])
    async def test_resolve_review_commits_the_chosen_action(
        self, monkeypatch: pytest.MonkeyPatch, review_action: str
    ) -> None:
        review = MatchReview(
            track_id=uuid4(),
            connector_name="spotify",
            connector_track_id=uuid4(),
            match_method="direct",
            confidence=80,
            match_weight=1.0,
            status="accepted",
            user_id=TEST_USER_ID,
        )
        seen = _capture(
            monkeypatch,
            ResolveMatchReviewUseCase,
            ResolveMatchReviewResult(review=review, mapping_created=True),
        )
        action = await _action({
            "operation": "resolve_review",
            "review_id": str(review.id),
            "action": review_action,
        })

        result = await matches_write.exec_manage_track_matches(action, "user-7")

        command = seen["command"]
        assert isinstance(command, ResolveMatchReviewCommand)
        assert command.review_id == review.id
        assert command.action == review_action
        assert command.user_id == "user-7"
        assert result["operation"] == "resolve_review"
        assert result["review_status"] == "accepted"
        assert result["mapping_created"] is True

    async def test_unreject_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        connector_track_id, candidate_track_id = uuid4(), uuid4()
        seen = _capture(
            monkeypatch,
            UnrejectMappingCandidateUseCase,
            UnrejectMappingCandidateResult(
                connector_name="spotify",
                connector_track_id=connector_track_id,
                candidate_track_id=candidate_track_id,
            ),
        )
        action = await _action({
            "operation": "unreject",
            "connector_track_id": str(connector_track_id),
            "candidate_track_id": str(candidate_track_id),
        })

        result = await matches_write.exec_manage_track_matches(action, "user-7")

        command = seen["command"]
        assert isinstance(command, UnrejectMappingCandidateCommand)
        assert command.connector_track_id == connector_track_id
        assert command.candidate_track_id == candidate_track_id
        # The withdrawal is attributed to the assistant, not a human edit.
        assert command.source == "assistant"
        assert command.user_id == "user-7"
        assert result["status"] == "confirmed"
        assert result["operation"] == "unreject"
        assert result["connector_name"] == "spotify"
        assert result["connector_track_id"] == str(connector_track_id)
        assert result["candidate_track_id"] == str(candidate_track_id)

    async def test_not_found_at_confirm_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory, user_id: str | None = None):
            raise NotFoundError("gone")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await _action({
            "operation": "set_primary",
            "mapping_id": str(uuid4()),
            "track_id": str(uuid4()),
        })

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await matches_write.exec_manage_track_matches(action, "default")

    async def test_value_error_at_confirm_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory, user_id: str | None = None):
            raise ValueError("already resolved")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await _action({
            "operation": "resolve_review",
            "review_id": str(uuid4()),
            "action": "reject",
        })

        with pytest.raises(ToolExecutionError, match="no longer valid"):
            await matches_write.exec_manage_track_matches(action, "default")

    async def test_malformed_mapping_id_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A details payload the executor cannot parse fails with a corrective
        # message instead of a bare ValueError, and never reaches the use case.
        async def _raise(factory, user_id: str | None = None):
            raise AssertionError("not reached")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await _action({
            "operation": "set_primary",
            "mapping_id": "not-a-uuid",
            "track_id": str(uuid4()),
        })

        with pytest.raises(ToolExecutionError, match="must be a UUID string"):
            await matches_write.exec_manage_track_matches(action, "default")
