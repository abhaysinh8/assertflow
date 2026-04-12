"""Readable, normalized response snapshots and structural diffs."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from assertflow.errors import RegressionFailure
from assertflow.extraction import get_path
from assertflow.models import RegressionDifference, SuiteResult

_ARRAY_INDEX = re.compile(r"\[(\d+)\]")


def snapshot_path(source: Path, directory: Path) -> Path:
    return directory / f"{source.stem}.snapshot.json"


def _delete_path(document: Any, path: str) -> bool:
    normalized = _ARRAY_INDEX.sub(r".\1", path).strip(".")
    segments = normalized.split(".") if normalized else []
    if not segments:
        return False
    parent_path = ".".join(segments[:-1])
    parent = get_path(document, parent_path) if parent_path else document
    leaf = segments[-1]
    if isinstance(parent, dict) and leaf in parent:
        del parent[leaf]
        return True
    if isinstance(parent, list) and leaf.isdigit() and int(leaf) < len(parent):
        del parent[int(leaf)]
        return True
    return False


def normalize(document: dict[str, Any], ignore: list[str]) -> tuple[dict[str, Any], set[str]]:
    normalized = copy.deepcopy(document)
    matched = {path for path in ignore if _delete_path(normalized, path)}
    return normalized, matched


def _responses(result: SuiteResult, ignore: list[str]) -> tuple[dict[str, Any], set[str]]:
    responses: dict[str, Any] = {}
    matched: set[str] = set()
    for step in result.steps:
        if step.response is None:
            continue
        normalized, step_matches = normalize(step.response, ignore)
        responses[f"{step.phase}:{step.name}"] = normalized
        matched.update(step_matches)
    return responses, matched


def save_snapshot(
    result: SuiteResult,
    source: Path,
    directory: Path,
    ignore: list[str],
) -> Path:
    if result.status != "passed":
        raise RegressionFailure(f"cannot snapshot failed suite: {result.name}")
    responses, matched = _responses(result, ignore)
    unmatched = sorted(set(ignore) - matched)
    if unmatched:
        raise RegressionFailure(
            "regression ignore path(s) did not match any response: " + ", ".join(unmatched)
        )
    target = snapshot_path(source, directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "suite": result.name,
        "source": source.name,
        "ignore": ignore,
        "responses": responses,
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _diff(expected: Any, actual: Any, path: str = "") -> list[RegressionDifference]:
    if type(expected) is not type(actual):
        return [
            RegressionDifference(
                path=path or "$",
                kind="type_changed",
                expected=expected,
                actual=actual,
            )
        ]
    if isinstance(expected, dict):
        differences: list[RegressionDifference] = []
        expected_keys = set(expected)
        actual_keys = set(actual)
        for key in sorted(expected_keys - actual_keys):
            child = f"{path}.{key}" if path else key
            differences.append(
                RegressionDifference(path=child, kind="removed", expected=expected[key])
            )
        for key in sorted(actual_keys - expected_keys):
            child = f"{path}.{key}" if path else key
            differences.append(RegressionDifference(path=child, kind="added", actual=actual[key]))
        for key in sorted(expected_keys & actual_keys):
            child = f"{path}.{key}" if path else key
            differences.extend(_diff(expected[key], actual[key], child))
        return differences
    if isinstance(expected, list):
        differences = []
        common = min(len(expected), len(actual))
        for index in range(common):
            differences.extend(_diff(expected[index], actual[index], f"{path}[{index}]"))
        for index in range(common, len(expected)):
            differences.append(
                RegressionDifference(
                    path=f"{path}[{index}]",
                    kind="removed",
                    expected=expected[index],
                )
            )
        for index in range(common, len(actual)):
            differences.append(
                RegressionDifference(
                    path=f"{path}[{index}]",
                    kind="added",
                    actual=actual[index],
                )
            )
        return differences
    if expected != actual:
        return [
            RegressionDifference(path=path or "$", kind="changed", expected=expected, actual=actual)
        ]
    return []


def compare_snapshot(
    result: SuiteResult,
    source: Path,
    directory: Path,
    ignore: list[str],
) -> list[RegressionDifference]:
    target = snapshot_path(source, directory)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegressionFailure(f"regression snapshot not found: {target}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RegressionFailure(f"cannot read regression snapshot {target}: {exc}") from exc
    if payload.get("version") != 1 or not isinstance(payload.get("responses"), dict):
        raise RegressionFailure(f"unsupported or invalid regression snapshot: {target}")
    current, matched = _responses(result, ignore)
    unmatched = sorted(set(ignore) - matched)
    if unmatched:
        raise RegressionFailure(
            "regression ignore path(s) did not match current responses: " + ", ".join(unmatched)
        )
    expected = payload["responses"]
    if not isinstance(expected, dict):
        raise RegressionFailure(f"invalid responses mapping in snapshot: {target}")
    return _diff(expected, current)
