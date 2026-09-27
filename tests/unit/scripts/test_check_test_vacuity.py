"""The test-vacuity checker and its baseline ratchet.

Each case feeds a small test module through the scanner and pins the exact
V-code. A flagged and a clean snippet per code guard both directions: a checker
that flags too much blocks honest work, and one that flags too little lets
vacuous tests back in.
"""

import json
from pathlib import Path
from textwrap import dedent

import pytest

from scripts.check_test_vacuity import main, scan


def _write(root: Path, source: str, name: str = "test_sample.py") -> None:
    path = root / "tests" / "unit" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(source), encoding="utf-8")


def _codes(root: Path) -> set[tuple[str, str]]:
    return {(f.name, f.code) for f in scan(root)}


FLAGGED = [
    pytest.param(
        """
        def test_it():
            do_work()
        """,
        "V1",
        id="V1-no-check",
    ),
    pytest.param(
        """
        def test_it(repo):
            run(repo)
            repo.save.assert_called_once()
            assert repo.commit.call_count == 1
        """,
        "V2",
        id="V2-only-mock-calls",
    ),
    pytest.param(
        """
        def test_it(repo):
            run(repo)
            assert len(repo.save.call_args_list) == 2
            repo.push.assert_not_called()
        """,
        "V2",
        id="V2-call-list-length-is-a-count",
    ),
    pytest.param(
        """
        def test_it():
            result = build()
            assert result is not None
            assert isinstance(result, dict)
            assert len(result) > 0
        """,
        "V3",
        id="V3-existence-only",
    ),
    pytest.param(
        """
        import pytest

        def test_it():
            cmd = Command(name="a")
            with pytest.raises(AttributeError):
                cmd.name = "b"
        """,
        "V5",
        id="V5-frozen-raises",
    ),
    pytest.param(
        """
        def test_it():
            assert Color.RED.value == "red"
            assert Color.BLUE.value == 2
        """,
        "V5",
        id="V5-enum-value-pin",
    ),
    pytest.param(
        """
        from unittest.mock import patch

        def test_it():
            with patch("pkg.client.Client.fetch"):
                client = Client()
                assert client.fetch() == 1
        """,
        "V7",
        id="V7-patches-the-called-method",
    ),
    pytest.param(
        """
        from unittest.mock import patch

        def test_it():
            with patch.object(UseCase, "_load"):
                assert UseCase().execute() == 1
        """,
        "V7",
        id="V7-patches-own-private-method",
    ),
    pytest.param(
        """
        def test_it():
            result = run()
            assert result.count == 3
            assert result.execution_time_ms >= 0
        """,
        "V9",
        id="V9-timing-bound",
    ),
]

CLEAN = [
    pytest.param(
        """
        import pytest

        def test_it():
            with pytest.raises(ValueError, match="bad"):
                parse("x")
        """,
        id="raises-is-a-check",
    ),
    pytest.param(
        """
        def _assert_round_trip(value):
            assert decode(encode(value)) == value

        def test_it():
            _assert_round_trip(3)
        """,
        id="local-helper-asserts",
    ),
    pytest.param(
        """
        def verify(value):
            assert value == 3

        def test_it():
            verify(run())
        """,
        id="helper-found-by-body",
    ),
    pytest.param(
        """
        def _same(actual, expected):
            assert actual == expected

        def _round_trip(value):
            _same(decode(encode(value)), value)

        def test_it():
            _round_trip(3)
        """,
        id="helper-through-helper",
    ),
    pytest.param(
        """
        def test_it(repo):
            result = run(repo)
            repo.save.assert_called_once_with("a")
            assert result == ["a"]
        """,
        id="mock-call-plus-outcome",
    ),
    pytest.param(
        """
        def test_it(client):
            push(client, ["a", "b"])
            client.send.assert_called_once_with(["a", "b"])
            assert client.commit.call_count == 1
        """,
        id="mock-call-with-checked-args",
    ),
    pytest.param(
        """
        def test_it(client):
            push(client, ["a"])
            assert client.send.call_args.kwargs["ids"] == ["a"]
        """,
        id="mock-call-args-compared",
    ),
    pytest.param(
        """
        def test_it():
            result = build()
            assert result is not None
            assert result.name == "a"
        """,
        id="existence-plus-value",
    ),
    pytest.param(
        """
        from unittest.mock import patch

        def test_it():
            with patch("pkg.client.HttpClient.get"):
                assert Connector().fetch() == 1
        """,
        id="patches-a-boundary",
    ),
    pytest.param(
        """
        def test_it():
            result = run()
            assert result.execution_time_ms == 12
        """,
        id="exact-timing",
    ),
]


