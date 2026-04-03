"""Typed, recursive variable resolution without expression evaluation."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from assertflow.errors import VariableResolutionError

_PLACEHOLDER = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_ESCAPED_OPEN = "\x00ASSERTFLOW_LITERAL_OPEN\x00"


class VariableContext:
    """Suite-local values with deterministic precedence and typed substitution."""

    def __init__(self, values: Mapping[str, Any] | None = None) -> None:
        self._values: dict[str, Any] = dict(values or {})

    @classmethod
    def from_sources(
        cls,
        environment: Mapping[str, Any] | None = None,
        suite: Mapping[str, Any] | None = None,
    ) -> VariableContext:
        values: dict[str, Any] = dict(os.environ)
        values.update(environment or {})
        values.update(suite or {})
        return cls(values)

    def set(self, name: str, value: Any) -> None:
        self._values[name] = value

    def resolve(self, value: Any) -> Any:
        return self._resolve(value, stack=())

    def _resolve(self, value: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(value, str):
            return self._resolve_string(value, stack)
        if isinstance(value, list):
            return [self._resolve(item, stack) for item in value]
        if isinstance(value, tuple):
            return tuple(self._resolve(item, stack) for item in value)
        if isinstance(value, dict):
            return {key: self._resolve(item, stack) for key, item in value.items()}
        return value

    def _lookup(self, name: str, stack: tuple[str, ...]) -> Any:
        if name in stack:
            cycle = " -> ".join((*stack, name))
            raise VariableResolutionError(f"recursive variable reference: {cycle}")
        if name not in self._values:
            raise VariableResolutionError(f"undefined variable: {name}")
        return self._resolve(self._values[name], (*stack, name))

    def _resolve_string(self, template: str, stack: tuple[str, ...]) -> Any:
        protected = template.replace("$${", f"{_ESCAPED_OPEN}{{")
        exact = _PLACEHOLDER.fullmatch(protected)
        if exact:
            return self._lookup(exact.group(1), stack)

        def replace(match: re.Match[str]) -> str:
            resolved = self._lookup(match.group(1), stack)
            if isinstance(resolved, (dict, list, tuple)):
                raise VariableResolutionError(
                    f"variable {match.group(1)!r} is structured and cannot be embedded in text"
                )
            if resolved is None:
                return "null"
            if isinstance(resolved, bool):
                return str(resolved).lower()
            return str(resolved)

        rendered = _PLACEHOLDER.sub(replace, protected)
        return rendered.replace(f"{_ESCAPED_OPEN}{{", "${")
