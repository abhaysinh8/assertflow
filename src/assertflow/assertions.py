"""Reusable response assertion engine with structured diagnostics."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from assertflow.errors import ConfigurationError
from assertflow.extraction import MISSING, get_path
from assertflow.models import AssertionIssue, AssertionRule, AssertionsConfig


def _issue(path: str, assertion: str, expected: Any, actual: Any, message: str) -> AssertionIssue:
    return AssertionIssue(
        path=path,
        assertion=assertion,
        expected=expected,
        actual=repr(actual) if actual is MISSING else actual,
        message=message,
    )


def _matches_type(value: Any, expected: str) -> bool:
    checks = {
        "null": lambda item: item is None,
        "boolean": lambda item: isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "string": lambda item: isinstance(item, str),
        "array": lambda item: isinstance(item, list),
        "object": lambda item: isinstance(item, dict),
    }
    return checks[expected](value)


def evaluate_rule(path: str, actual: Any, rule: AssertionRule) -> list[AssertionIssue]:
    issues: list[AssertionIssue] = []
    fields = rule.model_fields_set
    missing = actual is MISSING

    if "exists" in fields:
        exists = not missing
        if exists != rule.exists:
            issues.append(_issue(path, "exists", rule.exists, exists, "existence check failed"))
    if missing:
        remaining = fields - {"exists"}
        if remaining:
            issues.append(
                _issue(path, "path", "present", actual, "value is missing; other checks cannot run")
            )
        return issues

    comparisons: tuple[tuple[str, Any, bool], ...] = (
        ("equals", rule.equals, actual == rule.equals),
        ("not_equals", rule.not_equals, actual != rule.not_equals),
    )
    for operator, expected, passed in comparisons:
        if operator in fields and not passed:
            issues.append(_issue(path, operator, expected, actual, f"{operator} check failed"))

    if (
        "type_name" in fields
        and rule.type_name is not None
        and not _matches_type(actual, rule.type_name)
    ):
        issues.append(
            _issue(path, "type", rule.type_name, type(actual).__name__, "type check failed")
        )

    numeric: tuple[tuple[str, float | None, Callable[[float, float], bool]], ...] = (
        ("greater_than", rule.greater_than, lambda a, b: a > b),
        ("greater_than_or_equal", rule.greater_than_or_equal, lambda a, b: a >= b),
        ("less_than", rule.less_than, lambda a, b: a < b),
        ("less_than_or_equal", rule.less_than_or_equal, lambda a, b: a <= b),
    )
    for operator, expected, numeric_compare in numeric:
        if operator not in fields or expected is None:
            continue
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            issues.append(_issue(path, operator, expected, actual, "actual value is not numeric"))
        elif not numeric_compare(actual, expected):
            issues.append(_issue(path, operator, expected, actual, f"{operator} check failed"))

    if "contains" in fields:
        try:
            passed = rule.contains in actual
        except TypeError:
            passed = False
        if not passed:
            issues.append(_issue(path, "contains", rule.contains, actual, "contains check failed"))

    string_checks = (
        ("starts_with", rule.starts_with, str.startswith),
        ("ends_with", rule.ends_with, str.endswith),
    )
    for operator, expected, string_compare in string_checks:
        if (
            operator in fields
            and expected is not None
            and (not isinstance(actual, str) or not string_compare(actual, expected))
        ):
            issues.append(_issue(path, operator, expected, actual, f"{operator} check failed"))

    if (
        "regex" in fields
        and rule.regex is not None
        and (not isinstance(actual, str) or re.search(rule.regex, actual) is None)
    ):
        issues.append(_issue(path, "regex", rule.regex, actual, "regular expression did not match"))

    if "length" in fields:
        try:
            actual_length = len(actual)
        except TypeError:
            issues.append(_issue(path, "length", rule.length, actual, "actual value has no length"))
        else:
            if actual_length != rule.length:
                issues.append(
                    _issue(path, "length", rule.length, actual_length, "length check failed")
                )

    if "empty" in fields:
        try:
            empty = len(actual) == 0
        except TypeError:
            issues.append(_issue(path, "empty", rule.empty, actual, "actual value has no length"))
        else:
            if empty != rule.empty:
                issues.append(_issue(path, "empty", rule.empty, empty, "empty check failed"))
    if "not_empty" in fields:
        try:
            not_empty = len(actual) > 0
        except TypeError:
            issues.append(
                _issue(path, "not_empty", rule.not_empty, actual, "actual value has no length")
            )
        else:
            if not_empty != rule.not_empty:
                issues.append(
                    _issue(path, "not_empty", rule.not_empty, not_empty, "not-empty check failed")
                )
    return issues


def _safe_schema_path(base_dir: Path, configured: str) -> Path:
    root = base_dir.resolve()
    candidate = Path(configured)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ConfigurationError(f"schema path escapes the suite directory: {configured}")
    return resolved


def evaluate_assertions(
    response: httpx.Response,
    config: AssertionsConfig,
    base_dir: Path,
) -> list[AssertionIssue]:
    issues: list[AssertionIssue] = []
    if config.status is not None:
        allowed = [config.status] if isinstance(config.status, int) else config.status
        if response.status_code not in allowed:
            issues.append(
                _issue("status", "equals", allowed, response.status_code, "unexpected HTTP status")
            )

    for name, rule in config.headers.items():
        issues.extend(evaluate_rule(f"headers.{name}", response.headers.get(name, MISSING), rule))

    body: Any = MISSING
    if config.json_values or config.schema_path:
        try:
            body = response.json()
        except ValueError:
            issues.append(
                _issue(
                    "body",
                    "json",
                    "valid JSON",
                    response.text,
                    "response body is not valid JSON",
                )
            )
            return issues

    for path, rule in config.json_values.items():
        issues.extend(evaluate_rule(f"body.{path}", get_path(body, path), rule))

    if config.schema_path:
        try:
            schema_path = _safe_schema_path(base_dir, config.schema_path)
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except ConfigurationError:
            raise
        except (OSError, json.JSONDecodeError, SchemaError) as exc:
            raise ConfigurationError(f"invalid JSON Schema {config.schema_path}: {exc}") from exc
        for error in sorted(validator.iter_errors(body), key=lambda item: list(item.path)):
            suffix = ".".join(str(part) for part in error.absolute_path)
            path = f"body.{suffix}" if suffix else "body"
            issues.append(
                _issue(path, "schema", error.validator_value, error.instance, error.message)
            )
    return issues
