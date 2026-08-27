"""Boundary validation for connector response bodies."""

from pydantic import BaseModel, ValidationError

from src.config import get_logger
from src.domain.entities.shared import JsonDict
from src.domain.exceptions import ConnectorSyncError

logger = get_logger(__name__).bind(service="connector_boundary")


def validated[ModelT: BaseModel](
    model: type[ModelT], data: JsonDict, *, service: str, subject: str
) -> ModelT:
    """Parse a response body, or raise the connector-flavored error.

    A body the provider actually served but we cannot read is an upstream-contract
    failure, not an internal one — surfacing it as ``ConnectorSyncError`` keeps a
    malformed response from becoming a 500.
    """
    try:
        return model.model_validate(data)
    except ValidationError:
        logger.error(
            f"{service} {subject} failed boundary validation",
            exc_info=True,
            subject=subject,
        )
        raise ConnectorSyncError(
            service,
            f"{service.title()} returned {subject} in an unexpected shape — "
            "try again in a moment",
        ) from None
