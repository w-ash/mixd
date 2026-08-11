"""The pre-calibration spotify_api start-shift backfill.

The whole value of this script is that it applies the *importer's* rule rather
than a lookalike, so these tests pin the two things that could still go wrong
around that shared call: the ledger→payload rehydration that feeds it, and the
predecessor walk that decides what "the play before this one" means once rows
from many polls sit in one table.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from scripts.backfill_spotify_api_start_shift import (
    ALREADY_STAMPED,
    NO_DURATION,
    NO_PREDECESSOR,
    OVERRUN,
    SHORTFALL,
    STAMPED,
    Observation,
    plan,
    to_observation,
)
from src.domain.matching.play_projection import START_SHIFT_MS_KEY

_BASE = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)
_THREE_MINUTES_MS = 180_000


def _row_id(n: int) -> UUID:
    return UUID(int=n)


def _raw_metadata(
    duration_ms: object = _THREE_MINUTES_MS, *, shift_ms: int | None = None
) -> dict[str, object]:
    service_metadata: dict[str, object] = {
        "track_uri": "spotify:track:6TnpH7XFHjhu5TQXlVs7SC",
        "duration_ms": duration_ms,
    }
    if shift_ms is not None:
        service_metadata[START_SHIFT_MS_KEY] = shift_ms
    return {"track_name": "rio", "service_metadata": service_metadata}


def _observation(
    n: int,
    *,
    offset_seconds: float,
    duration_ms: object = _THREE_MINUTES_MS,
    shift_ms: int | None = None,
) -> Observation:
    return to_observation(
        _row_id(n),
        _BASE + timedelta(seconds=offset_seconds),
        _raw_metadata(duration_ms, shift_ms=shift_ms),
        "spotify:track:6TnpH7XFHjhu5TQXlVs7SC",
    )


class TestToObservation:
    def test_reads_the_two_fields_the_rule_consults(self) -> None:
        observation = _observation(1, offset_seconds=0)

        assert observation.item.played_at == _BASE
        assert observation.item.track.duration_ms == _THREE_MINUTES_MS
        assert observation.item.track.id == "6TnpH7XFHjhu5TQXlVs7SC"
        assert not observation.already_stamped

    def test_recognizes_a_row_the_importer_already_stamped(self) -> None:
        assert _observation(1, offset_seconds=0, shift_ms=1234).already_stamped

    def test_a_non_integer_duration_becomes_the_refused_zero(self) -> None:
        """A junk duration must reach the shared rule as "no duration", not crash."""
        assert (
            _observation(1, offset_seconds=0, duration_ms="oops").item.track.duration_ms
            == 0
        )


class TestPlan:
    def test_first_row_has_no_predecessor_and_the_rest_stamp_their_duration(
        self,
    ) -> None:
        verdicts = plan([
            _observation(1, offset_seconds=0),
            _observation(2, offset_seconds=180),
            _observation(3, offset_seconds=360),
        ])

        assert [v.reason for v in verdicts] == [NO_PREDECESSOR, STAMPED, STAMPED]
        assert [v.shift_ms for v in verdicts] == [
            None,
            _THREE_MINUTES_MS,
            _THREE_MINUTES_MS,
        ]

    def test_truncated_play_is_refused_as_a_shortfall(self) -> None:
        """Stamping a full track length onto a play cut off at 60s would move it
        two minutes away from where it actually started."""
        verdicts = plan([
            _observation(1, offset_seconds=0),
            _observation(2, offset_seconds=60),
        ])

        assert verdicts[1].reason == SHORTFALL
        assert verdicts[1].shift_ms is None

    def test_idle_gap_before_playback_is_refused_as_an_overrun(self) -> None:
        """Also the poll-boundary shape: when the true predecessor was never
        stored, the elapsed gap spans the missing play and overruns."""
        verdicts = plan([
            _observation(1, offset_seconds=0),
            _observation(2, offset_seconds=3600),
        ])

        assert verdicts[1].reason == OVERRUN
        assert verdicts[1].shift_ms is None

    def test_missing_duration_is_refused(self) -> None:
        verdicts = plan([
            _observation(1, offset_seconds=0),
            _observation(2, offset_seconds=180, duration_ms=None),
        ])

        assert verdicts[1].reason == NO_DURATION
        assert verdicts[1].shift_ms is None

    def test_an_already_stamped_row_is_never_rewritten_but_still_leads(self) -> None:
        """Idempotency: the importer's own value wins, and the row remains the
        predecessor its successor's shift is measured against."""
        verdicts = plan([
            _observation(1, offset_seconds=0),
            _observation(2, offset_seconds=180, shift_ms=999),
            _observation(3, offset_seconds=360),
        ])

        assert [v.reason for v in verdicts] == [
            NO_PREDECESSOR,
            ALREADY_STAMPED,
            STAMPED,
        ]
        assert verdicts[1].shift_ms is None
        assert verdicts[2].shift_ms == _THREE_MINUTES_MS

    def test_replanning_after_a_write_stamps_nothing_further(self) -> None:
        """The second run of the script: every row it stamped now carries the
        key, and every refusal is deterministic in data the write never touched."""
        first_pass = plan([
            _observation(1, offset_seconds=0),
            _observation(2, offset_seconds=180),
            _observation(3, offset_seconds=3600),
        ])
        second_pass = plan([
            _observation(1, offset_seconds=0),
            _observation(2, offset_seconds=180, shift_ms=_THREE_MINUTES_MS),
            _observation(3, offset_seconds=3600),
        ])

        assert [v.reason for v in first_pass] == [NO_PREDECESSOR, STAMPED, OVERRUN]
        assert not [v for v in second_pass if v.shift_ms is not None]
