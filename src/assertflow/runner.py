"""Ordered suite execution and bounded concurrency across independent suites."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from assertflow.assertions import evaluate_assertions
from assertflow.errors import (
    AssertFlowError,
    ConfigurationError,
    ExtractionError,
    RequestExecutionError,
    VariableResolutionError,
)
from assertflow.extraction import apply_extractions
from assertflow.http import HttpExecutor, capture_response
from assertflow.mocks import MockService
from assertflow.models import (
    AssertionIssue,
    AttemptResult,
    Phase,
    RequestConfig,
    RunResult,
    StepConfig,
    StepResult,
    SuiteConfig,
    SuiteResult,
)
from assertflow.regression import compare_snapshot, save_snapshot
from assertflow.secrets import MASK, is_secret_name, redact, sanitize_url
from assertflow.variables import VariableContext


@dataclass(frozen=True, slots=True)
class RunOptions:
    workers: int = 1
    repeat: int = 1
    continue_on_fail: bool = False
    regression: bool = False
    snapshot: bool = False
    snapshot_dir: Path | None = None


def _resolved_step(step: StepConfig, context: VariableContext) -> StepConfig:
    data = context.resolve(step.model_dump(by_alias=True, exclude_unset=True))
    return StepConfig.model_validate(data)


def _redact_failures(
    failures: list[AssertionIssue],
    secret_names: set[str],
) -> list[AssertionIssue]:
    redacted: list[AssertionIssue] = []
    for issue in failures:
        path_parts = issue.path.replace("[", ".").split(".")
        secret_path = any(is_secret_name(part.rstrip("]"), secret_names) for part in path_parts)
        redacted.append(
            issue.model_copy(
                update={
                    "expected": MASK if secret_path else redact(issue.expected, secret_names),
                    "actual": MASK if secret_path else redact(issue.actual, secret_names),
                }
            )
        )
    return redacted


def _skipped(
    step: StepConfig,
    phase: Phase,
    reason: str,
    secret_names: set[str],
) -> StepResult:
    return StepResult(
        name=step.name,
        phase=phase,
        status="skipped",
        method=step.request.method,
        url=sanitize_url(step.request.url, secret_names),
        duration_ms=0,
        error=reason,
    )


async def _run_step(
    step: StepConfig,
    phase: Phase,
    context: VariableContext,
    executor: HttpExecutor,
    base_dir: Path,
    secret_names: set[str],
) -> StepResult:
    started = perf_counter()
    attempts: list[AttemptResult] = []
    request: RequestConfig = step.request
    try:
        resolved = _resolved_step(step, context)
        request = resolved.request
        assertions = resolved.assertions
        response, attempts = await executor.execute(request, resolved.retry)
        failures = evaluate_assertions(response, assertions, base_dir)
        capture = capture_response(response, secret_names)
        if failures:
            return StepResult(
                name=step.name,
                phase=phase,
                status="failed",
                method=request.method,
                url=sanitize_url(request.url, secret_names),
                duration_ms=(perf_counter() - started) * 1000,
                status_code=response.status_code,
                attempts=attempts,
                failures=_redact_failures(failures, secret_names),
                response=capture,
            )
        extracted = apply_extractions(response, resolved.extract)
        for name, value in extracted.items():
            context.set(name, value)
        return StepResult(
            name=step.name,
            phase=phase,
            status="passed",
            method=request.method,
            url=sanitize_url(request.url, secret_names),
            duration_ms=(perf_counter() - started) * 1000,
            status_code=response.status_code,
            attempts=attempts,
            extracted=redact(extracted, secret_names),
            response=capture,
        )
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
        return StepResult(
            name=step.name,
            phase=phase,
            status="error",
            method=request.method,
            url=sanitize_url(request.url, secret_names),
            duration_ms=(perf_counter() - started) * 1000,
            attempts=attempts,
            error=f"resolved step configuration is invalid: {details}",
            error_kind="configuration",
        )
    except (ConfigurationError, VariableResolutionError, ExtractionError) as exc:
        return StepResult(
            name=step.name,
            phase=phase,
            status="error",
            method=request.method,
            url=sanitize_url(request.url, secret_names),
            duration_ms=(perf_counter() - started) * 1000,
            attempts=attempts,
            error=str(exc),
            error_kind="configuration",
        )
    except RequestExecutionError as exc:
        return StepResult(
            name=step.name,
            phase=phase,
            status="error",
            method=request.method,
            url=sanitize_url(request.url, secret_names),
            duration_ms=(perf_counter() - started) * 1000,
            attempts=attempts,
            error=str(exc),
            error_kind="infrastructure",
        )
    except AssertFlowError as exc:
        return StepResult(
            name=step.name,
            phase=phase,
            status="error",
            method=request.method,
            url=sanitize_url(request.url, secret_names),
            duration_ms=(perf_counter() - started) * 1000,
            attempts=attempts,
            error=str(exc),
            error_kind="infrastructure",
        )


async def run_suite(
    suite: SuiteConfig,
    source: Path,
    environment: dict[str, Any] | None = None,
    options: RunOptions | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SuiteResult:
    options = options or RunOptions()
    started = perf_counter()
    secret_names = set(suite.secrets)
    context = VariableContext.from_sources(environment, suite.variables)
    mock_service = (
        MockService(suite.mocks, secret_names) if suite.mocks and transport is None else None
    )
    active_transport = mock_service.transport() if mock_service else transport
    executor = HttpExecutor(active_transport, secret_names)
    results: list[StepResult] = []
    cleanup_failures: list[str] = []
    mock_failures: list[str] = []
    setup_ok = True
    workflow_ok = True

    try:
        for step in suite.setup:
            if not setup_ok:
                results.append(_skipped(step, "setup", "skipped after setup failure", secret_names))
                continue
            result = await _run_step(step, "setup", context, executor, source.parent, secret_names)
            results.append(result)
            setup_ok = result.status == "passed"

        for step in suite.steps:
            can_run = setup_ok and (workflow_ok or options.continue_on_fail)
            if not can_run:
                reason = "skipped after setup failure" if not setup_ok else "skipped after failure"
                results.append(_skipped(step, "steps", reason, secret_names))
                continue
            result = await _run_step(step, "steps", context, executor, source.parent, secret_names)
            results.append(result)
            if result.status != "passed":
                workflow_ok = False

        for step in suite.teardown:
            result = await _run_step(
                step, "teardown", context, executor, source.parent, secret_names
            )
            results.append(result)
            if result.status != "passed":
                cleanup_failures.append(f"{step.name}: {result.error or 'assertion failure'}")
    finally:
        await executor.close()

    if mock_service:
        mock_failures = mock_service.verify()
    failed = any(result.status in {"failed", "error"} for result in results)
    suite_result = SuiteResult(
        name=suite.name,
        source=str(source),
        status="failed" if failed or mock_failures else "passed",
        duration_ms=(perf_counter() - started) * 1000,
        steps=results,
        cleanup_failures=cleanup_failures,
        mock_failures=mock_failures,
    )
    snapshot_dir = options.snapshot_dir or Path(".assertflow/snapshots")
    if options.snapshot:
        save_snapshot(suite_result, source, snapshot_dir, suite.regression.ignore)
    if options.regression:
        differences = compare_snapshot(
            suite_result,
            source,
            snapshot_dir,
            suite.regression.ignore,
        )
        if differences:
            suite_result.regression_failures = differences
            suite_result.status = "failed"
    return suite_result


async def run_many(
    suites: list[tuple[SuiteConfig, Path, dict[str, Any]]],
    options: RunOptions,
) -> RunResult:
    if options.workers < 1 or options.repeat < 1:
        raise ValueError("workers and repeat must be at least 1")
    started = perf_counter()
    semaphore = asyncio.Semaphore(options.workers)

    async def bounded(item: tuple[SuiteConfig, Path, dict[str, Any]]) -> SuiteResult:
        suite, source, environment = item
        async with semaphore:
            return await run_suite(suite, source, environment, options)

    work = [item for item in suites for _ in range(options.repeat)]
    results = await asyncio.gather(*(bounded(item) for item in work))
    failed = any(result.status != "passed" for result in results)
    return RunResult(
        status="failed" if failed else "passed",
        duration_ms=(perf_counter() - started) * 1000,
        suites=results,
    )
