from pathlib import Path

import pytest

from assertflow.models import SuiteConfig
from assertflow.runner import run_suite


async def test_sequential_mock_responses_drive_explicit_retry(tmp_path: Path) -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "mock retry",
            "mocks": [
                {
                    "name": "dependency",
                    "request": {"method": "GET", "path": "/dependency"},
                    "responses": [
                        {"status": 503, "json": {"state": "unavailable"}},
                        {"status": 200, "json": {"state": "ready"}},
                    ],
                    "expect_calls": 2,
                }
            ],
            "steps": [
                {
                    "name": "call dependency",
                    "request": {"method": "GET", "url": "http://mock/dependency"},
                    "retry": {"attempts": 2, "on_status": [503]},
                    "assert": {"status": 200, "json": {"state": "ready"}},
                }
            ],
        }
    )

    result = await run_suite(suite, tmp_path / "mock.yaml")

    assert result.status == "passed"
    assert [attempt.status_code for attempt in result.steps[0].attempts] == [503, 200]
    assert result.mock_failures == []


async def test_mock_call_verification_is_a_suite_failure(tmp_path: Path) -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "mock verification",
            "mocks": [
                {
                    "request": {"method": "POST", "path": "/payment"},
                    "response": {"status": 202},
                    "expect_calls": 1,
                },
                {
                    "request": {"method": "GET", "path": "/health"},
                    "response": {"status": 200},
                },
            ],
            "steps": [
                {
                    "name": "health",
                    "request": {"method": "GET", "url": "http://mock/health"},
                    "assert": {"status": 200},
                }
            ],
        }
    )

    result = await run_suite(suite, tmp_path / "verify.yaml")

    assert result.status == "failed"
    assert "expected 1 call(s), observed 0" in result.mock_failures[0]


async def test_mock_verifies_headers_query_and_json_without_leaking_secrets(
    tmp_path: Path,
) -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "request verification",
            "secrets": ["x-customer-id", "session_id"],
            "mocks": [
                {
                    "name": "create payment",
                    "request": {
                        "method": "POST",
                        "path": "/payment",
                        "headers": {"X-Customer-Id": "expected-customer"},
                        "params": {"mode": "capture"},
                        "json": {"amount": 10, "session_id": "expected-session"},
                    },
                    "response": {"status": 202},
                    "expect_calls": 1,
                }
            ],
            "steps": [
                {
                    "name": "payment",
                    "request": {
                        "method": "POST",
                        "url": "http://mock/payment",
                        "headers": {"X-Customer-Id": "actual-customer"},
                        "params": {"mode": "authorize"},
                        "json": {"amount": 11, "session_id": "actual-session"},
                    },
                    "assert": {"status": 202},
                }
            ],
        }
    )

    result = await run_suite(suite, tmp_path / "verify-request.yaml")
    rendered = "\n".join(result.mock_failures)

    assert result.status == "failed"
    assert "header 'X-Customer-Id'" in rendered
    assert "query 'mode'" in rendered
    assert "JSON body" in rendered
    assert "actual-customer" not in rendered
    assert "actual-session" not in rendered


async def test_mock_verifies_raw_request_body(tmp_path: Path) -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "raw body",
            "mocks": [
                {
                    "request": {"method": "POST", "path": "/raw", "body": "exact"},
                    "response": {"status": 200},
                }
            ],
            "steps": [
                {
                    "name": "raw",
                    "request": {"method": "POST", "url": "http://mock/raw", "body": "wrong"},
                    "assert": {"status": 200},
                }
            ],
        }
    )

    result = await run_suite(suite, tmp_path / "raw.yaml")

    assert result.status == "failed"
    assert "raw request body did not match" in result.mock_failures[0]


async def test_mock_delay_uses_configured_bounded_delay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[float] = []

    async def fake_sleep(delay: float) -> None:
        observed.append(delay)

    monkeypatch.setattr("assertflow.mocks.asyncio.sleep", fake_sleep)
    suite = SuiteConfig.model_validate(
        {
            "name": "delay",
            "mocks": [
                {
                    "request": {"method": "GET", "path": "/slow"},
                    "response": {"status": 200, "delay_ms": 37},
                }
            ],
            "steps": [
                {
                    "name": "slow",
                    "request": {"method": "GET", "url": "http://mock/slow"},
                    "assert": {"status": 200},
                }
            ],
        }
    )

    result = await run_suite(suite, tmp_path / "delay.yaml")

    assert result.status == "passed"
    assert observed == [0.037]


async def test_slow_mock_can_drive_deterministic_timeout(tmp_path: Path) -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "slow timeout",
            "mocks": [
                {
                    "request": {"method": "GET", "path": "/slow"},
                    "response": {"status": 200, "delay_ms": 50},
                }
            ],
            "steps": [
                {
                    "name": "bounded slow call",
                    "request": {
                        "method": "GET",
                        "url": "http://mock/slow",
                        "timeout": 0.005,
                    },
                }
            ],
        }
    )

    result = await run_suite(suite, tmp_path / "slow-timeout.yaml")

    assert result.steps[0].status == "error"
    assert "timed out" in (result.steps[0].error or "")
