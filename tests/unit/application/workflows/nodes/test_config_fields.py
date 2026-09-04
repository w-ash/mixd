# pyright: reportExplicitAny=false
"""Tests for the node config fields registry.

Validates structural invariants of ConfigFieldDef entries: coverage of all
registered node types, field type constraints, select option integrity,
numeric range consistency, and key uniqueness.
"""

import pytest

import src.application.workflows.nodes.catalog as _catalog
from src.application.workflows.nodes.config_fields import (
    ConfigFieldDef,
    get_node_config_fields,
)
from src.application.workflows.nodes.registry import list_nodes

# Importing the node catalog above triggers @node() registration as a side effect.
# This reference keeps F401 from flagging the import as unused under ruff
# configurations that autofix noqa comments.
_CATALOG_MODULE = _catalog.__name__

VALID_FIELD_TYPES = {"string", "number", "boolean", "select"}


@pytest.fixture
def registry() -> dict[str, tuple[ConfigFieldDef, ...]]:
    """Return the full config fields registry."""
    return get_node_config_fields()


def test_every_registered_node_has_config_fields_entry(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """Every node in the node registry has a corresponding entry in the config fields registry."""
    registered_node_ids = set(list_nodes().keys())
    config_field_ids = set(registry.keys())

    missing = registered_node_ids - config_field_ids
    assert not missing, (
        f"Registered nodes missing from the config fields registry: {sorted(missing)}"
    )


def test_no_extra_config_field_entries(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """Config fields registry has no entries for unregistered node types."""
    registered_node_ids = set(list_nodes().keys())
    config_field_ids = set(registry.keys())

    extra = config_field_ids - registered_node_ids
    assert not extra, (
        f"the config fields registry has entries for unregistered nodes: {sorted(extra)}"
    )


def test_select_fields_have_at_least_one_option(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """Every field with field_type='select' has at least one option."""
    for node_type, fields in registry.items():
        for field in fields:
            if field.field_type == "select":
                assert len(field.options) >= 1, (
                    f"{node_type}.{field.key}: select field has no options"
                )


def test_required_fields_have_valid_field_type(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """Required fields have a field_type in the allowed set."""
    for node_type, fields in registry.items():
        for field in fields:
            if field.required:
                assert field.field_type in VALID_FIELD_TYPES, (
                    f"{node_type}.{field.key}: required field has invalid "
                    f"field_type '{field.field_type}'"
                )


def test_all_fields_have_valid_field_type(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """All fields (not just required) have a valid field_type."""
    for node_type, fields in registry.items():
        for field in fields:
            assert field.field_type in VALID_FIELD_TYPES, (
                f"{node_type}.{field.key}: invalid field_type '{field.field_type}'"
            )


def test_numeric_fields_min_less_than_max(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """Numeric fields with both min and max have min < max."""
    for node_type, fields in registry.items():
        for field in fields:
            if field.min is not None and field.max is not None:
                assert field.min < field.max, (
                    f"{node_type}.{field.key}: min ({field.min}) >= max ({field.max})"
                )


def test_no_duplicate_keys_within_node_type(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """No duplicate keys within a single node type's field tuple."""
    for node_type, fields in registry.items():
        keys = [f.key for f in fields]
        duplicates = [k for k in keys if keys.count(k) > 1]
        assert not duplicates, (
            f"{node_type}: duplicate config field keys {set(duplicates)}"
        )


def test_option_values_unique_within_field(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """All option tuples have unique values within each field."""
    for node_type, fields in registry.items():
        for field in fields:
            if field.options:
                values = [opt.value for opt in field.options]
                duplicates = [v for v in values if values.count(v) > 1]
                assert not duplicates, (
                    f"{node_type}.{field.key}: duplicate option values {set(duplicates)}"
                )


# ── Enricher metric-def consistency tests ──────────────────────────


def test_enricher_metric_defs_covers_all_enrichers() -> None:
    """Every metric-providing enricher in the registry has a metric-def entry."""
    from src.application.workflows.nodes.config_fields import get_enricher_metric_defs

    registered_enrichers = {
        node_id
        for node_id, meta in list_nodes().items()
        if meta["category"] == "enricher"
    }
    # Enrichers that don't expose scalar metrics to the filter.by_metric /
    # sorter.by_metric dropdown. They feed purpose-built consumer nodes
    # (filter.by_preference, filter.by_tag, …) with fixed semantics.
    non_metric_enrichers = {
        "enricher.spotify_liked_status",
        "enricher.preferences",
        "enricher.tags",
    }
    expected = registered_enrichers - non_metric_enrichers

    metric_enrichers = set(get_enricher_metric_defs())
    assert metric_enrichers == expected, (
        f"Enricher metric defs out of sync with registry. "
        f"Missing: {sorted(expected - metric_enrichers)}, "
        f"Extra: {sorted(metric_enrichers - expected)}"
    )


def test_metric_options_matches_enricher_metric_defs() -> None:
    """The metric options are the flattened union of the enricher metric defs."""
    from src.application.workflows.nodes.config_fields import (
        get_enricher_metric_defs,
        get_metric_options,
    )

    expected_values = {
        opt.value for opts in get_enricher_metric_defs().values() for opt in opts
    }
    actual_values = {opt.value for opt in get_metric_options()}
    assert actual_values == expected_values


def test_connector_options_come_from_the_connector_catalog() -> None:
    """Service pickers list exactly the connectors declaring the capability."""
    from src.application.use_cases._shared.connector_catalog import (
        default_connector_catalog,
    )
    from src.application.workflows.nodes.config_fields import get_node_config_fields

    descriptors = default_connector_catalog().list_descriptors()
    expected = {d.name for d in descriptors if "playlist_sync" in d.capabilities}

    connector_field = next(
        f for f in get_node_config_fields()["source.playlist"] if f.key == "connector"
    )
    assert {opt.value for opt in connector_field.options} == expected


def test_required_select_with_no_options_fails_the_build() -> None:
    """A connector picker with nothing to pick is a build error, not an empty dropdown.

    ``filter.by_liked_status.service`` is required and its options come from the
    connector catalog; if the catalog yields no likes-import connector the
    registry must refuse to build rather than serve an unsatisfiable field.
    """
    from unittest.mock import patch

    from src.application.workflows.nodes import config_fields

    with (
        patch.object(config_fields, "_service_options", return_value=()),
        pytest.raises(RuntimeError, match=r"filter\.by_liked_status\.service"),
    ):
        _ = config_fields._build_node_config_fields()


def test_full_catalog_builds_without_error() -> None:
    """The live catalog satisfies every required select."""
    from src.application.workflows.nodes import config_fields

    registry = config_fields._build_node_config_fields()
    assert "filter.by_liked_status" in registry
