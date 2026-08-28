"""Unit tests for the config-driven play import registry.

The registry resolves ``(service, kind)`` lookups against the
``play_importer_factories`` / ``play_resolver_factory`` declarations on each
``ConnectorConfig`` — no hand-maintained service table.
"""

from unittest.mock import patch

import pytest

from src.infrastructure.services.play_import_registry import (
    PlayImportServiceRegistry,
    get_play_import_registry,
)
from tests.fixtures import make_mock_uow

_DISCOVERY = "src.infrastructure.services.play_import_registry.discover_connectors"


class TestDeclaredCombos:
    """Every combination a shipped connector declares resolves to an importer."""

    @pytest.mark.parametrize(
        ("service", "kind"),
        [
            ("spotify", "file"),
            ("spotify", "api"),
            ("lastfm", "api"),
            ("apple", "api"),
        ],
    )
    async def test_declared_combo_resolves(self, service: str, kind: str) -> None:
        registry = get_play_import_registry()

        importer = await registry.create_play_importer(service, kind, make_mock_uow())

        assert importer is not None

    async def test_apple_resolver_keys_on_data_plane_name(self) -> None:
        # apple_music's config declares play_service_name="apple" — the name
        # its play rows key on, not the package name.
        resolver = await get_play_import_registry().create_play_resolver("apple")

        assert type(resolver).__name__ == "AppleMusicConnectorPlayResolver"


class TestUndeclaredCombos:
    """Lookups nothing declares fail exactly as the hand-maintained table did."""

    async def test_undeclared_importer_combo_raises(self) -> None:
        registry = PlayImportServiceRegistry()

        with pytest.raises(ValueError, match="Unsupported import 'lastfm:file'"):
            await registry.create_play_importer("lastfm", "file", make_mock_uow())

    async def test_unknown_resolver_service_raises(self) -> None:
        registry = PlayImportServiceRegistry()

        with pytest.raises(ValueError, match="Unsupported service 'tidal'"):
            await registry.create_play_resolver("tidal")


class TestConfigDriven:
    """The lookup reads connector declarations, not a hardcoded table."""

    async def test_declared_factory_is_used(self) -> None:
        importer = object()
        fake_config = {
            "play_service_name": "faux",
            "play_importer_factories": {"api": lambda: importer},
        }

        with patch(_DISCOVERY, return_value={"faux_svc": fake_config}):
            registry = PlayImportServiceRegistry()
            built = await registry.create_play_importer("faux", "api", make_mock_uow())

            assert built is importer
            # With only faux registered, the spotify combos are gone too —
            # proof the table is derived, not hand-maintained.
            with pytest.raises(ValueError, match="Unsupported import 'spotify:file'"):
                await registry.create_play_importer("spotify", "file", make_mock_uow())

    async def test_declared_resolver_factory_is_used(self) -> None:
        resolver = object()
        fake_config = {"play_resolver_factory": lambda: resolver}

        with patch(_DISCOVERY, return_value={"faux_svc": fake_config}):
            built = await PlayImportServiceRegistry().create_play_resolver("faux_svc")

        assert built is resolver
