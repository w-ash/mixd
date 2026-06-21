"""Per-link sync base — the external snapshot a playlist link last reconciled to.

Records, per link, the connector snapshot id captured at the last successful
sync. It is user-scoped (unlike the shared global connector cache, which any
user's fetch overwrites) and is the foundation for a future snapshot fast-skip
and a 3-way (bidirectional) merge. Recorded on every apply; not yet read for
planning — preview/apply currently diff fresh remote against canonical directly.
"""

from datetime import datetime
from uuid import UUID, uuid7

from attrs import define, field

from .shared import utc_now_factory


@define(frozen=True, slots=True)
class PlaylistSyncBase:
    """What a playlist link last successfully synced to."""

    link_id: UUID
    user_id: str
    connector_name: str
    connector_playlist_identifier: str
    # The connector snapshot id captured when this base was recorded. Equal
    # snapshot on the next fetch ⇒ nothing changed remotely.
    base_snapshot_id: str | None = None
    base_taken_at: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)
