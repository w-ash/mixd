"""The session-layer refusal to open a transaction as ``default`` remotely.

``DEFAULT_USER_ID`` is local-dev scaffolding, so rows written under it on a
hosted database are always an accident. Production accumulated three such
tenants through three different doors — the CLI, a script, and (still open
until this guard) the API, whose ``get_current_user_id`` returns the sentinel
whenever auth is unconfigured, which is what ``pnpm dev`` does.

The guard lives on the ``after_begin`` event because that is the one place all
three entry points converge. These tests pin both halves: what it refuses, and
— just as important — what it must keep allowing, since a guard that blocks the
scheduler or the OAuth prune is worse than no guard.
"""

import pytest

from src.config.constants import BusinessLimits
from src.config.settings import database_host_and_mode
from src.infrastructure.persistence.database.user_context import (
    DefaultUserOnRemoteDatabaseError,
    _refuse_default_user_on_remote,
)

DEFAULT = BusinessLimits.DEFAULT_USER_ID
LOCAL = "postgresql+psycopg://mixd:mixd@localhost:5432/mixd"
REMOTE = "postgresql+psycopg://u:p@ep-super-glade.neon.tech/neondb"


class TestDatabaseHostAndMode:
    """One definition of "local", shared by ``whoami`` and the guard."""

    @pytest.mark.parametrize(
        "url",
        [
            LOCAL,
            "postgresql+psycopg://u:p@127.0.0.1:5432/m",
            "postgresql+psycopg://u:p@[::1]:5432/m",
            "postgresql+psycopg://u:p@LOCALHOST:5432/m",
        ],
    )
    def test_loopback_hosts_are_local(self, url: str) -> None:
        assert database_host_and_mode(url)[1] == "local"

    @pytest.mark.parametrize(
        "url",
        [
            REMOTE,
            "postgresql+psycopg://u:p@postgres:5432/m",  # docker-compose service
            "postgresql+psycopg://u:p@host.docker.internal:5432/m",
            "postgresql+psycopg://u:p@mymac.local:5432/m",  # mDNS is another box
            "postgresql+psycopg://u:p@0.0.0.0:5432/m",
        ],
    )
    def test_everything_else_is_remote(self, url: str) -> None:
        assert database_host_and_mode(url)[1] == "remote"

    def test_libpq_host_parameter_naming_a_hostname_is_remote(self) -> None:
        """``postgresql:///db?host=<hostname>`` is a real libpq form.

        It has no netloc, so a naive ``urlparse().hostname`` reads empty and a
        guard that treats empty as local waves production straight through.
        """
        host, mode = database_host_and_mode(
            "postgresql+psycopg:///neondb?host=ep-super-glade.neon.tech"
        )
        assert (host, mode) == ("ep-super-glade.neon.tech", "remote")

    def test_libpq_host_parameter_naming_a_socket_dir_is_local(self) -> None:
        host, mode = database_host_and_mode(
            "postgresql+psycopg:///mixd?host=/var/run/postgresql"
        )
        assert (host, mode) == ("/var/run/postgresql", "local")

    @pytest.mark.parametrize("url", ["", "not a url at all", "postgresql:///mixd"])
    def test_unparseable_fails_closed(self, url: str) -> None:
        """Unknown is not evidence of safety.

        The guard is not on a hot path, so a false alarm costs an export and a
        rerun; failing open costs production rows.
        """
        assert database_host_and_mode(url)[1] == "remote"


class TestGuard:
    def test_default_user_on_a_remote_database_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", REMOTE)
        with pytest.raises(DefaultUserOnRemoteDatabaseError) as exc:
            _refuse_default_user_on_remote(DEFAULT)
        # The message has to say what to do, not just that it said no.
        assert "MIXD_USER_ID" in str(exc.value)
        assert "ep-super-glade.neon.tech" in str(exc.value)

    def test_default_user_via_the_host_parameter_form_is_also_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql+psycopg:///neondb?host=ep-super-glade.neon.tech"
        )
        with pytest.raises(DefaultUserOnRemoteDatabaseError):
            _refuse_default_user_on_remote(DEFAULT)

    def test_default_user_on_localhost_is_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Local single-user dev is the case ``default`` exists to serve."""
        monkeypatch.setenv("DATABASE_URL", LOCAL)
        _refuse_default_user_on_remote(DEFAULT)  # must not raise


class TestGuardDoesNotBlockLegitimateWork:
    """A guard that breaks the scheduler is worse than no guard."""

    def test_a_real_user_against_production_is_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.infrastructure.persistence.database.user_context import (
            _current_user_id,
            _system_operation,
        )

        monkeypatch.setenv("DATABASE_URL", REMOTE)
        token = _current_user_id.set("cfe9d062-577f-real-account")
        try:
            uid = _current_user_id.get()
            # Mirrors the branch in set_rls_user_on_begin.
            assert not (uid == DEFAULT and not _system_operation.get())
        finally:
            _current_user_id.reset(token)

    def test_system_context_declares_a_user_less_transaction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cross-tenant maintenance runs without a user, on purpose.

        ``prune_expired_states`` is a DELETE with no user predicate; the
        sweeper and run reaper go through ``execute_use_case`` with no
        ``user_id``. Before ``system_context`` those were indistinguishable
        from the accident this guard exists to catch.
        """
        from src.infrastructure.persistence.database.user_context import (
            _system_operation,
            system_context,
        )

        monkeypatch.setenv("DATABASE_URL", REMOTE)
        assert _system_operation.get() is False
        with system_context():
            assert _system_operation.get() is True
        assert _system_operation.get() is False

    def test_system_context_resets_when_the_block_raises(self) -> None:
        from src.infrastructure.persistence.database.user_context import (
            _system_operation,
            system_context,
        )

        with pytest.raises(ValueError, match="boom"), system_context():
            raise ValueError("boom")
        assert _system_operation.get() is False

    @pytest.mark.asyncio
    async def test_execute_use_case_without_a_user_id_is_a_system_operation(
        self,
    ) -> None:
        """The sweeper and reaper rely on this — no ``user_id`` means no tenant.

        ``periodic_loop`` already documents the contract ("no ``user_id`` →
        cross-tenant"); this pins that it now also declares itself, so the
        guard lets it through.
        """
        from src.infrastructure.persistence.database.user_context import (
            _system_operation,
        )

        seen: list[bool] = []

        async def _factory(_uow: object) -> None:
            seen.append(_system_operation.get())

        from unittest.mock import AsyncMock, patch

        session = AsyncMock()
        with (
            patch(
                "src.infrastructure.persistence.database.db_connection.get_session"
            ) as get_session,
            patch(
                "src.infrastructure.persistence.repositories.factories.get_unit_of_work"
            ),
        ):
            get_session.return_value.__aenter__.return_value = session
            from src.application.runner import execute_use_case

            await execute_use_case(_factory)

        assert seen == [True], "execute_use_case(user_id=None) must declare system"
