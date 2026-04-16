import pytest
from pydantic import ValidationError

from assertflow.models import RequestConfig, RetryConfig, SuiteConfig


def test_scalar_assertion_is_equals_rule() -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "assertions",
            "steps": [
                {
                    "name": "one",
                    "request": {"method": "GET", "url": "https://example.test"},
                    "assert": {"json": {"state": "ready"}},
                }
            ],
        }
    )

    rule = suite.steps[0].assertions.json_values["state"]
    assert rule.equals == "ready"
    assert "equals" in rule.model_fields_set


def test_rejects_duplicate_step_names_across_phases() -> None:
    with pytest.raises(ValidationError, match="step names must be unique"):
        SuiteConfig.model_validate(
            {
                "name": "duplicate",
                "setup": [{"name": "same", "request": {"method": "GET", "url": "http://mock/a"}}],
                "steps": [{"name": "same", "request": {"method": "GET", "url": "http://mock/b"}}],
            }
        )


def test_request_security_and_transport_defaults() -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "defaults",
            "steps": [
                {
                    "name": "put",
                    "request": {"method": "PUT", "url": "https://api.test/item"},
                }
            ],
        }
    )

    request = suite.steps[0].request
    assert request.verify_tls is True
    assert request.follow_redirects is False

    explicit = request.model_copy(update={"verify_tls": False, "follow_redirects": True})
    assert explicit.verify_tls is False
    assert explicit.follow_redirects is True


def test_rejects_invalid_urls_and_status_codes() -> None:
    with pytest.raises(ValidationError, match="absolute http"):
        RequestConfig.model_validate({"method": "GET", "url": "/relative"})
    with pytest.raises(ValidationError, match="between 100 and 599"):
        RetryConfig.model_validate({"attempts": 2, "on_status": [999]})
