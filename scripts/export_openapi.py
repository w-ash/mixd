"""Export FastAPI's OpenAPI schema to web/openapi.json.

Post-processes SSE endpoints so Orval can parse them — FastAPI emits
``text/event-stream`` with a non-standard ``itemSchema`` key that
Orval rejects. We replace it with ``application/json`` + empty schema.
"""

import json
from pathlib import Path
from typing import Any

from src.interface.api.app import create_app


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


def main() -> None:
    app = create_app()
    schema = app.openapi()
    _normalize_sse_responses(schema)
    out = Path(__file__).resolve().parents[1] / "web" / "openapi.json"
    out.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Exported OpenAPI schema to {out}")


if __name__ == "__main__":
    main()
