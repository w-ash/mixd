"""ConfigFieldDef → ConfigFieldSchema projection for the node catalog endpoint."""

from src.application.workflows.nodes.config_fields import (
    ConfigFieldDef,
    ConfigFieldOption,
)
from src.interface.api.schemas.workflows import config_field_to_schema


def test_tuple_default_becomes_a_json_list() -> None:
    field = ConfigFieldDef(
        key="metrics",
        label="Metrics",
        field_type="multi_select",
        default=("a", "b"),
        options=(ConfigFieldOption("a", "A"), ConfigFieldOption("b", "B")),
    )

    schema = config_field_to_schema(field)

    assert schema.default == ["a", "b"]
    assert [o.value for o in schema.options] == ["a", "b"]


def test_scalar_default_and_bounds_pass_through() -> None:
    field = ConfigFieldDef(
        key="count", label="Count", field_type="number", default=10, min=1, max=100
    )

    schema = config_field_to_schema(field)

    assert schema.default == 10
    assert (schema.min, schema.max) == (1, 100)
    assert schema.options == []
