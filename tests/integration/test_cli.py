from pathlib import Path

from typer.testing import CliRunner

from assertflow.cli import app

runner = CliRunner()


def write_suite(path: Path, expected_status: int = 200) -> None:
    path.write_text(
        f"""
name: CLI smoke
mocks:
  - request:
      method: GET
      path: /health
    response:
      status: 200
      json:
        status: ready
steps:
  - name: health
    request:
      method: GET
      url: http://mock/health
    assert:
      status: {expected_status}
      json:
        status: ready
""",
        encoding="utf-8",
    )


def test_run_and_json_report(tmp_path: Path) -> None:
    suite = tmp_path / "suite.yaml"
    report = tmp_path / "reports" / "result.json"
    write_suite(suite)

    result = runner.invoke(app, ["run", str(suite), "--report", "json", "--output", str(report)])

    assert result.exit_code == 0, result.output
    assert report.exists()
    assert '"status": "passed"' in report.read_text(encoding="utf-8")


def test_assertion_failure_returns_one(tmp_path: Path) -> None:
    suite = tmp_path / "suite.yaml"
    write_suite(suite, expected_status=201)

    result = runner.invoke(app, ["run", str(suite)])

    assert result.exit_code == 1
    assert "Expected" in result.output


def test_invalid_suite_returns_two(tmp_path: Path) -> None:
    suite = tmp_path / "bad.yaml"
    suite.write_text("name: bad\nsteps: []\n", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(suite)])

    assert result.exit_code == 2
    assert "Configuration error" in result.output


def test_environment_file_is_discovered_and_snapshot_round_trip(tmp_path: Path) -> None:
    suite = tmp_path / "suite.yaml"
    snapshots = tmp_path / "snapshots"
    (tmp_path / "environments.yaml").write_text(
        "local:\n  BASE_URL: http://mock\n", encoding="utf-8"
    )
    suite.write_text(
        """
name: environment snapshot
environment: local
mocks:
  - request: {method: GET, path: /health}
    response: {status: 200, json: {state: ready}}
steps:
  - name: health
    request: {method: GET, url: "${BASE_URL}/health"}
    assert: {status: 200}
""",
        encoding="utf-8",
    )

    created = runner.invoke(
        app,
        ["snapshot", str(suite), "--output-dir", str(snapshots)],
    )
    compared = runner.invoke(
        app,
        ["run", str(suite), "--regression", "--snapshot-dir", str(snapshots)],
    )

    assert created.exit_code == 0, created.output
    assert compared.exit_code == 0, compared.output
    assert (snapshots / "suite.snapshot.json").exists()


def test_verbose_levels_show_masked_extraction_and_response(tmp_path: Path) -> None:
    suite = tmp_path / "verbose.yaml"
    suite.write_text(
        """
name: verbose
mocks:
  - request: {method: GET, path: /token}
    response: {status: 200, json: {access_token: secret-token, state: ready}}
steps:
  - name: token
    request: {method: GET, url: http://mock/token}
    extract: {access_token: body.access_token}
""",
        encoding="utf-8",
    )

    one = runner.invoke(app, ["run", str(suite), "-v"])
    two = runner.invoke(app, ["run", str(suite), "-vv"])

    assert one.exit_code == 0
    assert "Extracted" in one.output
    assert "secret-token" not in one.output
    assert "Response:" not in one.output
    assert two.exit_code == 0
    assert "Response:" in two.output
    assert "secret-token" not in two.output


def test_benchmark_command_reports_real_measurements(tmp_path: Path) -> None:
    suite = tmp_path / "benchmark.yaml"
    write_suite(suite)

    result = runner.invoke(
        app,
        ["benchmark", str(suite), "--requests", "3", "--concurrency", "2"],
    )

    assert result.exit_code == 0, result.output
    assert "Requests:    3" in result.output
    assert "Throughput:" in result.output
    assert "p99:" in result.output
