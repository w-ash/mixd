#!/usr/bin/env python3
"""Run mutmut on a project whose top-level package is named ``src``.

Usage:
    uv run python scripts/mutmut_run.py run "src.domain.matching.text_normalization*"
    uv run python scripts/mutmut_run.py results

Drop-in for the ``mutmut`` CLI: every argument goes to mutmut unchanged. Config is
``[tool.mutmut]`` in pyproject.toml.

mutmut 3.x assumes a src-layout, where ``src/`` is a directory on ``sys.path`` and
never part of a module name. It strips ``src.`` from every mutant name and asserts
that no runtime module name starts with ``src.``. In mixd, ``src`` IS the package
(``from src.domain ... import``), so without this wrapper every stats hit fails the
assertion, or, with the assertion removed, no mutant name matches its module and
every mutant survives.

The two patches make mutant names equal to real module names
(``src.domain.matching.algorithms.x_foo__mutmut_1``). mutmut forks its workers, so
the patches reach every worker process. Each patch checks the exact source text
it replaces and stops with an error when a mutmut upgrade changes that text.
"""

import inspect
import sys
import textwrap

from hypothesis import HealthCheck, settings
from mutmut import stats
from mutmut.__main__ import cli
from mutmut.mutation import trampoline
from mutmut.utils import format_utils

_STRIP_CALL = 'module_name = strip_prefix(module_name, prefix="src.")'
_SRC_ASSERT = (
    'assert not name.startswith("src."), '
    '"Failed trampoline hit. Module name starts with `src.`, which is invalid"'
)


def _without_line(func: object, line: str) -> str:
    """Return the source of ``func`` with ``line`` removed. Stop if it is absent."""
    source = textwrap.dedent(inspect.getsource(func))  # pyright: ignore[reportArgumentType]
    if line not in source:
        sys.exit(
            f"scripts/mutmut_run.py: mutmut changed {func!r}; "
            "re-check its src-prefix handling and update this wrapper."
        )
    return source.replace(line, "pass")


def _patch() -> None:
    """Keep the ``src.`` prefix in mutant names and stats keys."""
    exec(  # ruff: ignore[exec-builtin] — re-defines a mutmut function from its own checked source
        _without_line(format_utils.get_mutant_name, _STRIP_CALL),
        format_utils.__dict__,
    )
    exec(  # ruff: ignore[exec-builtin]
        _without_line(stats.record_trampoline_hit, _SRC_ASSERT),
        stats.__dict__,
    )
    # Modules that imported these names by value keep the old objects.
    trampoline.record_trampoline_hit = stats.record_trampoline_hit
    for module_name in ("mutmut.__main__", "mutmut.mutation.file_mutation"):
        sys.modules[module_name].get_mutant_name = format_utils.get_mutant_name  # pyright: ignore[reportAttributeAccessIssue]


def _load_hypothesis_profile() -> None:
    """Make Hypothesis tests safe to re-run inside one mutmut process.

    mutmut calls ``pytest.main`` several times in one process (stats, clean run,
    then forked mutants), so Hypothesis sees each test method from more than one
    class instance and fails the ``differing_executors`` health check. Fixed
    examples (``derandomize``) make every mutant meet the same inputs, and no
    example database keeps a run from reading examples from an earlier one.
    """
    settings.register_profile(
        "mutmut",
        derandomize=True,
        database=None,
        suppress_health_check=[HealthCheck.differing_executors],
    )
    settings.load_profile("mutmut")


if __name__ == "__main__":
    _patch()
    _load_hypothesis_profile()
    sys.exit(cli())
