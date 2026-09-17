"""The ORM leaf module names cross-aggregate targets it imports only for typing.

SQLAlchemy reads mapped annotations with ``Format.FORWARDREF`` and resolves the
bare names through the declarative registry. Pin that contract so an SQLAlchemy
upgrade that changes the annotation format fails here, not as a silent mapper
miss.
"""

from annotationlib import Format, ForwardRef, get_annotations

import pytest
from sqlalchemy.orm import configure_mappers

from src.infrastructure.persistence.database.models.mapping import DBTrackMapping
from src.infrastructure.persistence.database.models.play import (
    DBConnectorPlay,
    DBTrackPlay,
)
from src.infrastructure.persistence.database.models.track import (
    DBConnectorTrack,
    DBTrack,
)


@pytest.mark.parametrize(
    ("model", "attribute", "target"),
    [
        (DBTrack, "mappings", DBTrackMapping),
        (DBTrack, "plays", DBTrackPlay),
        (DBTrack, "connector_plays", DBConnectorPlay),
        (DBConnectorTrack, "mappings", DBTrackMapping),
    ],
)
def test_reverse_relationship_resolves_through_the_registry(
    model: type, attribute: str, target: type
) -> None:
    configure_mappers()
    assert getattr(model, attribute).property.mapper.class_ is target


def test_forwardref_format_carries_the_unimported_name() -> None:
    forward = get_annotations(DBTrack, format=Format.FORWARDREF)["mappings"]
    assert any(
        isinstance(arg, ForwardRef) and arg.__forward_arg__ == "DBTrackMapping"
        for arg in _flatten(forward)
    )


def test_value_format_raises_by_design() -> None:
    with pytest.raises(NameError):
        get_annotations(DBTrack, format=Format.VALUE)


def _flatten(annotation: object) -> list[object]:
    args = getattr(annotation, "__args__", ())
    return [annotation, *[a for arg in args for a in _flatten(arg)]]
