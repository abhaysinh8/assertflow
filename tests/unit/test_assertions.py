from pathlib import Path

import httpx
import pytest

from assertflow.assertions import evaluate_assertions, evaluate_rule
from assertflow.errors import ConfigurationError
from assertflow.extraction import MISSING
from assertflow.models import AssertionRule, AssertionsConfig


@pytest.mark.parametrize(
    ("actual", "rule"),
    [
        ("ready", {"equals": "ready"}),
        ("ready", {"not_equals": "failed"}),
        (12, {"greater_than": 10, "less_than_or_equal": 12}),
        ("application/json", {"contains": "json", "starts_with": "application"}),
        ("order-123", {"regex": r"^order-\d+$", "ends_with": "123"}),
        ([1, 2], {"length": 2, "not_empty": True, "contains": 2}),
        ({}, {"empty": True, "type": "object"}),
        (None, {"type": "null"}),
    ],
)
def test_rule_operators_pass(actual: object, rule: dict[str, object]) -> None:
    assert evaluate_rule("body.value", actual, AssertionRule.model_validate(rule)) == []


def test_missing_path_reports_exists_and_dependent_checks() -> None:
    issues = evaluate_rule(
        "body.id",
        MISSING,
        AssertionRule.model_validate({"exists": True, "type": "integer"}),
    )

    assert [issue.assertion for issue in issues] == ["exists", "path"]


@pytest.mark.parametrize("rule", [{"empty": False}, {"not_empty": False}])
def test_collection_checks_reject_scalar_values(rule: dict[str, object]) -> None:
    issues = evaluate_rule("body.value", 42, AssertionRule.model_validate(rule))

    assert len(issues) == 1
    assert "no length" in issues[0].message


def test_status_header_json_and_schema(tmp_path: Path) -> None:
    (tmp_path / "schema.json").write_text(
        '{"type":"object","required":["id"],"properties":{"id":{"type":"integer"}}}',
        encoding="utf-8",
    )
    response = httpx.Response(
        200,
        headers={"Content-Type": "application/json"},
        json={"id": 7, "state": "open"},
    )
    config = AssertionsConfig.model_validate(
        {
            "status": 200,
            "headers": {"content-type": {"contains": "json"}},
            "json": {"id": {"type": "integer"}, "state": "open"},
            "schema": "schema.json",
        }
    )

    assert evaluate_assertions(response, config, tmp_path) == []


def test_malformed_json_is_one_clear_failure(tmp_path: Path) -> None:
    response = httpx.Response(200, text="<html>")
    config = AssertionsConfig.model_validate({"json": {"id": {"exists": True}}})

    issues = evaluate_assertions(response, config, tmp_path)

    assert len(issues) == 1
    assert "not valid JSON" in issues[0].message


def test_schema_cannot_escape_suite_directory(tmp_path: Path) -> None:
    response = httpx.Response(200, json={})
    config = AssertionsConfig.model_validate({"schema": "../outside.json"})

    with pytest.raises(ConfigurationError, match="escapes the suite directory"):
        evaluate_assertions(response, config, tmp_path)


def test_schema_response_failure_reports_precise_body_path(tmp_path: Path) -> None:
    (tmp_path / "schema.json").write_text(
        '{"type":"object","properties":{"quantity":{"type":"integer"}}}',
        encoding="utf-8",
    )
    response = httpx.Response(200, json={"quantity": "10"})
    config = AssertionsConfig.model_validate({"schema": "schema.json"})

    issues = evaluate_assertions(response, config, tmp_path)

    assert len(issues) == 1
    assert issues[0].path == "body.quantity"
    assert "not of type" in issues[0].message


def test_invalid_schema_document_is_configuration_error(tmp_path: Path) -> None:
    (tmp_path / "schema.json").write_text(
        '{"type": "definitely-not-a-json-type"}', encoding="utf-8"
    )
    response = httpx.Response(200, json={})
    config = AssertionsConfig.model_validate({"schema": "schema.json"})

    with pytest.raises(ConfigurationError, match="invalid JSON Schema"):
        evaluate_assertions(response, config, tmp_path)
