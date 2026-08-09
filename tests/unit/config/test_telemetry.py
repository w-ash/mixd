"""Unit tests for the chunk probe's contextvar mechanics."""

from src.config.telemetry import (
    ChunkProbe,
    current_probe,
    measure_chunk,
    operation_scope,
    phase,
    record_api_call,
    record_statement,
)


class TestNoAmbientProbe:
    def test_recorders_are_a_no_op(self):
        record_statement(1_000)
        record_api_call(1_000)

        assert current_probe() is None


class TestMeasureChunk:
    async def test_sets_and_resets_the_probe(self):
        async with measure_chunk() as probe:
            assert current_probe() is probe

        assert current_probe() is None

    async def test_nested_spans_do_not_leak(self):
        async with measure_chunk() as outer:
            record_statement(1_000)
            async with measure_chunk() as inner:
                record_statement(2_000)
            assert current_probe() is outer
            record_statement(3_000)

        assert outer.statements == 2
        assert inner.statements == 1

    async def test_stamps_wall_time(self):
        async with measure_chunk() as probe:
            pass

        assert probe.wall_ns > 0


class TestOperationScope:
    async def test_outermost_wins(self):
        async with measure_chunk() as probe:
            with operation_scope("outer"):
                record_statement(1_000)
                with operation_scope("inner"):
                    record_statement(2_000)

        assert probe.by_operation == {"outer": (2, 3_000)}

    async def test_sibling_scopes_are_separate(self):
        async with measure_chunk() as probe:
            with operation_scope("a"):
                record_statement(1_000)
            with operation_scope("b"):
                record_statement(2_000)

        assert probe.by_operation == {"a": (1, 1_000), "b": (1, 2_000)}

    async def test_unscoped_statements_bucket_to_bare(self):
        async with measure_chunk() as probe:
            record_statement(1_000)

        assert probe.by_operation == {"_bare": (1, 1_000)}


class TestPhase:
    async def test_accumulates_across_re_entry(self):
        async with measure_chunk() as probe:
            async with phase("api"):
                pass
            first = probe.phases["api"]
            async with phase("api"):
                pass

        assert probe.phases["api"] > first

    async def test_is_a_no_op_without_a_probe(self):
        async with phase("api"):
            pass

        assert current_probe() is None


class TestDerivedFields:
    def test_rtt_is_zero_without_statements(self):
        assert ChunkProbe().rtt_ms == 0.0

    def test_rtt_is_mean_db_time(self):
        probe = ChunkProbe(statements=4, db_ns=8_000_000)

        assert probe.rtt_ms == 2.0

    def test_phase_ms_defaults_to_zero_for_unseen_phases(self):
        assert ChunkProbe().phase_ms("nope") == 0.0

    def test_top_operations_ranks_by_time_and_folds_the_rest(self):
        probe = ChunkProbe(
            by_operation={
                "slow": (1, 5_000_000),
                "mid": (2, 3_000_000),
                "fast": (3, 1_000_000),
                "faster": (4, 500_000),
            }
        )

        assert probe.top_operations(limit=2) == {
            "slow": [1, 5],
            "mid": [2, 3],
            "other": [7, 2],  # fast + faster, 1.5ms rounded
        }

    def test_top_operations_omits_other_when_nothing_is_folded(self):
        probe = ChunkProbe(by_operation={"only": (1, 1_000_000)})

        assert probe.top_operations() == {"only": [1, 1]}
