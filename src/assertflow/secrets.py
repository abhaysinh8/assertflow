"""Conservative redaction for reports and stored snapshots."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MASK = "********"
_SECRET_FRAGMENTS = (
    "authorization",
    "proxy-authorization",
    "api-key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
)


def is_secret_name(name: str, additional: set[str] | None = None) -> bool:
    lowered = name.lower().replace("_", "-")
    if additional and lowered in {item.lower().replace("_", "-") for item in additional}:
        return True
    return any(fragment in lowered for fragment in _SECRET_FRAGMENTS)


def redact(value: Any, additional: set[str] | None = None) -> Any:
    if isinstance(value, dict):
        return {
            str(key): MASK if is_secret_name(str(key), additional) else redact(item, additional)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, additional) for item in value]
    if isinstance(value, tuple):
        return [redact(item, additional) for item in value]
    return value


def sanitize_url(url: str, additional: set[str] | None = None) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    query = [
        (key, MASK if is_secret_name(key, additional) else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )
