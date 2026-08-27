"""Export the connector status-state matrix to web/src/test/fixtures.

``derive_status_state()`` is domain truth that the frontend's test factory has to
reproduce: a test constructing a connector without an explicit ``status`` still
has to land in the right UI branch. A hand-written mirror of the rule drifts
green — the factory keeps passing while the real UI goes wrong — so the rule is
exported as data and the mirror reads it.

The exported domain is the four axes a factory default can vary. Run via
``uv run export-status-matrix`` (``pnpm --prefix web sync-api`` runs it beside
the OpenAPI export); ``tests/unit/domain/entities/test_status_matrix_export.py``
fails if the checked-in file falls behind the rule.
"""

from itertools import product
import json
from pathlib import Path
from typing import Final, TypedDict, cast, get_args

from src.domain.entities.connector import (
    ConnectorAuthError,
    ConnectorAuthMethod,
    ConnectorStatus,
    ConnectorStatusState,
    derive_status_state,
)

# A past epoch second, so the ``expired`` arm is reachable. Any fixed value in
# the past does: the rule compares against wall time, and a fixture that moved
# with the clock would not be a fixture.
_EXPIRED_AT: Final[int] = 1_000_000_000

FIXTURE_PATH: Final[Path] = (
    Path(__file__).resolve().parents[1]
    / "web"
    / "src"
    / "test"
    / "fixtures"
    / "connector-status-matrix.json"
)


class StatusMatrixRow(TypedDict):
    """One exported combination and the state the rule derives for it."""

    auth_method: ConnectorAuthMethod
    auth_error: ConnectorAuthError | None
    connected: bool
    expired: bool
    status: ConnectorStatusState


def literal_members[MemberT](alias: object) -> tuple[MemberT, ...]:
    """The members of a PEP 695 ``type`` alias over a ``Literal``.

    ``get_args`` on the alias itself sees a ``TypeAliasType`` and returns
    nothing, so the underlying value is unwrapped first. Typeshed types
    ``__value__`` as ``Any``, hence the casts — reflection is the point here,
    and enumerating the members by hand would be the mirror this file removes.
    """
    value = cast(object, getattr(alias, "__value__", alias))
    return cast(tuple[MemberT, ...], get_args(value))


def build_matrix() -> list[StatusMatrixRow]:
    """Every (auth_method, auth_error, connected, expired) → state row."""
    auth_methods: tuple[ConnectorAuthMethod, ...] = literal_members(ConnectorAuthMethod)
    error_members: tuple[ConnectorAuthError, ...] = literal_members(ConnectorAuthError)
    auth_errors: tuple[ConnectorAuthError | None, ...] = (None, *error_members)
    return [
        StatusMatrixRow(
            auth_method=auth_method,
            auth_error=auth_error,
            connected=connected,
            expired=expired,
            status=derive_status_state(
                ConnectorStatus(
                    name="fixture",
                    auth_method=auth_method,
                    connected=connected,
                    auth_error=auth_error,
                    token_expires_at=_EXPIRED_AT if expired else None,
                )
            ),
        )
        for auth_method, auth_error, connected, expired in product(
            auth_methods, auth_errors, (False, True), (False, True)
        )
    ]


def render() -> str:
    return json.dumps(build_matrix(), indent=2) + "\n"


def main() -> None:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ = FIXTURE_PATH.write_text(render())
    print(f"Exported connector status matrix to {FIXTURE_PATH}")


if __name__ == "__main__":
    main()
