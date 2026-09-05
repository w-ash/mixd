# pyright: reportExplicitAny=false
"""Tests for the node config fields registry.

Validates structural invariants of ConfigFieldDef entries: coverage of all
registered node types, field type constraints, select option integrity,
numeric range consistency, and key uniqueness.
"""

import pytest

import src.application.workflows.nodes.catalog as _catalog
from src.application.workflows.nodes.config_fields import (
    DEFAULT_PLAY_HISTORY_METRICS,
    PRIMARY_INPUT_FIELD,
    ConfigFieldDef,
    apply_declared_defaults,
    format_bound,
    get_node_config_fields,
    is_unset,
)
from src.application.workflows.nodes.registry import list_nodes

# Importing the node catalog above triggers @node() registration as a side effect.
# This reference keeps F401 from flagging the import as unused under ruff
# configurations that autofix noqa comments.
_CATALOG_MODULE = _catalog.__name__

VALID_FIELD_TYPES = {
    "string",
    "number",
    "boolean",
    "select",
    "multi_select",
    "task_ref",
}


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
    """Every select or multi_select field has at least one option."""
    for node_type, fields in registry.items():
        for field in fields:
            if field.field_type in ("select", "multi_select"):
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


def test_every_non_source_node_declares_primary_input(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """The executor reads primary_input on any node with upstreams; sources have none."""
    for node_id, meta in list_nodes().items():
        keys = [f.key for f in registry[node_id]]
        if meta["category"] == "source":
            assert "primary_input" not in keys, f"{node_id} is a source"
        else:
            assert registry[node_id][-1] is PRIMARY_INPUT_FIELD, node_id


def test_task_ref_fields_never_carry_defaults(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    """A task id cannot be defaulted — it depends on the workflow's graph."""
    for node_type, fields in registry.items():
        for field in fields:
            if field.field_type == "task_ref":
                assert field.default is None, f"{node_type}.{field.key}"
                assert not field.options, f"{node_type}.{field.key}"


def test_boolean_fields_default_to_real_booleans(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    for node_type, fields in registry.items():
        for field in fields:
            if field.field_type == "boolean" and field.default is not None:
                assert isinstance(field.default, bool), f"{node_type}.{field.key}"


def test_multi_select_defaults_are_subsets_of_options(
    registry: dict[str, tuple[ConfigFieldDef, ...]],
) -> None:
    for node_type, fields in registry.items():
        for field in fields:
            if field.field_type == "multi_select" and field.default is not None:
                assert isinstance(field.default, tuple), f"{node_type}.{field.key}"
                allowed = {o.value for o in field.options}
                assert set(field.default) <= allowed, f"{node_type}.{field.key}"


class TestApplyDeclaredDefaults:
    """Declared defaults reach a node once, from the executor, never from node code."""

    def test_fills_absent_keys_only(self) -> None:
        config = apply_declared_defaults(
            "selector.limit_tracks", {"count": 3, "primary_input": "src"}
        )
        assert config == {"count": 3, "method": "first", "primary_input": "src"}

    def test_present_key_wins_even_when_falsy(self) -> None:
        config = apply_declared_defaults("combiner.merge_playlists", {})
        assert config == {"deduplicate": False}
        assert apply_declared_defaults(
            "combiner.merge_playlists", {"deduplicate": True}
        ) == {"deduplicate": True}

    def test_multi_select_default_becomes_a_json_list(self) -> None:
        config = apply_declared_defaults("enricher.play_history", {})
        assert config == {"metrics": list(DEFAULT_PLAY_HISTORY_METRICS)}

    def test_unknown_node_type_passes_config_through(self) -> None:
        assert apply_declared_defaults("totally.fake", {"x": 1}) == {"x": 1}

    def test_null_falls_back_to_the_declared_default(self) -> None:
        """A key present with JSON null is absent: the default fills it."""
        config = apply_declared_defaults(
            "selector.limit_tracks", {"count": None, "method": None}
        )
        assert config == {"count": 10, "method": "first"}

    def test_empty_multi_select_falls_back_to_the_declared_default(self) -> None:
        config = apply_declared_defaults("enricher.play_history", {"metrics": []})
        assert config == {"metrics": list(DEFAULT_PLAY_HISTORY_METRICS)}

    def test_null_without_a_default_is_dropped(self) -> None:
        """Absent means absent: no default, no key."""
        config = apply_declared_defaults(
            "selector.limit_tracks", {"count": 3, "primary_input": None}
        )
        assert config == {"count": 3, "method": "first"}

    def test_empty_string_is_a_present_value(self) -> None:
        """Only null and an empty multi_select count as unset."""
        config = apply_declared_defaults(
            "destination.create_playlist", {"name": "x", "description": ""}
        )
        assert config["description"] == ""

    def test_every_declared_default_is_supplied(self) -> None:
        for node_type, fields in get_node_config_fields().items():
            config = apply_declared_defaults(node_type, {})
            for field in fields:
                if field.default is None:
                    assert field.key not in config, f"{node_type}.{field.key}"
                elif isinstance(field.default, tuple):
                    assert config[field.key] == list(field.default)
                else:
                    assert config[field.key] == field.default, (
                        f"{node_type}.{field.key}"
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


class TestIsUnset:
    """The one absent-value rule the executor and the validator share."""

    _string = ConfigFieldDef(key="k", label="K", field_type="string")
    _multi = ConfigFieldDef(key="k", label="K", field_type="multi_select")
    _number = ConfigFieldDef(key="k", label="K", field_type="number")

    def test_null_is_unset_for_every_field_type(self) -> None:
        assert is_unset(self._string, None)
        assert is_unset(self._multi, None)
        assert is_unset(self._number, None)

    def test_empty_list_is_unset_only_for_multi_select(self) -> None:
        assert is_unset(self._multi, [])
        assert not is_unset(self._string, [])

    def test_falsy_values_are_set(self) -> None:
        assert not is_unset(self._string, "")
        assert not is_unset(self._number, 0)
        assert not is_unset(self._multi, ["a"])


class TestFormatBound:
    def test_whole_numbers_never_use_an_exponent(self) -> None:
        assert format_bound(1_000_000) == "1000000"
        assert format_bound(1_000_000.0) == "1000000"
        assert format_bound(1) == "1"

    def test_fractions_keep_their_digits(self) -> None:
        assert format_bound(0.5) == "0.5"
        assert format_bound(-0.25) == "-0.25"


class TestJsonDefault:
    def test_tuple_default_becomes_a_list(self) -> None:
        field = ConfigFieldDef(
            key="k", label="K", field_type="multi_select", default=("a", "b")
        )
        assert field.json_default() == ["a", "b"]

    def test_scalar_and_missing_defaults_pass_through(self) -> None:
        assert (
            ConfigFieldDef(
                key="k", label="K", field_type="number", default=10
            ).json_default()
            == 10
        )
        assert (
            ConfigFieldDef(
                key="k", label="K", field_type="boolean", default=False
            ).json_default()
            is False
        )
        assert (
            ConfigFieldDef(key="k", label="K", field_type="string").json_default()
            is None
        )
