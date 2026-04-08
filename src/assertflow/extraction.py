"""Safe response path traversal for assertions and variable extraction."""

from __future__ import annotations

import re
from typing import Any

import httpx

from assertflow.errors import ExtractionError


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


MISSING = _Missing()
_ARRAY_INDEX = re.compile(r"\[(\d+)\]")


def get_path(value: Any, path: str, *, default: Any = MISSING) -> Any:
    """Read dot-separated mapping keys and numeric list indexes."""
    if not path:
        return value
    normalized = _ARRAY_INDEX.sub(r".\1", path).strip(".")
    current = value
    for segment in normalized.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if index >= len(current):
                return default
            current = current[index]
        else:
            return default
    return current


def response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ExtractionError("response body is not valid JSON") from exc


def extract_response_value(response: httpx.Response, path: str) -> Any:
    if path == "status":
        return response.status_code
    if path == "body":
        return response_json(response)
    if path.startswith("body."):
        value = get_path(response_json(response), path.removeprefix("body."))
    elif path.startswith("headers."):
        name = path.removeprefix("headers.")
        value = response.headers.get(name, MISSING)
    else:
        raise ExtractionError(
            f"invalid extraction path {path!r}; expected body.*, headers.*, body, or status"
        )
    if value is MISSING:
        raise ExtractionError(f"extraction path not found: {path}")
    return value


def apply_extractions(
    response: httpx.Response,
    extractions: dict[str, str],
) -> dict[str, Any]:
    return {name: extract_response_value(response, path) for name, path in extractions.items()}
