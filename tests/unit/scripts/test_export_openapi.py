"""Unit tests for the OpenAPI export post-processing.

The SSE payload models describe stream frames, not response bodies, so FastAPI
never reaches them: the merge in ``export_openapi`` is the only thing that puts
them in front of the client's code generator.
"""

from typing import Any

import pytest

from scripts.export_openapi import (
    _EVENT_NAME_SCHEMA,
    _collect_sse_schemas,
    _merge_sse_schemas,
    _normalize_sse_responses,
)
from src.interface.api.schemas.sse_events import SSE_EVENT_SCHEMAS


class TestCollectSseSchemas:
    def test_includes_every_registered_model(self) -> None:
        collected = _collect_sse_schemas()

        for model in SSE_EVENT_SCHEMAS.values():
            assert model.__name__ in collected

    def test_lifts_nested_models_and_status_enums(self) -> None:
        collected = _collect_sse_schemas()

        # A nested model and a Literal alias both arrive via ``$defs``; without
        # lifting them the events' ``$ref``s would dangle.
        assert "SseNodePreviewSummary" in collected
        assert collected["SseFinalStatus"]["enum"]

    def test_emits_the_event_name_enum(self) -> None:
        collected = _collect_sse_schemas()

        assert collected[_EVENT_NAME_SCHEMA]["enum"] == sorted(SSE_EVENT_SCHEMAS)

    def test_refs_point_at_components(self) -> None:
        preview = _collect_sse_schemas()["SsePreviewCompleteEvent"]

        assert preview["properties"]["node_summaries"]["items"] == {
            "$ref": "#/components/schemas/SseNodePreviewSummary"
        }


class TestMergeSseSchemas:
    def test_adds_schemas_and_is_idempotent(self) -> None:
        schema: dict[str, Any] = {"components": {"schemas": {"Existing": {}}}}

        _merge_sse_schemas(schema)
        first = dict(schema["components"]["schemas"])
        _merge_sse_schemas(schema)

        assert schema["components"]["schemas"] == first
        assert "Existing" in first
        assert "SseOperationProgressEvent" in first

    def test_creates_the_components_section_when_absent(self) -> None:
        schema: dict[str, Any] = {}

        _merge_sse_schemas(schema)

        assert "SseNodeStatusEvent" in schema["components"]["schemas"]

    def test_raises_on_a_name_collision(self) -> None:
        schema: dict[str, Any] = {
            "components": {"schemas": {"SseNodeStatusEvent": {"type": "string"}}}
        }

        with pytest.raises(ValueError, match="collides"):
            _merge_sse_schemas(schema)


class TestNormalizeSseResponses:
    def test_replaces_event_stream_content(self) -> None:
        schema: dict[str, Any] = {
            "paths": {
                "/stream": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "text/event-stream": {"itemSchema": {"type": "x"}}
                                }
                            }
                        }
                    },
                    "summary": "not an operation",
                }
            }
        }

        _normalize_sse_responses(schema)

        response = schema["paths"]["/stream"]["get"]["responses"]["200"]
        assert response["content"] == {"application/json": {"schema": {}}}
