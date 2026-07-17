"""Unit tests for Spotify OAuth scope-gap detection (``missing_scopes``).

The v0.10.1 re-consent flow hinges on comparing a stored token's granted
scope string against the app's current ``SPOTIFY_SCOPES`` request.
"""

from src.infrastructure.connectors.spotify.auth import SPOTIFY_SCOPES, missing_scopes

RECENTLY_PLAYED = "user-read-recently-played"


class TestMissingScopes:
    def test_full_grant_has_no_gap(self) -> None:
        assert missing_scopes(" ".join(SPOTIFY_SCOPES)) == frozenset()

    def test_legacy_token_without_scope_reports_all_missing(self) -> None:
        assert missing_scopes(None) == frozenset(SPOTIFY_SCOPES)
        assert missing_scopes("") == frozenset(SPOTIFY_SCOPES)

    def test_grant_with_extra_scopes_is_clean(self) -> None:
        granted = " ".join([*SPOTIFY_SCOPES, "user-top-read"])
        assert missing_scopes(granted) == frozenset()

    def test_pre_v0_10_1_grant_is_missing_recently_played(self) -> None:
        legacy = " ".join(s for s in SPOTIFY_SCOPES if s != RECENTLY_PLAYED)
        assert missing_scopes(legacy) == {RECENTLY_PLAYED}

    def test_recently_played_scope_is_requested(self) -> None:
        assert RECENTLY_PLAYED in SPOTIFY_SCOPES
