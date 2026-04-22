from pathlib import Path

import pytest

from assertflow.benchmark import benchmark_suite
from assertflow.errors import ConfigurationError
from assertflow.models import SuiteConfig


async def test_benchmark_reports_measured_results(tmp_path: Path) -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "benchmark",
            "mocks": [
                {
                    "request": {"method": "GET", "path": "/health"},
                    "response": {"status": 200},
                    "expect_calls": 1,
                }
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

    result = await benchmark_suite(suite, tmp_path / "bench.yaml", {}, 5, 2)

    assert result.requests == 5
    assert result.passed == 5
    assert result.failed == 0
    assert result.throughput > 0
    assert result.p50_ms <= result.p95_ms <= result.p99_ms
    assert result.exit_code == 0


async def test_benchmark_rejects_stateful_workflow(tmp_path: Path) -> None:
    suite = SuiteConfig.model_validate(
        {
            "name": "workflow",
            "steps": [
                {"name": "one", "request": {"method": "GET", "url": "http://mock/one"}},
                {"name": "two", "request": {"method": "GET", "url": "http://mock/two"}},
            ],
        }
    )

    with pytest.raises(ConfigurationError, match="exactly one"):
        await benchmark_suite(suite, tmp_path / "workflow.yaml", {}, 2, 1)
