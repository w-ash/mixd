"""Unit tests for BatchTagTracksUseCase.

The critical property is atomicity: an invalid raw_tag must raise
BEFORE any repo write, so the user never ends up with half-tagged
tracks. Also verifies track_id deduplication and the reported counts.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest

from src.application.use_cases.batch_tag_tracks import (
    BatchTagTracksCommand,
    BatchTagTracksUseCase,
)
from tests.fixtures import make_mock_uow


def _cmd(
    track_ids,
    raw_tag="Mood:Chill",
    source="manual",
    user_id="default",
    tagged_at=None,
) -> BatchTagTracksCommand:
    return BatchTagTracksCommand(
        user_id=user_id,
        track_ids=track_ids,
        raw_tag=raw_tag,
        source=source,
        tagged_at=tagged_at or datetime.now(UTC),
    )


def _uow(*, add_tags_result=None):
    uow = make_mock_uow()
    if add_tags_result is not None:
        uow.get_tag_repository().add_tags.side_effect = None
        uow.get_tag_repository().add_tags.return_value = add_tags_result
    return uow


class TestHappyPath:
    async def test_batch_tag_applies_once(self) -> None:
        ids = [uuid7() for _ in range(3)]
        uow = _uow(
            add_tags_result=[
                # Pretend repo inserted all three.
                _stub_tag(tid)
                for tid in ids
            ]
        )

        result = await BatchTagTracksUseCase().execute(_cmd(ids), uow)

        assert result.tag == "mood:chill"
        assert result.requested == 3
        assert result.tagged == 3

        tag_repo = uow.get_tag_repository()
        tag_repo.add_tags.assert_called_once()
        tag_repo.add_events.assert_called_once()

        # One bulk write call, not one-per-track.
        written = tag_repo.add_tags.call_args[0][0]
        assert len(written) == 3

    async def test_dedupes_track_ids(self) -> None:
        shared = uuid7()
        uow = _uow(add_tags_result=[_stub_tag(shared)])

        result = await BatchTagTracksUseCase().execute(
            _cmd([shared, shared, shared]), uow
        )

        # Dedup happens BEFORE the repo call, so add_tags sees one entity.
        written = uow.get_tag_repository().add_tags.call_args[0][0]
        assert len(written) == 1
        assert result.requested == 1

    async def test_skipped_duplicates_reported(self) -> None:
        """Repo returns fewer rows than requested — ``tagged`` reflects it."""
        ids = [uuid7() for _ in range(3)]
        # Repo reports only 1 actually inserted (other two pre-existed).
        uow = _uow(add_tags_result=[_stub_tag(ids[0])])

        result = await BatchTagTracksUseCase().execute(_cmd(ids), uow)

        assert result.requested == 3
        assert result.tagged == 1


class TestAtomicity:
    """Invalid tag aborts the whole batch with zero repo writes."""

    async def test_invalid_tag_writes_nothing(self) -> None:
        uow = _uow()
        cmd = _cmd([uuid7() for _ in range(5)], raw_tag="cafe!")

        with pytest.raises(ValueError):
            await BatchTagTracksUseCase().execute(cmd, uow)

        uow.get_tag_repository().add_tags.assert_not_called()
        uow.get_tag_repository().add_events.assert_not_called()

    async def test_empty_batch_still_validates_tag(self) -> None:
        uow = _uow()

        with pytest.raises(ValueError):
            await BatchTagTracksUseCase().execute(_cmd([], raw_tag="bad!"), uow)

    async def test_empty_batch_valid_tag_returns_zero(self) -> None:
        uow = _uow()

        result = await BatchTagTracksUseCase().execute(
            _cmd([], raw_tag="Mood:Chill"), uow
        )

        assert result == result.__class__(tag="mood:chill", requested=0, tagged=0)
        uow.get_tag_repository().add_tags.assert_not_called()


class TestNormalization:
    async def test_returns_normalized_tag(self) -> None:
        ids = [uuid7()]
        uow = _uow(add_tags_result=[_stub_tag(ids[0])])

        result = await BatchTagTracksUseCase().execute(
            _cmd(ids, raw_tag="  Mood:Chill  "), uow
        )

        assert result.tag == "mood:chill"


def _stub_tag(track_id: UUID):
    """Minimal stand-in for a TrackTag in repo return values."""
    from src.domain.entities.tag import TrackTag

    return TrackTag.create(
        user_id="default",
        track_id=track_id,
        raw_tag="mood:chill",
        tagged_at=datetime.now(UTC),
        source="manual",
    )
