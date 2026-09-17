"""One declaration per sortable listing: the sort key, its column, and the
two facts keyset paging needs to know about that column.

Application (cursor codec) and infrastructure (ORDER BY + seek) both read the
same value, so a sort cannot be declared twice and drift.
"""

from typing import Literal

from attrs import define

type SortDirection = Literal["asc", "desc"]


@define(frozen=True, slots=True)
class KeysetSort:
    """A sortable column and how a page over it is bounded.

    ``column`` is the model attribute the repository orders by and the value
    the cursor's ``"c"`` field carries. ``nullable`` columns order NULLS LAST
    and need the keyset's NULL arms; ``is_datetime`` columns travel through the
    cursor as ISO strings.
    """

    column: str
    direction: SortDirection
    nullable: bool = False
    is_datetime: bool = False

    @property
    def desc(self) -> bool:
        return self.direction == "desc"
