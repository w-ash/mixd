"""Unit tests for shared JSON:API error-body parsing.

Covers the defensive contract both connector classifiers (Apple Music,
Tidal) rely on: valid JSON:API error envelopes parse to the first error
object, while an absent, non-JSON, empty, or unexpectedly-shaped body reads
as "no error object" — never raises. Also covers the detail-then-title
precedence of ``error_detail``.
"""

import httpx2

from src.infrastructure.connectors._shared.json_api import (
    JsonApiError,
    error_detail,
    first_json_api_error,
)


def _response(
    *, json_body: dict[str, object] | None = None, text: str | None = None
) -> httpx2.Response:
    request = httpx2.Request("GET", "https://api.example.com/v1/resource")
    if json_body is not None:
        return httpx2.Response(400, request=request, json=json_body)
    return httpx2.Response(400, request=request, text=text or "")


class TestFirstJsonApiError:
    def test_first_error_extracted_from_errors_array(self):
        body = {
            "errors": [
                {
                    "id": "ERR1",
                    "status": "400",
                    "code": "40005",
                    "title": "Bad Request",
                    "detail": "Missing filter",
                },
                {"status": "400", "title": "Second", "detail": "Ignored"},
            ]
        }

        error = first_json_api_error(_response(json_body=body))

        assert error is not None
        assert error.id == "ERR1"
        assert error.status == "400"
        assert error.code == "40005"
        assert error.title == "Bad Request"
        assert error.detail == "Missing filter"

    def test_all_error_fields_are_optional(self):
        error = first_json_api_error(_response(json_body={"errors": [{}]}))

        assert error is not None
        assert error.id is None
        assert error.status is None
        assert error.code is None
        assert error.title is None
        assert error.detail is None

    def test_unknown_error_fields_are_ignored(self):
        body = {"errors": [{"detail": "boom", "meta": {"trace": "abc"}}]}

        error = first_json_api_error(_response(json_body=body))

        assert error is not None
        assert error.detail == "boom"

    def test_empty_errors_array_returns_none(self):
        assert first_json_api_error(_response(json_body={"errors": []})) is None

    def test_no_errors_key_returns_none(self):
        assert first_json_api_error(_response(json_body={"data": []})) is None

    def test_non_json_body_returns_none(self):
        assert first_json_api_error(_response(text="<html>Forbidden</html>")) is None

    def test_empty_body_returns_none(self):
        assert first_json_api_error(_response(text="")) is None

    def test_non_object_error_entries_return_none(self):
        assert (
            first_json_api_error(_response(json_body={"errors": ["not-an-object"]}))
            is None
        )


class TestErrorDetail:
    def test_detail_wins_over_title(self):
        error = JsonApiError(title="Unauthorized", detail="The access token expired")

        assert error_detail(error) == "The access token expired"

    def test_falls_back_to_title(self):
        assert error_detail(JsonApiError(title="Unauthorized")) == "Unauthorized"

    def test_empty_strings_count_as_absent(self):
        assert error_detail(JsonApiError(title="", detail="")) is None

    def test_none_error_reads_as_no_detail(self):
        assert error_detail(None) is None
