#!/usr/bin/env python3
"""Run mutmut on a project whose top-level package is named ``src``.

Usage:
    uv run python scripts/mutmut_run.py run "src.domain.matching.text_normalization*"
    uv run python scripts/mutmut_run.py results
    uv run python scripts/mutmut_run.py show <mutant name>

Drop-in for the ``mutmut`` CLI: every argument goes to mutmut unchanged. Always run
mutmut through this wrapper; bare ``mutmut`` cannot test this package. ``/ship``
Step 6 runs it on the release diff; CI does not. Results go to ``mutants/``.

Config is ``[tool.mutmut]`` in pyproject.toml:

- Only pure, synchronous ``src/domain/`` is mutated (``only_mutate``). mutmut forks
  once per mutant, and a forked child that inherits a live event loop hangs async
  tests (mutmut #578).
- ``source_paths`` is all of ``src/``: the root conftest imports infrastructure, so
  ``mutants/`` must hold a complete, importable ``src`` package.
- ``pytest_add_cli_args`` loads the Hypothesis ``mutmut`` profile, which
  ``tests/conftest.py`` registers. mutmut calls ``pytest.main`` several times in one
  process, so Hypothesis sees each test method from more than one class instance
  and fails the ``differing_executors`` health check; the profile suppresses it.
  Fixed examples (``derandomize``) give every mutant the same inputs, and no example
  database keeps one run from reading examples from an earlier one.

mutmut 3.x assumes a src-layout, where ``src/`` is a directory on ``sys.path`` and
never part of a module name. It strips ``src.`` from every mutant name and asserts
that no runtime module name starts with ``src.``. In mixd, ``src`` IS the package
(``from src.domain ... import``), so without this wrapper every stats hit fails the
assertion, or, with the assertion removed, no mutant name matches its module and
every mutant survives.

Two patches make mutant names equal to real module names
(``src.domain.matching.algorithms.x_foo__mutmut_1``):

- ``format_utils.strip_prefix`` returns the name unchanged for the ``src.`` prefix.
  ``get_mutant_name`` looks it up at call time. The pytest harness imports its own
  reference and strips ``mutants/``, which stays unchanged.
- ``stats.record_trampoline_hit`` loses its ``src.`` assertion. The patch checks the
  exact source line it removes and stops with an error when a mutmut upgrade
  changes it.

mutmut forks its workers, so the patches reach every worker process.
"""

import inspect
import sys
import textwrap

from mutmut import stats
from mutmut.__main__ import cli
from mutmut.mutation import trampoline
from mutmut.utils import format_utils

_SRC_ASSERT = (
    'assert not name.startswith("src."), '
    '"Failed trampoline hit. Module name starts with `src.`, which is invalid"'
)
_strip_prefix = format_utils.strip_prefix


def _keep_src_prefix(s: str, *, prefix: str, strict: bool = False) -> str:
    """Return ``s`` unchanged for the ``src.`` prefix; strip other prefixes."""
    if prefix == "src.":
        return s
    return _strip_prefix(s, prefix=prefix, strict=strict)


def _patch() -> None:
    """Keep the ``src.`` prefix in mutant names and stats keys."""
    format_utils.strip_prefix = _keep_src_prefix
    source = textwrap.dedent(inspect.getsource(stats.record_trampoline_hit))
    if _SRC_ASSERT not in source:
        sys.exit(
            "scripts/mutmut_run.py: mutmut changed stats.record_trampoline_hit; "
            "re-check its src-prefix handling and update this wrapper."
        )
    exec(  # ruff: ignore[exec-builtin] — re-defines a mutmut function from its own checked source
        source.replace(_SRC_ASSERT, "pass"), stats.__dict__
    )
    # trampoline imported the function by value and keeps the old object.
    trampoline.__dict__["record_trampoline_hit"] = stats.record_trampoline_hit


if __name__ == "__main__":
    _patch()
    cli()
