import httpx
import pytest

from assertflow.errors import ExtractionError
from assertflow.extraction import extract_response_value, get_path


def test_nested_and_array_paths() -> None:
    body = {"orders": [{"id": "one"}, {"id": None}]}

    assert get_path(body, "orders[0].id") == "one"
    assert get_path(body, "orders.1.id") is None


def test_extracts_body_header_and_status() -> None:
    response = httpx.Response(
        201,
        headers={"X-Request-Id": "req-1"},
        json={"item": {"id": 42}},
    )

    assert extract_response_value(response, "body.item.id") == 42
    assert extract_response_value(response, "headers.x-request-id") == "req-1"
    assert extract_response_value(response, "status") == 201


def test_missing_path_and_malformed_json_fail() -> None:
    response = httpx.Response(200, text="not-json")

    with pytest.raises(ExtractionError, match="not valid JSON"):
        extract_response_value(response, "body.id")
