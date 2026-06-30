"""Grep gate: the legacy day-window keys must not reappear in ``src/``.

v0.8.10 renamed ``min_days_back``/``max_days_back`` →
``not_played_in_days``/``played_within_days`` as a clean break (no shim). This
gate fails if either legacy key creeps back into application code or a seed
definition — the rename map in migration 033 (under ``alembic/``, not ``src/``)
is the one legitimate remaining reference.
"""

from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src"
_LEGACY_KEYS = ("min_days_back", "max_days_back")


def _source_files() -> list[Path]:
    return [p for ext in ("*.py", "*.json") for p in _SRC.rglob(ext)]


@pytest.mark.parametrize("key", _LEGACY_KEYS)
def test_legacy_day_window_key_absent_from_src(key: str):
    offenders = [
        str(path.relative_to(_SRC))
        for path in _source_files()
        if key in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"Legacy key '{key}' found in src/ — it was renamed in v0.8.10. "
        f"Offending files: {offenders}"
    )
