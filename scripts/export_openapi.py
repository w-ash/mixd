"""Export FastAPI's OpenAPI schema to web/openapi.json.

Two post-processing passes run over what ``app.openapi()`` produces:

- SSE endpoints are normalized so Orval can parse them — FastAPI emits
  ``text/event-stream`` with a non-standard ``itemSchema`` key that
  Orval rejects. We replace it with ``application/json`` + empty schema.
- The SSE *payload* schemas are merged into ``components.schemas``. They
  describe stream frames rather than response bodies, so FastAPI never
  reaches them on its own, and without them the web client has no generated
  type for anything it receives over a stream.
"""

import json
from pathlib import Path
from typing import Any

from src.interface.api.app import create_app
from src.interface.api.schemas.sse_events import SSE_EVENT_SCHEMAS

_REF_TEMPLATE = "#/components/schemas/{model}"

# Name of the merged string-enum listing every SSE event name, so the client
# builds its event union from generated code instead of restating the list.
_EVENT_NAME_SCHEMA = "SseEventName"


def _normalize_sse_responses(schema: dict[str, Any]) -> None:
    """Replace text/event-stream responses with an Orval-compatible stub."""
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for response in operation.get("responses", {}).values():
                content = response.get("content", {})
                if "text/event-stream" in content:
                    response["content"] = {"application/json": {"schema": {}}}


def _collect_sse_schemas() -> dict[str, Any]:
    """Every SSE payload model (and its nested models) as JSON schemas.

    Serialization mode: these models describe frames the server *writes*, so
    the schema must be the output shape rather than the accepted input one.
    ``ref_template`` points nested ``$ref``s at ``components.schemas`` so a
    lifted ``$defs`` entry resolves once merged. A model reached from two
    events must generate identically both times — anything else is a name
    collision worth failing on.
    """
    collected: dict[str, Any] = {}
    for model in dict.fromkeys(SSE_EVENT_SCHEMAS.values()):
        json_schema = model.model_json_schema(
            ref_template=_REF_TEMPLATE, mode="serialization"
        )
        defs: dict[str, Any] = json_schema.pop("$defs", {})
        for name, sub_schema in ({model.__name__: json_schema} | defs).items():
            existing = collected.get(name)
            if existing is not None and existing != sub_schema:
                raise ValueError(f"Conflicting SSE schema definitions for '{name}'")
            collected[name] = sub_schema
    collected[_EVENT_NAME_SCHEMA] = {
        "title": _EVENT_NAME_SCHEMA,
        "description": "Wire name of an SSE event.",
        "type": "string",
        "enum": sorted(SSE_EVENT_SCHEMAS),
    }
    return collected


def _merge_sse_schemas(schema: dict[str, Any]) -> None:
    """Add the SSE payload schemas to ``components.schemas``.

    Idempotent: re-merging identical definitions is a no-op. A name already
    taken by a *different* schema raises — silently winning that clash would
    change an unrelated endpoint's generated type.
    """
    components: dict[str, Any] = schema.setdefault("components", {}).setdefault(
        "schemas", {}
    )
    for name, sub_schema in _collect_sse_schemas().items():
        existing = components.get(name)
        if existing is not None and existing != sub_schema:
            raise ValueError(
                f"SSE schema '{name}' collides with an existing component schema"
            )
        components[name] = sub_schema


def main() -> None:
    app = create_app()
    schema = app.openapi()
    _normalize_sse_responses(schema)
    _merge_sse_schemas(schema)
    out = Path(__file__).resolve().parents[1] / "web" / "openapi.json"
    out.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Exported OpenAPI schema to {out}")


if __name__ == "__main__":
    main()
