from pathlib import Path

import pytest

from assertflow.errors import SuiteValidationError
from assertflow.parser import discover_suites, load_suite


def test_loads_minimal_suite(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
steps:
  - name: health
    request:
      method: get
      url: https://example.test/health
    assert:
      status: 200
""",
        encoding="utf-8",
    )

    suite = load_suite(suite_path)

    assert suite.name == "smoke"
    assert suite.steps[0].request.method == "GET"
    assert suite.steps[0].assertions.status == 200


def test_rejects_unknown_configuration(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        "name: bad\nsteps: []\nunexpected: true\n",
        encoding="utf-8",
    )

    with pytest.raises(SuiteValidationError, match="unexpected"):
        load_suite(suite_path)


def test_discovers_yaml_recursively_and_deterministically(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.yml").write_text("name: b", encoding="utf-8")
    (tmp_path / "a.yaml").write_text("name: a", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / "environments.yaml").write_text(
        "local:\n  BASE_URL: http://mock\n", encoding="utf-8"
    )

    paths = discover_suites(tmp_path)

    assert [path.name for path in paths] == ["a.yaml", "b.yml"]


def test_environment_file_values_are_selected(tmp_path: Path) -> None:
    from assertflow.parser import find_environment_file, load_environment

    suite_dir = tmp_path / "nested"
    suite_dir.mkdir()
    suite = suite_dir / "suite.yaml"
    suite.write_text("name: suite\nsteps: []\n", encoding="utf-8")
    environments = tmp_path / "environments.yaml"
    environments.write_text("local:\n  BASE_URL: http://mock\n", encoding="utf-8")

    found = find_environment_file(suite)

    assert found == environments
    assert load_environment(found, "local") == {"BASE_URL": "http://mock"}
