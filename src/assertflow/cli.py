"""AssertFlow command-line interface."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from assertflow import __version__
from assertflow.benchmark import benchmark_suite
from assertflow.errors import AssertFlowError, ConfigurationError
from assertflow.models import RunResult, SuiteConfig
from assertflow.parser import (
    discover_suites,
    find_environment_file,
    load_environment,
    load_suite,
)
from assertflow.reporting import (
    exit_code,
    json_report,
    junit_report,
    render_benchmark,
    render_terminal,
    write_report,
)
from assertflow.runner import RunOptions, run_many

app = typer.Typer(
    name="assertflow",
    help="Declarative API integration and regression testing.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


class ReportFormat(StrEnum):
    terminal = "terminal"
    json = "json"
    junit = "junit"


SuiteTarget = Annotated[Path, typer.Argument(exists=True, readable=True)]
EnvName = Annotated[str | None, typer.Option("--env", help="Environment name to select.")]
EnvFile = Annotated[
    Path | None,
    typer.Option("--env-file", exists=True, readable=True, help="YAML environment mapping."),
]


def _version(value: bool) -> None:
    if value:
        console.print(f"AssertFlow {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True),
) -> None:
    """Run and validate AssertFlow API workflow suites."""


def _load_targets(
    target: Path,
    env_name: str | None,
    env_file: Path | None,
) -> list[tuple[SuiteConfig, Path, dict[str, Any]]]:
    loaded: list[tuple[SuiteConfig, Path, dict[str, Any]]] = []
    for path in discover_suites(target):
        suite = load_suite(path)
        selected = env_name or suite.environment
        selected_file = env_file or (find_environment_file(path) if selected else None)
        environment = load_environment(selected_file, selected)
        loaded.append((suite, path, environment))
    return loaded


def _emit(result: RunResult, report: ReportFormat, output: Path | None, verbose: int) -> None:
    if report is ReportFormat.terminal:
        if output is not None:
            raise ConfigurationError("--output is supported with --report json or junit")
        render_terminal(result, console, verbose)
        return
    content = json_report(result) if report is ReportFormat.json else junit_report(result)
    if output is None:
        console.print(content, markup=False, highlight=False)
    else:
        write_report(content, output)
        console.print(f"Report written to {output}")


@app.command()
def validate(target: SuiteTarget) -> None:
    """Validate one suite or every YAML suite below a directory."""
    try:
        paths = discover_suites(target)
        for path in paths:
            suite = load_suite(path)
            console.print(f"[green]VALID[/green] {suite.name} ({path})")
        console.print(f"\n{len(paths)} suite(s) valid")
    except AssertFlowError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc


@app.command()
def run(
    target: SuiteTarget,
    env: EnvName = None,
    env_file: EnvFile = None,
    workers: Annotated[int, typer.Option(min=1, max=256)] = 1,
    repeat: Annotated[int, typer.Option(min=1, max=100_000)] = 1,
    report: Annotated[ReportFormat, typer.Option()] = ReportFormat.terminal,
    output: Annotated[Path | None, typer.Option()] = None,
    regression: Annotated[bool, typer.Option(help="Compare stored response snapshots.")] = False,
    snapshot_dir: Annotated[Path, typer.Option(help="Snapshot storage directory.")] = Path(
        ".assertflow/snapshots"
    ),
    continue_on_fail: Annotated[
        bool,
        typer.Option("--continue-on-fail", help="Run later steps after a failed step."),
    ] = False,
    verbose: Annotated[int, typer.Option("-v", "--verbose", count=True)] = 0,
) -> None:
    """Run one suite or a directory of suites."""
    try:
        suites = _load_targets(target, env, env_file)
        options = RunOptions(
            workers=workers,
            repeat=repeat,
            continue_on_fail=continue_on_fail,
            regression=regression,
            snapshot_dir=snapshot_dir,
        )
        result = asyncio.run(run_many(suites, options))
        _emit(result, report, output, verbose)
        raise typer.Exit(code=exit_code(result))
    except typer.Exit:
        raise
    except AssertFlowError as exc:
        console.print(f"[red]AssertFlow error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        console.print(
            f"[red]Infrastructure error:[/red] {type(exc).__name__}: {exc}",
            highlight=False,
        )
        raise typer.Exit(code=3) from exc


@app.command()
def snapshot(
    target: SuiteTarget,
    env: EnvName = None,
    env_file: EnvFile = None,
    workers: Annotated[int, typer.Option(min=1, max=256)] = 1,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for readable baseline files."),
    ] = Path(".assertflow/snapshots"),
) -> None:
    """Execute suites and store regression baselines."""
    try:
        suites = _load_targets(target, env, env_file)
        result = asyncio.run(
            run_many(
                suites,
                RunOptions(workers=workers, snapshot=True, snapshot_dir=output_dir),
            )
        )
        render_terminal(result, console)
        if result.status == "passed":
            console.print(f"Snapshots written under {output_dir}")
        raise typer.Exit(code=exit_code(result))
    except typer.Exit:
        raise
    except AssertFlowError as exc:
        console.print(f"[red]Snapshot error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        console.print(
            f"[red]Infrastructure error:[/red] {type(exc).__name__}: {exc}",
            highlight=False,
        )
        raise typer.Exit(code=3) from exc


@app.command()
def benchmark(
    target: SuiteTarget,
    env: EnvName = None,
    env_file: EnvFile = None,
    requests: Annotated[int, typer.Option(min=1, max=1_000_000)] = 100,
    concurrency: Annotated[int, typer.Option(min=1, max=10_000)] = 10,
) -> None:
    """Benchmark one request suite without claiming full load-test semantics."""
    try:
        suites = _load_targets(target, env, env_file)
        if len(suites) != 1:
            raise ConfigurationError("benchmark target must resolve to exactly one suite")
        suite, source, environment = suites[0]
        result = asyncio.run(benchmark_suite(suite, source, environment, requests, concurrency))
        render_benchmark(result, console)
        raise typer.Exit(code=result.exit_code)
    except typer.Exit:
        raise
    except AssertFlowError as exc:
        console.print(f"[red]Benchmark error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        console.print(
            f"[red]Infrastructure error:[/red] {type(exc).__name__}: {exc}",
            highlight=False,
        )
        raise typer.Exit(code=3) from exc
