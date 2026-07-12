"""Unit tests for the ``manage_track_matches`` two-phase write dispatcher.

Covers the four operations (relink, unlink, set_primary, resolve_review) across
both phases: ``handle_manage_track_matches`` proposes (stores a pending action,
marking unlink destructive) and ``exec_manage_track_matches`` commits through the
matching use case. The pending-action store is swapped for a fresh instance per
test, and ``execute_use_case`` is monkeypatched on the module under test so the
commit path never touches a database.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, matches_write
from src.application.chat.pending_actions import PendingAction, PendingActionStore
from src.application.chat.protocols import ToolContext
from src.application.use_cases.relink_connector_track import RelinkConnectorTrackResult
from src.application.use_cases.resolve_match_review import ResolveMatchReviewResult
from src.application.use_cases.unlink_connector_track import UnlinkConnectorTrackResult
from src.domain.entities.match_review import MatchReview
from src.domain.exceptions import NotFoundError, ToolExecutionError

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> PendingActionStore:
    store = PendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _fake_use_case_runner(result: object):
    async def _run(factory, user_id: str | None = None):  # matches runner signature
        return result

    return _run


def _action(details: dict[str, object]) -> PendingAction:
    return PendingActionStore().create(
        user_id="default",
        tool_name="manage_track_matches",
        tool_input={},
        description="Manage match",
        details=details,
    )


class TestManageTrackMatchesPropose:
    async def test_relink_proposes_pending_confirmation(
        self, fresh_store: PendingActionStore
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

        action = fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_track_matches"

    async def test_unlink_is_destructive(self, fresh_store: PendingActionStore) -> None:
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
        self, fresh_store: PendingActionStore
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

    async def test_unknown_operation_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="operation"):
            await matches_write.handle_manage_track_matches(
                {"operation": "bogus"}, _CTX
            )

    async def test_relink_missing_field_rejected(
        self, fresh_store: PendingActionStore
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
        old_id, new_id = uuid4(), uuid4()
        monkeypatch.setattr(
            matches_write,
            "execute_use_case",
            _fake_use_case_runner(
                RelinkConnectorTrackResult(old_track_id=old_id, new_track_id=new_id)
            ),
        )
        action = _action({
            "operation": "relink",
            "mapping_id": str(uuid4()),
            "new_track_id": str(new_id),
            "current_track_id": str(old_id),
        })

        result = await matches_write.exec_manage_track_matches(action, "default")

        assert result["status"] == "confirmed"
        assert result["operation"] == "relink"
        assert result["old_track_id"] == str(old_id)
        assert result["new_track_id"] == str(new_id)

    async def test_unlink_commits_with_orphan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mapping_id, orphan_id = uuid4(), uuid4()
        monkeypatch.setattr(
            matches_write,
            "execute_use_case",
            _fake_use_case_runner(
                UnlinkConnectorTrackResult(
                    deleted_mapping_id=mapping_id, orphan_track_id=orphan_id
                )
            ),
        )
        action = _action({
            "operation": "unlink",
            "mapping_id": str(mapping_id),
            "current_track_id": str(uuid4()),
        })

        result = await matches_write.exec_manage_track_matches(action, "default")

        assert result["deleted_mapping_id"] == str(mapping_id)
        assert result["orphan_track_id"] == str(orphan_id)

    async def test_set_primary_commits_without_result_object(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # SetPrimaryMappingUseCase.execute returns None — the confirmation echoes
        # the committed ids.
        mapping_id, track_id = uuid4(), uuid4()
        monkeypatch.setattr(
            matches_write, "execute_use_case", _fake_use_case_runner(None)
        )
        action = _action({
            "operation": "set_primary",
            "mapping_id": str(mapping_id),
            "track_id": str(track_id),
        })

        result = await matches_write.exec_manage_track_matches(action, "default")

        assert result["status"] == "confirmed"
        assert result["operation"] == "set_primary"
        assert result["mapping_id"] == str(mapping_id)
        assert result["track_id"] == str(track_id)

    async def test_resolve_review_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        review = MatchReview(
            track_id=uuid4(),
            connector_name="spotify",
            connector_track_id=uuid4(),
            match_method="direct",
            confidence=80,
            match_weight=1.0,
            status="accepted",
        )
        monkeypatch.setattr(
            matches_write,
            "execute_use_case",
            _fake_use_case_runner(
                ResolveMatchReviewResult(review=review, mapping_created=True)
            ),
        )
        action = _action({
            "operation": "resolve_review",
            "review_id": str(review.id),
            "action": "accept",
        })

        result = await matches_write.exec_manage_track_matches(action, "default")

        assert result["operation"] == "resolve_review"
        assert result["review_status"] == "accepted"
        assert result["mapping_created"] is True

    async def test_not_found_at_confirm_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory, user_id: str | None = None):
            raise NotFoundError("gone")

        monkeypatch.setattr(matches_write, "execute_use_case", _raise)
        action = _action({
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

        monkeypatch.setattr(matches_write, "execute_use_case", _raise)
        action = _action({
            "operation": "resolve_review",
            "review_id": str(uuid4()),
            "action": "reject",
        })

        with pytest.raises(ToolExecutionError, match="no longer valid"):
            await matches_write.exec_manage_track_matches(action, "default")
