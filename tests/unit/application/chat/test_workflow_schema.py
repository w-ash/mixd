"""The node-catalog → JSON Schema bridge for generate_workflow_def.

Guards the structured-output constraints (anyOf not oneOf, prose ranges not
minimum/maximum, additionalProperties: false everywhere) and byte-stability
of the derived schema — it renders into the cached tool prefix, so any
nondeterminism silently invalidates the prompt cache per request.
"""

import json

from src.application.chat.workflow_schema import (
    build_workflow_def_schema,
    workflow_def_to_dict,
)
from src.application.workflows.nodes.config_fields import get_node_config_fields
from src.application.workflows.nodes.registry import list_nodes
from tests.fixtures import make_workflow_def

_FORBIDDEN_KEYS = {"oneOf", "minimum", "maximum", "strict", "const"}


def _walk(node: object):
    """Yield every dict in the schema tree."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _branches(schema: dict) -> list[dict]:
    return schema["properties"]["workflow_def"]["properties"]["tasks"]["items"]["anyOf"]


class TestSchemaShape:
    def test_every_registered_node_has_a_branch(self):
        branch_types = {
            b["properties"]["type"]["enum"][0]
            for b in _branches(build_workflow_def_schema())
        }
        assert branch_types == set(list_nodes())

    def test_no_forbidden_constructs_anywhere(self):
        for d in _walk(build_workflow_def_schema()):
            assert not (_FORBIDDEN_KEYS & d.keys()), f"forbidden key in {d.keys()}"

    def test_every_object_forbids_additional_properties(self):
        for d in _walk(build_workflow_def_schema()):
            if d.get("type") == "object":
                assert d.get("additionalProperties") is False

    def test_required_config_fields_enforced_per_node(self):
        by_type = {
            b["properties"]["type"]["enum"][0]: b
            for b in _branches(build_workflow_def_schema())
        }
        for node_id, fields in get_node_config_fields().items():
            if node_id not in by_type:
                continue  # config entry without a registered node
            required_keys = [f.key for f in fields if f.required]
            branch = by_type[node_id]
            if required_keys:
                assert branch["properties"]["config"]["required"] == required_keys
                assert "config" in branch["required"]
            else:
                assert "required" not in branch["properties"]["config"]

    def test_select_fields_become_enums_and_ranges_become_prose(self):
        by_type = {
            b["properties"]["type"]["enum"][0]: b
            for b in _branches(build_workflow_def_schema())
        }
        # source.preferred_tracks: state is a required select, limit is 1-10000.
        config = by_type["source.preferred_tracks"]["properties"]["config"][
            "properties"
        ]
        assert set(config["state"]["enum"]) == {"star", "yah", "hmm", "nah"}
        assert "between 1 and 10000" in config["limit"]["description"]


class TestDeterminism:
    def test_derivation_is_byte_stable(self):
        first = json.dumps(build_workflow_def_schema())
        build_workflow_def_schema.cache_clear()
        assert json.dumps(build_workflow_def_schema()) == first


class TestSerializer:
    def test_round_trip_normalizes(self):
        workflow_def = make_workflow_def()
        out = workflow_def_to_dict(workflow_def)
        task = out["tasks"][0]
        # Canonical shape: upstream/config always present, result_key elided.
        assert task["upstream"] == []
        assert task["config"] == {"service": "spotify"}
        assert "result_key" not in task
        assert out["version"] == "1.0"
