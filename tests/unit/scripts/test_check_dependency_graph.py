"""The dependency-graph drift alarm.

This check is the only thing standing between "GitHub's ingestion broke" and
"nobody notices for two months" — which is exactly what happened in 2026. Its
value is entirely in reporting drift accurately, so these tests pin the
comparison and the lockfile reading rather than the HTTP plumbing.
"""

import pytest

from scripts.check_dependency_graph import (
    LOCKFILE,
    compare,
    locked_versions,
    normalize_name,
)


class TestNaming:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("PyJWT", "pyjwt"),
            ("python_multipart", "python-multipart"),
            ("zope.interface", "zope-interface"),
        ],
    )
    def test_normalizes_per_pep503(self, raw: str, expected: str) -> None:
        """Lock names and PURL names must land in the same spelling to compare."""
        assert normalize_name(raw) == expected


class TestCompare:
    def test_matching_graph_reports_nothing(self) -> None:
        locked = {"starlette": "1.3.1", "pyjwt": "2.13.0"}

        assert compare(locked, dict(locked)) == ([], [], [])

    def test_version_mismatch_is_reported(self) -> None:
        """The exact symptom of the 2026 freeze: right packages, stale versions."""
        missing, mismatched, extra = compare(
            {"starlette": "1.3.1"},
            {"starlette": "1.0.0"},
        )

        assert missing == []
        assert mismatched == ["starlette: lock has 1.3.1, graph reports 1.0.0"]
        assert extra == []

    def test_package_absent_from_graph_is_reported(self) -> None:
        missing, mismatched, extra = compare({"mcp": "2.0.0b1"}, {})

        assert missing == ["mcp 2.0.0b1"]
        assert mismatched == []

    def test_extra_graph_packages_are_informational(self) -> None:
        """Stale leftovers are surfaced but must not fail the build."""
        missing, mismatched, extra = compare({}, {"retired-pkg": "1.0.0"})

        assert (missing, mismatched) == ([], [])
        assert extra == ["retired-pkg 1.0.0"]


class TestLockedVersions:
    def test_reads_the_real_lockfile(self) -> None:
        locked = locked_versions()

        assert len(locked) > 50
        assert "mixd" not in locked, "the editable root is not its own dependency"
        assert all(version for version in locked.values())

    def test_versions_match_the_lockfile_contents(self) -> None:
        """Spot-check against the file so a parser change can't silently pass."""
        import tomllib

        raw = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))
        expected = {
            pkg["name"]: pkg["version"]
            for pkg in raw["package"]
            if pkg["name"] in {"starlette", "pyjwt", "cryptography"}
        }
        locked = locked_versions()

        for name, version in expected.items():
            assert locked[name] == version
