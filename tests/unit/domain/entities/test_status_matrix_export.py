"""The exported status matrix must agree with the rule it exports.

``web/src/test/factories.ts`` reads the checked-in fixture instead of
re-implementing ``derive_status_state()``. That only removes the drift trap if
the fixture is current, so a rule change that nobody re-exported fails here
rather than shipping a factory that quietly disagrees with production.
"""

from scripts.export_status_matrix import (
    FIXTURE_PATH,
    build_matrix,
    literal_members,
    render,
)
from src.domain.entities.connector import ConnectorStatusState


def test_checked_in_matrix_matches_the_rule() -> None:
    assert FIXTURE_PATH.read_text() == render(), (
        "web/src/test/factories.ts reads this fixture as the connector status "
        "rule. Re-export it: uv run export-status-matrix"
    )


def test_every_status_state_is_reachable() -> None:
    """A matrix missing an arm would pin the factory to a partial rule."""
    states: tuple[ConnectorStatusState, ...] = literal_members(ConnectorStatusState)
    assert {row["status"] for row in build_matrix()} == set(states)
