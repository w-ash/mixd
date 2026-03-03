"""Domain exception types.

Typed exceptions for domain-level error conditions. These replace stringly-typed
ValueError messages so the API layer can map specific exception types to
specific HTTP status codes without parsing error message text.
"""


class NotFoundError(Exception):
    """Raised when a requested entity does not exist."""
