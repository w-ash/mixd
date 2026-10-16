"""The test-vacuity checker and its baseline ratchet.

Each case feeds a small test module through the scanner and pins the exact
V-code. A flagged and a clean snippet per code guard both directions: a checker
that flags too much blocks honest work, and one that flags too little lets
vacuous tests back in.
"""

import json
from pathlib import Path
import subprocess
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
    pytest.param(
        """
        def test_it(result):
            assert result
        """,
        "V3",
        id="V3-truthiness-of-a-fixture",
    ),
    pytest.param(
        """
        def _assert_saved(repo):
            repo.save.assert_called_once()

        def test_it(repo):
            run(repo)
            _assert_saved(repo)
        """,
        "V2",
        id="V2-helper-with-only-bare-mock-calls",
    ),
    pytest.param(
        """
        import subprocess

        def test_it():
            subprocess.check_call(["make"])
        """,
        "V1",
        id="V1-check-prefix-on-a-foreign-module",
    ),
    pytest.param(
        """
        from unittest.mock import patch

        from pkg.calc import total

        def test_it():
            with patch("pkg.calc.total"):
                assert total() == 1
        """,
        "V7",
        id="V7-patches-the-imported-function-it-calls",
    ),
    pytest.param(
        """
        import pytest
        from unittest.mock import patch

        @pytest.fixture
        def use_case():
            return UseCase()

        def test_it(use_case):
            with patch.object(UseCase, "_load"):
                assert use_case.execute() == 1
        """,
        "V7",
        id="V7-private-patch-on-a-fixture-instance",
    ),
    pytest.param(
        """
        from unittest.mock import patch

        class TestIt:
            def setup_method(self):
                self.use_case = UseCase()

            def test_it(self):
                with patch.object(UseCase, "_load"):
                    assert self.use_case.execute() == 1
        """,
        "V7",
        id="V7-private-patch-on-a-setup-method-instance",
    ),
    pytest.param(
        """
        import pytest
        from unittest.mock import patch

        @pytest.fixture
        def client():
            built = Client()
            return built

        @pytest.fixture
        def fast_client(client):
            client.wait = 0
            return client

        def test_it(fast_client):
            with patch.object(Client, "_fetch_impl"):
                assert fast_client.fetch() == 1
        """,
        "V7",
        id="V7-private-patch-on-a-fixture-passed-through",
    ),
    pytest.param(
        """
        def test_it():
            result = run()
            assert result.count == 3
            assert result.duration_ms >= 0
        """,
        "V9",
        id="V9-result-duration-bound",
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
    pytest.param(
        """
        def test_it():
            result = finish()
            assert result.status.value == "completed"
        """,
        id="value-of-a-non-enum-attribute",
    ),
    pytest.param(
        """
        def test_it(repo):
            outcome = run(repo)
            assert (outcome, repo.commit.call_count) == ("ok", 1)
        """,
        id="mock-count-beside-an-outcome",
    ),
    pytest.param(
        """
        def test_it():
            valid = validate("x")
            assert valid
        """,
        id="truthiness-of-a-predicate-result",
    ),
    pytest.param(
        """
        from unittest.mock import patch

        from datetime import datetime

        def test_it():
            with patch("pkg.thing.datetime"):
                assert stamp(datetime(2024, 1, 1)) == "2024"
        """,
        id="patches-the-clock-in-the-subject-module",
    ),
    pytest.param(
        """
        from unittest.mock import patch

        from other import total

        def test_it():
            with patch("pkg.calc.total"):
                assert total() == 1
        """,
        id="patch-target-in-another-module",
    ),
    pytest.param(
        """
        from unittest.mock import patch

        def test_it():
            with patch.object(Client, "_request_json"):
                assert Client().fetch() == 1
        """,
        id="patches-the-transport-method",
    ),
    pytest.param(
        """
        def test_it():
            track = load()
            assert track.title == "a"
            assert track.duration_ms > 0
        """,
        id="data-field-in-milliseconds",
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

    def test_same_text_over_different_class_subjects_is_distinct(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            """
            class TestSpotify:
                subject = SpotifyConnector

                def test_a(self):
                    assert self.subject().name == "x"

            class TestLastfm:
                def setup_method(self):
                    self.subject = LastfmConnector

                def test_a(self):
                    assert self.subject().name == "x"
            """,
        )

        assert _codes(tmp_path) == set()

    def test_same_text_over_a_shared_base_attribute_is_a_duplicate(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            """
            class Base:
                subject = SpotifyConnector

            class TestOne(Base):
                def test_a(self):
                    assert self.subject().name == "x"

            class TestTwo(Base):
                def test_a(self):
                    assert self.subject().name == "x"
            """,
        )

        assert _codes(tmp_path) == {("test_a", "DUP")}


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


def _git(root: Path, *args: str) -> None:
    identity = ["-c", "user.name=t", "-c", "user.email=t@t"]
    _ = subprocess.run(["git", "-C", str(root), *identity, *args], check=True)


class TestBaseRef:
    """``--base-ref`` stops a change from adding its own tests to the baseline."""

    def _commit_baseline(self, root: Path, tests: dict[str, list[str]]) -> None:
        path = root / "tests" / ".vacuity_baseline.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"tests": tests}))
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "base")

    def _grow(self, root: Path, capsys: pytest.CaptureFixture[str]) -> int:
        assert main(["--root", str(root), "--update-baseline"]) == 0
        _ = capsys.readouterr()
        return main(["--root", str(root), "--base-ref", "HEAD"])

    def test_a_new_id_in_a_modified_file_fails_and_names_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path, "def test_old():\n    assert run() == 1\n")
        self._commit_baseline(tmp_path, {})
        _write(tmp_path, "def test_old():\n    run()\n")

        assert self._grow(tmp_path, capsys) == 1
        out = capsys.readouterr().out
        assert "BASELINE GREW: V1 tests/unit/test_sample.py::test_old" in out

    def test_a_new_id_in_an_untracked_file_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._commit_baseline(tmp_path, {})
        _write(tmp_path, "def test_new():\n    run()\n", "test_new.py")

        assert self._grow(tmp_path, capsys) == 1
        out = capsys.readouterr().out
        assert "BASELINE GREW: V1 tests/unit/test_new.py::test_new" in out

    def test_a_new_id_in_a_renamed_file_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path, "def test_old():\n    run()\n", "test_before.py")
        self._commit_baseline(tmp_path, {"V1": ["tests/unit/test_before.py::test_old"]})
        _git(tmp_path, "mv", "tests/unit/test_before.py", "tests/unit/test_after.py")

        assert self._grow(tmp_path, capsys) == 1
        out = capsys.readouterr().out
        assert "BASELINE GREW: V1 tests/unit/test_after.py::test_old" in out

    def test_a_new_id_in_an_untouched_file_passes_with_a_note(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A sharper checker may flag an old test that this change did not touch."""
        _write(tmp_path, "def test_old():\n    run()\n")
        self._commit_baseline(tmp_path, {})

        assert self._grow(tmp_path, capsys) == 0
        out = capsys.readouterr().out
        assert (
            "BASELINE ADDED (untouched file): V1 tests/unit/test_sample.py::test_old"
            in out
        )
        assert "BASELINE GREW" not in out

    def test_a_baseline_equal_to_the_base_passes(self, tmp_path: Path) -> None:
        _write(tmp_path, "def test_old():\n    run()\n")
        self._commit_baseline(tmp_path, {"V1": ["tests/unit/test_sample.py::test_old"]})

        assert main(["--root", str(tmp_path), "--base-ref", "HEAD"]) == 0

    def test_a_base_without_a_baseline_skips_the_check(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path, "def test_old():\n    run()\n")
        self._commit_baseline(tmp_path, {})
        (tmp_path / "tests" / ".vacuity_baseline.json").unlink()
        _git(tmp_path, "commit", "-qam", "drop baseline")
        assert main(["--root", str(tmp_path), "--update-baseline"]) == 0
        _ = capsys.readouterr()

        assert main(["--root", str(tmp_path), "--base-ref", "HEAD"]) == 0
        assert "no tests/.vacuity_baseline.json at HEAD" in capsys.readouterr().out

    def test_an_unknown_ref_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path, "def test_old():\n    run()\n")
        self._commit_baseline(tmp_path, {"V1": ["tests/unit/test_sample.py::test_old"]})

        assert main(["--root", str(tmp_path), "--base-ref", "no-such-ref"]) == 1
        assert "unknown ref no-such-ref" in capsys.readouterr().out

    @pytest.mark.parametrize("ref", ["-x", "--output={out}"])
    def test_a_ref_that_starts_with_a_dash_fails_before_git_sees_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], ref: str
    ) -> None:
        """A ref that git would read as an option is rejected, not passed on."""
        _write(tmp_path, "def test_old():\n    run()\n")
        self._commit_baseline(tmp_path, {"V1": ["tests/unit/test_sample.py::test_old"]})
        out_file = tmp_path / "injected"
        ref = ref.format(out=out_file)

        assert main(["--root", str(tmp_path), f"--base-ref={ref}"]) == 1
        out = capsys.readouterr().out
        assert f"VACUITY FAIL: ref may not start with '-': {ref}" in out
        assert "unknown ref" not in out
        assert not out_file.exists()


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
