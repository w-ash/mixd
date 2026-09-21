"""One declaration per sortable listing: the sort key, its column, and the
two facts keyset paging needs to know about that column.

Application (cursor codec) and infrastructure (ORDER BY + seek) both read the
same value, so a sort cannot be declared twice and drift.
"""

from collections.abc import Mapping
from typing import Literal

from attrs import define, evolve, field

type SortDirection = Literal["asc", "desc"]


@define(frozen=True, slots=True)
class KeysetSort:
    """A sortable column and how a page over it is bounded.

    ``key`` names the sort on the wire: it is what the cursor's ``"c"`` field
    carries, so a cursor minted under one sort is refused under another that
    shares its column but not its direction. A registry entry gets its key
    from :func:`sorts`; a standalone sort names it explicitly. ``column`` is
    the model attribute the repository orders by. ``nullable`` columns order
    NULLS LAST and need the keyset's NULL arms; ``is_datetime`` columns travel
    through the cursor as ISO strings. A ``computed`` column is not a column
    of the model: the repository owns the expression it orders by and the
    side map it reads the cursor value from.
    """

    column: str
    direction: SortDirection
    nullable: bool = False
    is_datetime: bool = False
    computed: bool = False
    key: str = field(default="", kw_only=True)

    @property
    def desc(self) -> bool:
        return self.direction == "desc"


def sorts[K: str](entries: Mapping[K, KeysetSort]) -> Mapping[K, KeysetSort]:
    """A sort registry whose every entry carries the name it is declared under."""
    return {name: evolve(sort, key=name) for name, sort in entries.items()}
