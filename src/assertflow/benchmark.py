"""Lightweight, reproducible benchmarking for single-request suites."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any

from assertflow.errors import ConfigurationError
from assertflow.models import BenchmarkResult, RunResult, SuiteConfig, SuiteResult
from assertflow.reporting import exit_code
from assertflow.runner import RunOptions, run_suite


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


async def benchmark_suite(
    suite: SuiteConfig,
    source: Path,
    environment: dict[str, Any],
    requests: int,
    concurrency: int,
) -> BenchmarkResult:
    if requests < 1 or concurrency < 1:
        raise ConfigurationError("requests and concurrency must be at least 1")
    if suite.setup or suite.teardown or len(suite.steps) != 1:
        raise ConfigurationError(
            "benchmark requires exactly one workflow step and no setup or teardown"
        )

    semaphore = asyncio.Semaphore(concurrency)

    async def one() -> SuiteResult:
        async with semaphore:
            return await run_suite(suite, source, environment, RunOptions())

    started = perf_counter()
    results = await asyncio.gather(*(one() for _ in range(requests)))
    duration_ms = (perf_counter() - started) * 1000
    latencies = [result.duration_ms for result in results]
    passed = sum(result.status == "passed" for result in results)
    aggregate = RunResult(
        status="passed" if passed == requests else "failed",
        duration_ms=duration_ms,
        suites=results,
    )
    return BenchmarkResult(
        requests=requests,
        passed=passed,
        failed=requests - passed,
        concurrency=concurrency,
        duration_ms=duration_ms,
        throughput=requests / (duration_ms / 1000),
        mean_ms=fmean(latencies),
        p50_ms=_percentile(latencies, 0.50),
        p95_ms=_percentile(latencies, 0.95),
        p99_ms=_percentile(latencies, 0.99),
        exit_code=exit_code(aggregate),
    )
