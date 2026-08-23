"""Connect-hint copy on the connector auth exceptions.

The messages tell the user a literal CLI command to run, so the command must
exist: it is ``mixd connectors auth <service>`` (the ``auth`` sub-app under
the ``connectors`` Typer group) — ``mixd connector connect`` never existed,
and shipping a hint the shell rejects strands exactly the user the message
was written for (v0.11.2 review fix 4).
"""

from src.domain.exceptions import (
    LastfmAuthRequiredError,
    SpotifyAuthRequiredError,
    SpotifyReauthRequiredError,
)


class TestConnectHintNamesARealCommand:
    def test_spotify_auth_required_hint(self) -> None:
        message = str(SpotifyAuthRequiredError())
        assert "mixd connectors auth spotify" in message
        assert "connector connect" not in message

    def test_spotify_reauth_required_hint(self) -> None:
        message = str(SpotifyReauthRequiredError())
        assert "mixd connectors auth spotify" in message
        assert "connector connect" not in message

    def test_lastfm_auth_required_hint(self) -> None:
        message = str(LastfmAuthRequiredError())
        assert "mixd connectors auth lastfm" in message
        assert "connector connect" not in message
