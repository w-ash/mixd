#!/usr/bin/env python3
"""Assert GitHub's dependency graph still matches ``uv.lock``.

Dependabot alerts are derived from the dependency graph, so when graph
ingestion breaks, every Python alert silently goes stale and a real CVE looks
identical to the noise. That is not hypothetical here: a submodule pointing at
an unpushed commit made Dependabot's clone fail, its ``update_graph`` job
failed on every run from 2026-05-10, and nobody noticed for over two months. At
discovery the graph was wrong about 39 package versions, missing 10 outright,
and carrying 56 stale extras.

Nothing in that failure mode is loud on its own — the alerts page looked
populated, just wrong. This check is the alarm: it compares what GitHub
*reports* against what the lockfile *says*, and is deliberately indifferent to
*why* they diverge (a broken clone, a parser regression, a workflow that
quietly stopped) so it keeps working when the next cause is a different one.

Run on a schedule, not per push — graph ingestion is asynchronous, so an
immediate assertion after a lockfile change would flake.

Usage:
    GITHUB_TOKEN=... uv run python scripts/check_dependency_graph.py

Exit code 1 when a locked package is missing from the graph or reported at the
wrong version. Extra PyPI packages are reported but do not fail — GitHub may
retain entries from a previous resolution for a while.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import tomllib

import httpx2
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parent.parent
LOCKFILE = REPO / "uv.lock"

API_ROOT = "https://api.github.com"
DEFAULT_REPOSITORY = "w-ash/mixd"
PYPI_PREFIX = "pkg:pypi/"
MAX_REPORTED = 20

_NAME_SEPARATORS = re.compile(r"[-_.]+")


class LockPackage(BaseModel):
    """A resolved package in ``uv.lock``."""

    name: str
    version: str
    source: dict[str, str] = Field(default_factory=dict)


class LockFile(BaseModel):
    """The subset of ``uv.lock`` this check reads."""

    package: list[LockPackage]


class SbomExternalRef(BaseModel):
    """A package identifier; PURLs are the ones worth reading."""

    reference_locator: str = Field(default="", alias="referenceLocator")


class SbomPackage(BaseModel):
    """One package as GitHub's SBOM reports it."""

    external_refs: list[SbomExternalRef] = Field(
        default_factory=list,
        alias="externalRefs",
    )


class SbomDocument(BaseModel):
    """The ``sbom`` object of the dependency-graph SBOM response."""

    packages: list[SbomPackage] = Field(default_factory=list)


class SbomResponse(BaseModel):
    """Response body of ``GET /repos/{owner}/{repo}/dependency-graph/sbom``."""

    sbom: SbomDocument


def normalize_name(name: str) -> str:
    """Normalize a project name per PEP 503, matching PURL spelling."""
    return _NAME_SEPARATORS.sub("-", name).lower()


def locked_versions(lockfile: Path = LOCKFILE) -> dict[str, str]:
    """Package name → version, straight from ``uv.lock``.

    The editable root is the project itself, not a dependency, so it is skipped.
    """
    parsed = LockFile.model_validate(
        tomllib.loads(lockfile.read_text(encoding="utf-8")),
    )
    return {
        normalize_name(pkg.name): pkg.version
        for pkg in parsed.package
        if "editable" not in pkg.source
    }


def graph_versions(repository: str, token: str) -> dict[str, str]:
    """Package name → version, as GitHub's dependency graph currently reports it."""
    response = httpx2.get(
        f"{API_ROOT}/repos/{repository}/dependency-graph/sbom",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )
    response.raise_for_status()
    parsed = SbomResponse.model_validate(response.json())

    versions: dict[str, str] = {}
    for package in parsed.sbom.packages:
        for ref in package.external_refs:
            locator = ref.reference_locator
            if not locator.startswith(PYPI_PREFIX) or "@" not in locator:
                continue
            name, _, version = locator.removeprefix(PYPI_PREFIX).partition("@")
            versions[normalize_name(name)] = version
    return versions


def compare(
    locked: dict[str, str],
    reported: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """Split the diff into (missing, mismatched, extra) human-readable lines."""
    missing = sorted(
        f"{name} {version}" for name, version in locked.items() if name not in reported
    )
    mismatched = sorted(
        f"{name}: lock has {version}, graph reports {reported[name]}"
        for name, version in locked.items()
        if name in reported and reported[name] != version
    )
    extra = sorted(
        f"{name} {version}" for name, version in reported.items() if name not in locked
    )
    return missing, mismatched, extra


def _report(label: str, lines: list[str]) -> None:
    """Print a section, truncated so a wholesale drift doesn't flood the log."""
    if not lines:
        return
    print(f"\n{label} ({len(lines)}):")
    for line in lines[:MAX_REPORTED]:
        print(f"  {line}")
    if len(lines) > MAX_REPORTED:
        print(f"  … and {len(lines) - MAX_REPORTED} more")


def main() -> int:
    """Compare the graph against the lockfile and fail on real drift."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 1
    repository = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY)

    locked = locked_versions()
    reported = graph_versions(repository, token)
    missing, mismatched, extra = compare(locked, reported)

    print(
        f"uv.lock: {len(locked)} packages; graph reports {len(reported)} PyPI packages",
    )
    _report("MISSING from the graph", missing)
    _report("VERSION MISMATCH", mismatched)
    _report("extra in the graph (informational)", extra)

    if missing or mismatched:
        print(
            "\nThe dependency graph does not match uv.lock, so Python Dependabot "
            "alerts are unreliable. Check whether Dependabot's own jobs are "
            "failing (`gh run list --workflow 'Dependency Graph'`) — a failing "
            "clone or update job is the usual cause. See "
            "docs/development.md#dependency-security.",
            file=sys.stderr,
        )
        return 1

    print("\nGraph matches uv.lock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
