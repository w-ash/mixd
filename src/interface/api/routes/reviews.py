"""Match review queue API endpoints.

Exposes pending match reviews for human disambiguation and accept/reject actions.
Zero business logic — delegates to use cases via execute_use_case().
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.application.runner import execute_use_case
from src.application.use_cases.list_match_reviews import (
    ListMatchReviewsCommand,
    ListMatchReviewsUseCase,
)
from src.application.use_cases.resolve_match_review import (
    ResolveMatchReviewCommand,
    ResolveMatchReviewUseCase,
)
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.reviews import (
    MatchReviewListSchema,
    ResolveReviewRequest,
    ResolveReviewResponse,
    to_resolve_response,
    to_review_list,
)

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("")
async def list_reviews(
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="confidence_desc"),
) -> MatchReviewListSchema:
    """List pending match reviews with pagination."""
    result = await execute_use_case(
        lambda uow: ListMatchReviewsUseCase().execute(
            ListMatchReviewsCommand(
                user_id=user_id, limit=limit, offset=offset, sort_by=sort_by
            ),
            uow,
        ),
        user_id=user_id,
    )
    return to_review_list(result)


@router.post("/{review_id}/resolve")
async def resolve_review(
    review_id: UUID,
    body: ResolveReviewRequest,
    user_id: str = Depends(get_current_user_id),
) -> ResolveReviewResponse:
    """Accept or reject a match review."""
    result = await execute_use_case(
        lambda uow: ResolveMatchReviewUseCase().execute(
            ResolveMatchReviewCommand(
                user_id=user_id, review_id=review_id, action=body.action
            ),
            uow,
        ),
        user_id=user_id,
    )
    return to_resolve_response(result)
