# pyright: reportExplicitAny=false
"""Tests for the node config fields registry.

Validates structural invariants of ConfigFieldDef entries: coverage of all
registered node types, field type constraints, select option integrity,
numeric range consistency, and key uniqueness.
"""

import pytest

import src.application.workflows.node_catalog  # noqa: F401 — triggers node registration
from src.application.workflows.node_config_fields import (
    ConfigFieldDef,
    get_node_config_fields,
)
from src.application.workflows.node_registry import list_nodes

VALID_FIELD_TYPES = {"string", "number", "boolean", "select"}


@pytest.fixture
def registry() -> dict[str, tuple[ConfigFieldDef, ...]]:
    """Return the full config fields registry."""
    return get_node_config_fields()


def test_every_registered_node_has_config_fields_entry(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """Every node in the node registry has a corresponding entry in _NODE_CONFIG_FIELDS."""
    registered_node_ids = set(list_nodes().keys())
    config_field_ids = set(registry.keys())

    missing = registered_node_ids - config_field_ids
    assert not missing, (
        f"Registered nodes missing from _NODE_CONFIG_FIELDS: {sorted(missing)}"
    )


def test_no_extra_config_field_entries(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """Config fields registry has no entries for unregistered node types."""
    registered_node_ids = set(list_nodes().keys())
    config_field_ids = set(registry.keys())

    extra = config_field_ids - registered_node_ids
    assert not extra, (
        f"_NODE_CONFIG_FIELDS has entries for unregistered nodes: {sorted(extra)}"
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


# ── ENRICHER_METRIC_DEFS consistency tests ─────────────────────────


def test_enricher_metric_defs_covers_all_enrichers() -> None:
    """Every metric-providing enricher in the registry has an entry in ENRICHER_METRIC_DEFS."""
    from src.application.workflows.node_config_fields import ENRICHER_METRIC_DEFS

    registered_enrichers = {
        node_id
        for node_id, meta in list_nodes().items()
        if meta["category"] == "enricher"
    }
    # enricher.spotify_liked_status doesn't provide filter/sort metrics
    non_metric_enrichers = {"enricher.spotify_liked_status"}
    expected = registered_enrichers - non_metric_enrichers

    metric_enrichers = set(ENRICHER_METRIC_DEFS.keys())
    assert metric_enrichers == expected, (
        f"ENRICHER_METRIC_DEFS out of sync with registry. "
        f"Missing: {sorted(expected - metric_enrichers)}, "
        f"Extra: {sorted(metric_enrichers - expected)}"
    )


def test_metric_options_matches_enricher_metric_defs() -> None:
    """METRIC_OPTIONS is the flattened union of ENRICHER_METRIC_DEFS."""
    from src.application.workflows.node_config_fields import (
        ENRICHER_METRIC_DEFS,
        METRIC_OPTIONS,
    )

    expected_values = {
        opt.value for opts in ENRICHER_METRIC_DEFS.values() for opt in opts
    }
    actual_values = {opt.value for opt in METRIC_OPTIONS}
    assert actual_values == expected_values
