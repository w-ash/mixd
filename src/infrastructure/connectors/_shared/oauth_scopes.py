"""One reading of an OAuth grant string.

Every provider stores its granted scopes the RFC 6749 way — one space-delimited
string on the token — and every consumer wants the same answer from it: which of
the scopes I need are absent? That question was being answered in three places
(the FastAPI pre-flight dependency, Spotify's ``missing_scopes``, and the
recently-played importer), each re-deciding the delimiter and the
``None``/empty-string handling. One of them then picked up a fix the others
missed, which is precisely the failure this module exists to prevent.
"""

from collections.abc import Collection


def missing_from_grant(
    granted: str | None, required: Collection[str]
) -> frozenset[str]:
    """Required scopes absent from a space-delimited grant string.

    ``None`` or empty (a token stored before scope tracking, or a provider that
    omitted the field) reports every required scope as missing — the correct
    re-consent signal, and the reason writers must never drop a known grant.
    """
    return frozenset(required) - frozenset((granted or "").split())
