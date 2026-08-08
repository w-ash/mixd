"""Unit tests for the play-import pipeline's tenancy guard and persist contract."""

from datetime import UTC, datetime
from typing import override

import pytest

from src.domain.entities import ConnectorTrackPlay
from src.domain.entities.progress import ProgressEmitter
from src.domain.repositories.play import LastfmImportParams
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.services.base_play_importer import (
    BasePlayImporter,
    _require_uniform_tenancy,
)
from tests.fixtures.mocks import make_mock_uow


def _make_play(track_name: str, **overrides: str) -> ConnectorTrackPlay:
    """Build a minimal valid connector play.

    ``user_id`` is left to the entity's ``"default"`` attrs default unless an
    override is given — that default is the realistic failure shape a chunked
    importer produces when it forgets to stamp a row.
    """
    return ConnectorTrackPlay(
        artist_name="Test Artist",
        track_name=track_name,
        played_at=datetime(2024, 6, 15, 14, 30, tzinfo=UTC),
        service="lastfm",
        **overrides,
    )


class TestRequireUniformTenancy:
    """Every ledger row must carry the importing user's ID before persistence."""

    def test_uniformly_stamped_batch_passes(self) -> None:
        """A batch stamped end-to-end with the importing user is accepted."""
        track_plays = [
            _make_play("First", user_id="u1"),
            _make_play("Second", user_id="u1"),
            _make_play("Third", user_id="u1"),
        ]

        _require_uniform_tenancy(track_plays, "u1", "LastfmPlayImporter")

    def test_empty_batch_passes(self) -> None:
        """No rows means no tenancy to violate."""
        _require_uniform_tenancy([], "u1", "LastfmPlayImporter")

    def test_mixed_stamp_batch_raises_naming_the_offending_index(self) -> None:
        """A defaulted row past the first is caught, not waved through."""
        track_plays = [
            _make_play("First", user_id="u1"),
            _make_play("Second"),  # takes the "default" attrs default
            _make_play("Third", user_id="u1"),
        ]

        with pytest.raises(RuntimeError) as exc_info:
            _require_uniform_tenancy(track_plays, "u1", "LastfmPlayImporter")

        message = str(exc_info.value)
        assert "play 1" in message
        assert "'default'" in message
        assert "LastfmPlayImporter" in message


class _StubImporter(BasePlayImporter[ConnectorTrackPlay, LastfmImportParams]):
    """Minimal importer whose fetch optionally persists its own chunk."""

    operation_name = "Stub Import"

    def __init__(self, *, persists_during_fetch: bool, user_id: str = "u1") -> None:
        self.persists_plays_during_fetch = persists_during_fetch
        self._plays = [_make_play("First", user_id=user_id)]

    @override
    async def _fetch_data(
        self,
        params: LastfmImportParams,
        *,
        uow: UnitOfWorkProtocol,
        user_id: str,
        batch_id: str,
        import_timestamp: datetime,
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
    ) -> list[ConnectorTrackPlay]:
        if self.persists_plays_during_fetch:
            await self._persist_fetched_chunk(self._plays, uow, user_id=user_id)
        return self._plays

    @override
    async def _process_data(
        self,
        raw_data: list[ConnectorTrackPlay],
        *,
        user_id: str,
        batch_id: str,
        import_timestamp: datetime,
    ) -> list[ConnectorTrackPlay]:
        return raw_data

    @override
    async def _handle_checkpoints(
        self,
        raw_data: list[ConnectorTrackPlay],
        params: LastfmImportParams,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        return None


def _insert_calls(uow) -> int:
    repo = uow.get_connector_play_repository()
    return repo.bulk_insert_connector_plays.await_count


def _uow_with_ledger(*, inserted: int, duplicates: int):
    """Mock UoW whose ledger write reports a fixed (inserted, duplicates).

    The counts are the repository's to give — its ON CONFLICT is the only thing
    that knows how many rows were already stored — so a test that wants to see
    what an importer *reports* has to say what the ledger *accepted*.
    """
    uow = make_mock_uow()
    uow.get_connector_play_repository().bulk_insert_connector_plays.return_value = (
        inserted,
        duplicates,
    )
    return uow


class TestPersistsPlaysDuringFetch:
    """The run-end save belongs to whoever did not already write the rows.

    A source that commits a resume cursor mid-fetch has to persist each chunk
    itself (the checkpoint may never lead the data); the pipeline must then not
    re-stream the whole span behind it, for zero rows gained.
    """

    async def test_fetch_persisted_span_is_not_saved_again(self) -> None:
        uow = _uow_with_ledger(inserted=1, duplicates=0)
        importer = _StubImporter(persists_during_fetch=True)

        result, plays = await importer.import_data(
            LastfmImportParams(), uow=uow, user_id="u1"
        )

        assert _insert_calls(uow) == 1
        assert result.summary_metrics.get("imported") == len(plays)

    async def test_ordinary_importer_still_saved_by_the_pipeline(self) -> None:
        uow = _uow_with_ledger(inserted=1, duplicates=0)
        importer = _StubImporter(persists_during_fetch=False)

        result, plays = await importer.import_data(
            LastfmImportParams(), uow=uow, user_id="u1"
        )

        assert _insert_calls(uow) == 1
        assert result.summary_metrics.get("imported") == len(plays)

    async def test_chunk_tenancy_is_verified_before_the_write(self) -> None:
        """The guard runs on the chunk, not only on the assembled span —
        otherwise a mis-stamped row is already in the ledger when it fires."""
        uow = _uow_with_ledger(inserted=1, duplicates=0)
        importer = _StubImporter(persists_during_fetch=True, user_id="someone-else")

        result, _plays = await importer.import_data(
            LastfmImportParams(), uow=uow, user_id="u1"
        )

        assert result.is_failure
        assert _insert_calls(uow) == 0


class TestReportedCountsComeFromTheLedger:
    """What a run reports imported is what the ledger accepted, either path.

    The mid-fetch path used to fabricate ``(len(track_plays), 0)`` at the run
    end while the chunk writes' real answers were thrown away — a re-fetched
    day's ON CONFLICT no-ops read back as fresh imports.
    """

    async def test_mid_fetch_writes_report_their_duplicates(self) -> None:
        uow = _uow_with_ledger(inserted=0, duplicates=1)
        importer = _StubImporter(persists_during_fetch=True)

        result, plays = await importer.import_data(
            LastfmImportParams(), uow=uow, user_id="u1"
        )

        assert len(plays) == 1  # the span was streamed...
        assert result.summary_metrics.get("imported") == 0  # ...and stored nothing
        assert result.summary_metrics.get("duplicates") == 1

    async def test_persist_chunk_hands_the_counts_back_to_its_caller(self) -> None:
        """The chunk write's own return, for sources accumulating per chunk."""
        uow = _uow_with_ledger(inserted=3, duplicates=2)
        importer = _StubImporter(persists_during_fetch=True)

        counts = await importer._persist_fetched_chunk(
            [_make_play("First", user_id="u1")], uow, user_id="u1"
        )

        assert counts == (3, 2)

    async def test_run_totals_do_not_leak_between_runs(self) -> None:
        """A second run on the same importer starts from zero, not doubled."""
        uow = _uow_with_ledger(inserted=1, duplicates=0)
        importer = _StubImporter(persists_during_fetch=True)

        _ = await importer.import_data(LastfmImportParams(), uow=uow, user_id="u1")
        result, _plays = await importer.import_data(
            LastfmImportParams(), uow=uow, user_id="u1"
        )

        assert result.summary_metrics.get("imported") == 1
