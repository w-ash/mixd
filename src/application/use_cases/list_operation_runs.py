"""List the user's OperationRun audit-log rows (v0.7.7)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from attrs import define

from src.application.pagination import (
    PageCursor,
    cursor_sort_value_from_row,
    decode_cursor,
    encode_cursor,
)
from src.domain.entities.operation_run import OperationRun
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ListOperationRunsCommand:
    user_id: str
    limit: int = 20
    encoded_cursor: str | None = None
    operation_types: Sequence[str] | None = None


@define(frozen=True, slots=True)
class ListOperationRunsResult:
    runs: list[OperationRun]
    next_cursor: str | None


@define(slots=True)
class ListOperationRunsUseCase:
    async def execute(
        self,
        command: ListOperationRunsCommand,
        uow: UnitOfWorkProtocol,
    ) -> ListOperationRunsResult:
        after_started_at: datetime | None = None
        after_id: UUID | None = None
        if command.encoded_cursor is not None:
            decoded = decode_cursor(command.encoded_cursor)
            # Cursor stores started_at as ISO string; parse back here so the
            # repo layer never sees the wire format.
            if decoded.sort_value is not None:
                after_started_at = datetime.fromisoformat(str(decoded.sort_value))
                after_id = decoded.last_id

        async with uow:
            repo = uow.get_operation_run_repository()
            runs, next_page_key = await repo.list_for_user(
                user_id=command.user_id,
                limit=command.limit,
                after_started_at=after_started_at,
                after_id=after_id,
                operation_types=command.operation_types,
            )

        next_cursor: str | None = None
        if next_page_key is not None:
            next_started_at, next_id = next_page_key
            next_cursor = encode_cursor(
                PageCursor(
                    sort_column="started_at",
                    sort_value=cursor_sort_value_from_row(
                        "started_at", next_started_at
                    ),
                    last_id=next_id,
                )
            )

        return ListOperationRunsResult(runs=runs, next_cursor=next_cursor)