@pytest.mark.parametrize(("source", "code"), FLAGGED)
def test_flags_the_pattern(tmp_path: Path, source: str, code: str) -> None:
    _write(tmp_path, source)

    assert _codes(tmp_path) == {("test_it", code)}


@pytest.mark.parametrize("source", CLEAN)
def test_leaves_sound_tests_alone(tmp_path: Path, source: str) -> None:
    _write(tmp_path, source)

    assert _codes(tmp_path) == set()


class TestDuplicates:
    def test_identical_bodies_form_one_group(self, tmp_path: Path) -> None:
        body = """
        from pkg import total

        def test_{name}():
            \"\"\"{doc}\"\"\"
            assert total([1, 2]) == 3
        """
        _write(tmp_path, body.format(name="a", doc="first"), "test_one.py")
        _write(tmp_path, body.format(name="b", doc="second"), "test_two.py")

        dups = [(f.file, f.name, f.group) for f in scan(tmp_path) if f.code == "DUP"]

        assert dups == [
            ("tests/unit/test_one.py", "test_a", 1),
            ("tests/unit/test_two.py", "test_b", 1),
        ]

    def test_same_text_over_different_imports_is_distinct(self, tmp_path: Path) -> None:
        body = """
        from {module} import total

        def test_a():
            assert total([1, 2]) == 3
        """
        _write(tmp_path, body.format(module="pkg.ints"), "test_one.py")
        _write(tmp_path, body.format(module="pkg.floats"), "test_two.py")

        assert _codes(tmp_path) == set()


class TestRatchet:
    def _baseline(self, root: Path, tests: dict[str, list[str]]) -> None:
        path = root / "tests" / ".vacuity_baseline.json"
        path.write_text(json.dumps({"tests": tests}))

    def test_unlisted_test_fails_and_names_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path, "def test_new():\n    run()\n")
        self._baseline(tmp_path, {})

        assert main(["--root", str(tmp_path)]) == 1
        assert "tests/unit/test_sample.py:1 test_new V1" in capsys.readouterr().out

    def test_new_offender_fails_even_when_a_fixed_test_keeps_the_count(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path, "def test_new():\n    run()\n")
        self._baseline(tmp_path, {"V1": ["tests/unit/test_sample.py::test_fixed"]})

        assert main(["--root", str(tmp_path)]) == 1
        out = capsys.readouterr().out
        assert "VACUITY FAIL: V1 = 1 (baseline 1)" in out
        assert "tests/unit/test_sample.py:1 test_new V1" in out

    def test_listed_test_passes(self, tmp_path: Path) -> None:
        _write(tmp_path, "def test_old():\n    run()\n")
        self._baseline(tmp_path, {"V1": ["tests/unit/test_sample.py::test_old"]})

        assert main(["--root", str(tmp_path)]) == 0

    def test_fixed_test_passes_and_asks_for_a_baseline_update(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path, "def test_old():\n    assert run() == 1\n")
        self._baseline(tmp_path, {"V1": ["tests/unit/test_sample.py::test_old"]})

        assert main(["--root", str(tmp_path)]) == 0
        assert "--update-baseline" in capsys.readouterr().out

    def test_update_baseline_then_ratchet_passes(self, tmp_path: Path) -> None:
        _write(tmp_path, "def test_old():\n    run()\n")

        assert main(["--root", str(tmp_path), "--update-baseline"]) == 0
        saved = json.loads((tmp_path / "tests" / ".vacuity_baseline.json").read_text())
        assert saved == {
            "tests": {
                "V1": ["tests/unit/test_sample.py::test_old"],
                "V2": [],
                "V3": [],
                "V5": [],
                "V7": [],
                "V9": [],
                "DUP": [],
            }
        }
        assert main(["--root", str(tmp_path)]) == 0

    def test_nested_class_test_is_keyed_by_its_full_node_id(
        self, tmp_path: Path
    ) -> None:
        """Pytest ids a nested-class test as ``Outer::Inner::name``."""
        _write(
            tmp_path,
            "class TestOuter:\n"
            "    class TestInner:\n"
            "        def test_x(self):\n"
            "            run()\n",
        )
        self._baseline(
            tmp_path,
            {"V1": ["tests/unit/test_sample.py::TestOuter::TestInner::test_x"]},
        )

        assert main(["--root", str(tmp_path)]) == 0


def test_json_report_lists_class_and_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "class TestThing:\n    def test_x(self):\n        run()\n")

    assert main(["--root", str(tmp_path), "--report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "file": "tests/unit/test_sample.py",
            "line": 2,
            "name": "test_x",
            "class": "TestThing",
            "code": "V1",
        },
    ]
