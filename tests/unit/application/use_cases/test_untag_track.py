"""Unit tests for UntagTrackUseCase.

Verifies the tag is normalized before lookup, no event fires when the
row didn't exist, and NotFoundError surfaces when the track is missing.
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.application.use_cases.untag_track import (
    UntagTrackCommand,
    UntagTrackUseCase,
)
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_track


def _cmd(
    track_id=None,
    raw_tag="Mood:Chill",
    source="manual",
    user_id="default",
    tagged_at=None,
) -> UntagTrackCommand:
    return UntagTrackCommand(
        user_id=user_id,
        track_id=track_id or uuid7(),
        raw_tag=raw_tag,
        source=source,
        tagged_at=tagged_at or datetime.now(UTC),
    )


def _uow_for(track, *, removed_pairs=None):
    uow = make_mock_uow()
    uow.get_track_repository().get_track_by_id.return_value = track
    if removed_pairs is not None:
        uow.get_tag_repository().remove_tags.side_effect = None
        uow.get_tag_repository().remove_tags.return_value = removed_pairs
    return uow


class TestRemoveExisting:
    async def test_removes_and_emits_event(self) -> None:
        track = make_track()
        uow = _uow_for(track, removed_pairs=[(track.id, "mood:chill")])

        result = await UntagTrackUseCase().execute(
            _cmd(track_id=track.id, raw_tag="Mood:Chill"), uow
        )

        assert result.changed is True
        assert result.tag == "mood:chill"

        tag_repo = uow.get_tag_repository()
        tag_repo.remove_tags.assert_called_once_with(
            [(track.id, "mood:chill")], user_id="default"
        )
        tag_repo.add_events.assert_called_once()
        event = tag_repo.add_events.call_args[0][0][0]
        assert event.action == "remove"
        assert event.tag == "mood:chill"


class TestIdempotency:
    async def test_missing_tag_no_event(self) -> None:
        track = make_track()
        uow = _uow_for(track, removed_pairs=[])

        result = await UntagTrackUseCase().execute(
            _cmd(track_id=track.id, raw_tag="banger"), uow
        )

        assert result.changed is False
        uow.get_tag_repository().add_events.assert_not_called()


class TestErrors:
    async def test_invalid_tag_raises(self) -> None:
        track = make_track()
        uow = _uow_for(track)

        with pytest.raises(ValueError):
            await UntagTrackUseCase().execute(
                _cmd(track_id=track.id, raw_tag="cafe!"), uow
            )

        uow.get_tag_repository().remove_tags.assert_not_called()

    async def test_nonexistent_track_raises(self) -> None:
        uow = make_mock_uow()
        uow.get_track_repository().get_track_by_id.side_effect = NotFoundError(
            "Track not found"
        )

        with pytest.raises(NotFoundError):
            await UntagTrackUseCase().execute(_cmd(), uow)
