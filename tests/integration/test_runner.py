from pathlib import Path

import httpx

from assertflow.models import SuiteConfig
from assertflow.runner import RunOptions, run_suite


async def test_chained_workflow_and_teardown_after_failure(tmp_path: Path) -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        if request.url.path == "/login":
            return httpx.Response(200, json={"token": "abc"})
        if request.url.path == "/orders":
            assert request.headers["Authorization"] == "Bearer abc"
            return httpx.Response(201, json={"id": 9, "state": "OPEN"})
        if request.url.path == "/verify":
            return httpx.Response(200, json={"state": "CANCELLED"})
        return httpx.Response(204)

    suite = SuiteConfig.model_validate(
        {
            "name": "order flow",
            "setup": [
                {
                    "name": "login",
                    "request": {"method": "POST", "url": "https://api.test/login"},
                    "assert": {"status": 200},
                    "extract": {"token": "body.token"},
                }
            ],
            "steps": [
                {
                    "name": "create",
                    "request": {
                        "method": "POST",
                        "url": "https://api.test/orders",
                        "headers": {"Authorization": "Bearer ${token}"},
                    },
                    "assert": {"status": 201, "json": {"state": "OPEN"}},
                    "extract": {"order_id": "body.id"},
                },
                {
                    "name": "verify",
                    "request": {"method": "GET", "url": "https://api.test/verify"},
                    "assert": {"json": {"state": "OPEN"}},
                },
                {
                    "name": "not-run",
                    "request": {"method": "GET", "url": "https://api.test/not-run"},
                },
            ],
            "teardown": [
                {
                    "name": "cleanup",
                    "request": {
                        "method": "DELETE",
                        "url": "https://api.test/orders/${order_id}",
                    },
                    "assert": {"status": 204},
                }
            ],
        }
    )

    result = await run_suite(
        suite,
        tmp_path / "flow.yaml",
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "failed"
    assert [step.status for step in result.steps] == [
        "passed",
        "passed",
        "failed",
        "skipped",
        "passed",
    ]
    assert observed == ["/login", "/orders", "/verify", "/orders/9"]


async def test_explicit_status_retry_records_attempts(tmp_path: Path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls < 3 else 200, json={"ok": calls == 3})

    suite = SuiteConfig.model_validate(
        {
            "name": "retry",
            "steps": [
                {
                    "name": "eventual",
                    "request": {"method": "GET", "url": "https://api.test/eventual"},
                    "retry": {"attempts": 3, "on_status": [503]},
                    "assert": {"status": 200},
                }
            ],
        }
    )

    result = await run_suite(
        suite,
        tmp_path / "retry.yaml",
        options=RunOptions(),
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "passed"
    assert [attempt.status_code for attempt in result.steps[0].attempts] == [503, 503, 200]


async def test_timeout_is_an_execution_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("bounded timeout", request=request)

    suite = SuiteConfig.model_validate(
        {
            "name": "timeout",
            "steps": [
                {
                    "name": "slow",
                    "request": {"method": "GET", "url": "https://api.test/slow", "timeout": 0.01},
                }
            ],
        }
    )

    result = await run_suite(
        suite,
        tmp_path / "timeout.yaml",
        transport=httpx.MockTransport(handler),
    )

    assert result.steps[0].status == "error"
    assert "timed out" in (result.steps[0].error or "")


async def test_put_form_raw_query_and_redirect_execution(tmp_path: Path) -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        if request.url.path == "/form":
            assert request.method == "PUT"
            assert request.extensions["assertflow_verify_tls"] is False
            assert request.url.params["page"] == "2"
            assert request.content == b"name=AssertFlow"
            return httpx.Response(201)
        if request.url.path == "/raw":
            assert request.content == b"raw-payload"
            return httpx.Response(200)
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "/final"})
        return httpx.Response(200, json={"redirected": True})

    suite = SuiteConfig.model_validate(
        {
            "name": "request bodies",
            "steps": [
                {
                    "name": "put form",
                    "request": {
                        "method": "PUT",
                        "url": "https://api.test/form",
                        "params": {"page": 2},
                        "form": {"name": "AssertFlow"},
                        "verify_tls": False,
                    },
                    "assert": {"status": 201},
                },
                {
                    "name": "raw body",
                    "request": {
                        "method": "POST",
                        "url": "https://api.test/raw",
                        "body": "raw-payload",
                    },
                    "assert": {"status": 200},
                },
                {
                    "name": "redirect",
                    "request": {
                        "method": "GET",
                        "url": "https://api.test/redirect",
                        "follow_redirects": True,
                    },
                    "assert": {"status": 200, "json": {"redirected": True}},
                },
            ],
        }
    )

    result = await run_suite(
        suite,
        tmp_path / "bodies.yaml",
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "passed"
    assert observed == ["/form", "/raw", "/redirect", "/final"]


async def test_setup_failure_skips_workflow_and_cleanup_failure_is_separate(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500 if request.url.path in {"/setup", "/cleanup"} else 200)

    suite = SuiteConfig.model_validate(
        {
            "name": "phase failures",
            "setup": [
                {
                    "name": "prepare",
                    "request": {"method": "POST", "url": "https://api.test/setup"},
                    "assert": {"status": 201},
                },
                {
                    "name": "later setup",
                    "request": {"method": "POST", "url": "https://api.test/later"},
                },
            ],
            "steps": [
                {"name": "main", "request": {"method": "GET", "url": "https://api.test/main"}}
            ],
            "teardown": [
                {
                    "name": "cleanup",
                    "request": {"method": "DELETE", "url": "https://api.test/cleanup"},
                    "assert": {"status": 204},
                }
            ],
        }
    )

    result = await run_suite(
        suite,
        tmp_path / "phases.yaml",
        transport=httpx.MockTransport(handler),
    )

    assert [step.status for step in result.steps] == ["failed", "skipped", "skipped", "failed"]
    assert result.cleanup_failures == ["cleanup: assertion failure"]


async def test_additional_secret_names_mask_results(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"account_number": "123456"})

    suite = SuiteConfig.model_validate(
        {
            "name": "custom secrets",
            "secrets": ["account_number"],
            "variables": {"account_number": "123456"},
            "steps": [
                {
                    "name": "account",
                    "request": {
                        "method": "GET",
                        "url": "https://api.test/account?account_number=${account_number}",
                    },
                    "extract": {"account_number": "body.account_number"},
                }
            ],
        }
    )

    result = await run_suite(
        suite,
        tmp_path / "secrets.yaml",
        transport=httpx.MockTransport(handler),
    )
    step = result.steps[0]

    assert "123456" not in step.url
    assert step.extracted == {"account_number": "********"}
    assert step.response is not None
    assert step.response["body"]["account_number"] == "********"


async def test_assertion_diagnostics_mask_secret_paths(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "actual-secret"})

    suite = SuiteConfig.model_validate(
        {
            "name": "secret assertion",
            "steps": [
                {
                    "name": "token",
                    "request": {"method": "GET", "url": "https://api.test/token"},
                    "assert": {"json": {"access_token": {"equals": "expected-secret"}}},
                }
            ],
        }
    )

    result = await run_suite(
        suite,
        tmp_path / "secret-assertion.yaml",
        transport=httpx.MockTransport(handler),
    )
    issue = result.steps[0].failures[0]

    assert issue.expected == "********"
    assert issue.actual == "********"
