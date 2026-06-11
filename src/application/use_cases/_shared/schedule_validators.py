"""Pure validators for schedule inputs that need external libraries.

Reused by the CLI (wrapped as ``typer.BadParameter``), the HTTP API (Pydantic
``AfterValidator``), and the use cases (the non-bypassable backstop). All raise
plain ``ValueError`` — the ``normalize_tag`` pattern (``domain/entities/tag.py``).

These live in the *application* layer (not the domain) because they import
``zoneinfo`` / serialize JSON. The simple range checks for the cadence itself
(hour/minute/day_of_week) are pure and live with the entity in
``domain/entities/schedule.py``.
"""

import functools
from zoneinfo import available_timezones


@functools.cache
def _iana_timezones() -> frozenset[str]:
    """Memoize the ~600-entry IANA set — ``available_timezones()`` rebuilds it
    (a fresh ``set``) on every call."""
    return frozenset(available_timezones())


def validate_iana_timezone(tz: str) -> str:
    """Return the IANA timezone name, or raise ``ValueError``.

    Rejects abbreviations like ``'PST'`` (not IANA) and bogus zones — only
    names ``zoneinfo`` recognizes pass, so ``ZoneInfo(tz)`` downstream is safe.
    """
    if tz not in _iana_timezones():
        raise ValueError(f"unknown IANA timezone: {tz!r}")
    return tz
