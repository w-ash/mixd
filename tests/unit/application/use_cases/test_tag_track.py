"""Unit tests for TagTrackUseCase.

Verifies normalization happens before the DB write, the event log is
written only when the row actually inserts, and NotFoundError surfaces
when the track doesn't exist.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.application.use_cases.tag_track import (
    TagTrackCommand,
    TagTrackUseCase,
)
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_track


def _cmd(
    track_id=None,
    raw_tag="Mood:Chill",
    source="manual",
    user_id="default",
    tagged_at=None,
) -> TagTrackCommand:
    return TagTrackCommand(
        user_id=user_id,
        track_id=track_id or uuid7(),
        raw_tag=raw_tag,
        source=source,
        tagged_at=tagged_at or datetime.now(UTC),
    )


def _uow_for(track, *, inserted_tags=None):
    """Build a mock UoW where ``add_tags`` returns ``inserted_tags`` or input."""
    uow = make_mock_uow()
    uow.get_track_repository().get_track_by_id.return_value = track
    if inserted_tags is not None:
        uow.get_tag_repository().add_tags.side_effect = None
        uow.get_tag_repository().add_tags.return_value = inserted_tags
    return uow


class TestNewTag:
    async def test_normalizes_and_tags(self) -> None:
        track = make_track()
        uow = _uow_for(track)
        cmd = _cmd(track_id=track.id, raw_tag="Mood:Chill")

        result = await TagTrackUseCase().execute(cmd, uow)

        assert result.changed is True
        assert result.tag == "mood:chill"  # normalized

        tag_repo = uow.get_tag_repository()
        tag_repo.add_tags.assert_called_once()
        tag_repo.add_events.assert_called_once()

        written_tag = tag_repo.add_tags.call_args[0][0][0]
        assert written_tag.tag == "mood:chill"
        assert written_tag.namespace == "mood"
        assert written_tag.value == "chill"

    async def test_nonexistent_track_raises(self) -> None:
        uow = make_mock_uow()
        uow.get_track_repository().get_track_by_id.side_effect = NotFoundError(
            "Track not found"
        )

        with pytest.raises(NotFoundError):
            await TagTrackUseCase().execute(_cmd(), uow)


class TestIdempotency:
    """Re-tagging an already-tagged track writes no event."""

    async def test_duplicate_tag_no_event(self) -> None:
        track = make_track()
        # add_tags returns [] → row already existed, DO NOTHING kicked in
        uow = _uow_for(track, inserted_tags=[])
        cmd = _cmd(track_id=track.id, raw_tag="banger")

        result = await TagTrackUseCase().execute(cmd, uow)

        assert result.changed is False
        uow.get_tag_repository().add_events.assert_not_called()


class TestInvalidTag:
    async def test_invalid_raw_tag_raises_before_write(self) -> None:
        track = make_track()
        uow = _uow_for(track)
        cmd = _cmd(track_id=track.id, raw_tag="cafe!")

        with pytest.raises(ValueError):
            await TagTrackUseCase().execute(cmd, uow)

        uow.get_tag_repository().add_tags.assert_not_called()
        uow.get_tag_repository().add_events.assert_not_called()


class TestSource:
    """Command-provided source flows through to the entity and event."""

    async def test_source_propagates(self) -> None:
        track = make_track()
        uow = _uow_for(track)

        await TagTrackUseCase().execute(
            _cmd(track_id=track.id, source="service_import"), uow
        )

        tag = uow.get_tag_repository().add_tags.call_args[0][0][0]
        event = uow.get_tag_repository().add_events.call_args[0][0][0]
        assert tag.source == "service_import"
        assert event.source == "service_import"
