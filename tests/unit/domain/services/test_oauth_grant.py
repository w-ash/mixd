"""Grant-string parsing: the one reading of a space-delimited OAuth scope list."""

from src.domain.services.oauth_grant import grant_scopes, missing_from_grant


class TestGrantScopes:
    """``grant_scopes`` — the shared parse every reader now goes through."""

    def test_grant_scopes_none_and_blank_are_empty(self) -> None:
        """A missing or empty grant permits nothing — no scopes, not a crash."""
        assert grant_scopes(None) == frozenset()
        assert grant_scopes("") == frozenset()
        assert grant_scopes("   ") == frozenset()

    def test_grant_scopes_splits_on_whitespace(self) -> None:
        """RFC 6749 uses single spaces; runs and padding must not leak empties."""
        assert grant_scopes("user-read-recently-played") == frozenset({
            "user-read-recently-played"
        })
        assert grant_scopes("playlist-read-private user-library-read") == frozenset({
            "playlist-read-private",
            "user-library-read",
        })
        assert grant_scopes("  a   b\tc\n") == frozenset({"a", "b", "c"})


class TestMissingFromGrant:
    """``missing_from_grant`` keeps its contract while delegating the parse."""

    def test_absent_scopes_are_reported(self) -> None:
        missing = missing_from_grant("a b", ("a", "c"))

        assert missing == frozenset({"c"})

    def test_fully_granted_reports_nothing(self) -> None:
        assert missing_from_grant("a b", ("a", "b")) == frozenset()

    def test_none_grant_reports_every_required_scope(self) -> None:
        """The re-consent signal for tokens stored before scope tracking."""
        assert missing_from_grant(None, ("a", "b")) == frozenset({"a", "b"})
