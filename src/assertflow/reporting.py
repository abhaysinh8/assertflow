"""Terminal, JSON, and JUnit reporting over the shared result model."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

from rich.console import Console
from rich.table import Table

from assertflow.errors import ReportingError
from assertflow.models import BenchmarkResult, RunResult


def exit_code(result: RunResult) -> int:
    kinds = {
        step.error_kind
        for suite in result.suites
        for step in suite.steps
        if step.error_kind is not None
    }
    if "configuration" in kinds:
        return 2
    if "infrastructure" in kinds:
        return 3
    return 0 if result.status == "passed" else 1


def render_terminal(result: RunResult, console: Console | None = None, verbose: int = 0) -> None:
    console = console or Console()
    console.print("\n[bold]AssertFlow[/bold]")
    for suite in result.suites:
        table = Table(title=f"{suite.name} - {suite.status.upper()}", show_header=True)
        table.add_column("Status", width=7)
        table.add_column("Phase")
        table.add_column("Step")
        table.add_column("HTTP")
        table.add_column("Time", justify="right")
        colors = {"passed": "green", "failed": "red", "error": "magenta", "skipped": "yellow"}
        for step in suite.steps:
            table.add_row(
                f"[{colors[step.status]}]{step.status.upper()}[/]",
                step.phase,
                step.name,
                f"{step.method} {step.status_code or '-'}",
                f"{step.duration_ms:.1f} ms",
            )
        console.print(table)
        for step in suite.steps:
            if step.error:
                console.print(f"[red]{step.name}:[/red] {step.error}", highlight=False)
            for failure in step.failures:
                console.print(
                    f"[red]{step.name}[/red] {failure.path} {failure.assertion}\n"
                    f"  Expected: {failure.expected!r}\n  Actual: {failure.actual!r}",
                    highlight=False,
                )
            if verbose:
                console.print(f"  URL: {step.url}", highlight=False)
                if step.extracted:
                    console.print(f"  Extracted: {step.extracted!r}", highlight=False)
            if verbose and len(step.attempts) > 1:
                rendered = ", ".join(
                    f"attempt {attempt.attempt}={attempt.status_code or attempt.error}"
                    for attempt in step.attempts
                )
                console.print(f"  Retries: {rendered}", highlight=False)
            if verbose >= 2 and step.response is not None:
                console.print("  Response:", highlight=False)
                console.print(json.dumps(step.response, indent=2, sort_keys=True), markup=False)
        for message in suite.cleanup_failures:
            console.print(f"[red]Cleanup failure:[/red] {message}", highlight=False)
        for message in suite.mock_failures:
            console.print(f"[red]Mock verification:[/red] {message}", highlight=False)
        for difference in suite.regression_failures:
            marker = {"added": "+", "removed": "-", "changed": "~", "type_changed": "~"}[
                difference.kind
            ]
            console.print(
                f"[red]{marker} {difference.path}[/red] "
                f"{difference.expected!r} -> {difference.actual!r}",
                highlight=False,
            )
    passed = sum(suite.status == "passed" for suite in result.suites)
    console.print(
        f"\n{passed} passed, {len(result.suites) - passed} failed in {result.duration_ms:.1f} ms"
    )


def render_benchmark(result: BenchmarkResult, console: Console | None = None) -> None:
    console = console or Console()
    console.print("\n[bold]AssertFlow benchmark[/bold]")
    console.print(f"Requests:    {result.requests}")
    console.print(f"Passed:      {result.passed}")
    console.print(f"Failed:      {result.failed}")
    console.print(f"Concurrency: {result.concurrency}")
    console.print(f"Throughput:  {result.throughput:.2f} requests/s")
    console.print("\nLatency")
    console.print(f"mean: {result.mean_ms:.2f} ms")
    console.print(f"p50:  {result.p50_ms:.2f} ms")
    console.print(f"p95:  {result.p95_ms:.2f} ms")
    console.print(f"p99:  {result.p99_ms:.2f} ms")


def json_report(result: RunResult) -> str:
    return result.model_dump_json(indent=2, exclude_none=True)


def junit_report(result: RunResult) -> str:
    suite_level_cases = sum(
        bool(suite.regression_failures) + bool(suite.mock_failures) for suite in result.suites
    )
    tests = sum(len(suite.steps) for suite in result.suites) + suite_level_cases
    failures = (
        sum(step.status == "failed" for suite in result.suites for step in suite.steps)
        + suite_level_cases
    )
    errors = sum(step.status == "error" for suite in result.suites for step in suite.steps)
    root = ET.Element(
        "testsuites",
        tests=str(tests),
        failures=str(failures),
        errors=str(errors),
        time=f"{result.duration_ms / 1000:.6f}",
    )
    for suite in result.suites:
        extra_cases = int(bool(suite.regression_failures)) + int(bool(suite.mock_failures))
        suite_node = ET.SubElement(
            root,
            "testsuite",
            name=suite.name,
            tests=str(len(suite.steps) + extra_cases),
            failures=str(sum(step.status == "failed" for step in suite.steps) + extra_cases),
            errors=str(sum(step.status == "error" for step in suite.steps)),
            skipped=str(sum(step.status == "skipped" for step in suite.steps)),
            time=f"{suite.duration_ms / 1000:.6f}",
        )
        for step in suite.steps:
            case = ET.SubElement(
                suite_node,
                "testcase",
                name=step.name,
                classname=f"{suite.name}.{step.phase}",
                time=f"{step.duration_ms / 1000:.6f}",
            )
            if step.status == "failed":
                node = ET.SubElement(
                    case,
                    "failure",
                    message="; ".join(i.message for i in step.failures),
                )
                node.text = "\n".join(
                    f"{issue.path}: expected {issue.expected!r}, actual {issue.actual!r}"
                    for issue in step.failures
                )
            elif step.status == "error":
                node = ET.SubElement(case, "error", message=step.error or "execution error")
                node.text = step.error
            elif step.status == "skipped":
                ET.SubElement(case, "skipped", message=step.error or "skipped")
        if suite.regression_failures:
            case = ET.SubElement(
                suite_node,
                "testcase",
                name="regression comparison",
                classname=f"{suite.name}.regression",
            )
            node = ET.SubElement(case, "failure", message="regression differences detected")
            node.text = "\n".join(
                f"{item.kind}: {item.path} ({item.expected!r} -> {item.actual!r})"
                for item in suite.regression_failures
            )
        if suite.mock_failures:
            case = ET.SubElement(
                suite_node,
                "testcase",
                name="mock verification",
                classname=f"{suite.name}.mocks",
            )
            node = ET.SubElement(case, "failure", message="mock expectations were not met")
            node.text = "\n".join(suite.mock_failures)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def write_report(content: str, output: Path) -> None:
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content + ("" if content.endswith("\n") else "\n"), encoding="utf-8")
    except OSError as exc:
        raise ReportingError(f"cannot write report {output}: {exc}") from exc
