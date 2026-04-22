import asyncio
from pathlib import Path

import pytest

import assertflow.runner as runner_module
from assertflow.models import SuiteConfig, SuiteResult
from assertflow.runner import RunOptions, run_many


async def test_independent_suites_are_bounded_and_concurrent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    maximum = 0
    both_started = asyncio.Event()

    async def fake_run_suite(
        suite: SuiteConfig,
        source: Path,
        environment: dict[str, object],
        options: RunOptions,
    ) -> SuiteResult:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        active -= 1
        return SuiteResult(
            name=suite.name,
            source=str(source),
            status="passed",
            duration_ms=1,
            steps=[],
        )

    monkeypatch.setattr(runner_module, "run_suite", fake_run_suite)
    suite = SuiteConfig.model_validate(
        {
            "name": "parallel",
            "steps": [{"name": "one", "request": {"method": "GET", "url": "http://mock"}}],
        }
    )

    result = await run_many(
        [(suite, Path("a.yaml"), {}), (suite, Path("b.yaml"), {})],
        RunOptions(workers=2),
    )

    assert result.status == "passed"
    assert maximum == 2


async def test_repeat_runs_have_isolated_mock_state() -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "repeat",
            "mocks": [
                {
                    "request": {"method": "GET", "path": "/once"},
                    "response": {"status": 200},
                    "expect_calls": 1,
                }
            ],
            "steps": [
                {
                    "name": "once",
                    "request": {"method": "GET", "url": "http://mock/once"},
                    "assert": {"status": 200},
                }
            ],
        }
    )

    result = await run_many(
        [(suite, Path("repeat.yaml"), {})],
        RunOptions(workers=3, repeat=3),
    )

    assert len(result.suites) == 3
    assert all(item.status == "passed" for item in result.suites)
