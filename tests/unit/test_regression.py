from pathlib import Path

import pytest

from assertflow.errors import RegressionFailure
from assertflow.models import StepResult, SuiteResult
from assertflow.regression import compare_snapshot, normalize, save_snapshot


def suite_result(body: object) -> SuiteResult:
    return SuiteResult(
        name="users",
        source="users.yaml",
        status="passed",
        duration_ms=1,
        steps=[
            StepResult(
                name="get user",
                phase="steps",
                status="passed",
                method="GET",
                url="http://mock/user",
                duration_ms=1,
                status_code=200,
                response={"status": 200, "headers": {"date": "now"}, "body": body},
            )
        ],
    )


def test_snapshot_round_trip_and_ignored_dynamic_field(tmp_path: Path) -> None:
    source = Path("users.yaml")
    baseline = suite_result({"id": 1, "timestamp": "first", "active": True})
    save_snapshot(baseline, source, tmp_path, ["body.timestamp", "headers.date"])
    current = suite_result({"id": 1, "timestamp": "second", "active": True})

    differences = compare_snapshot(
        current,
        source,
        tmp_path,
        ["body.timestamp", "headers.date"],
    )

    assert differences == []


def test_diff_detects_added_removed_changed_and_type(tmp_path: Path) -> None:
    source = Path("users.yaml")
    save_snapshot(
        suite_result({"removed": 1, "changed": 1, "typed": 1}),
        source,
        tmp_path,
        [],
    )
    differences = compare_snapshot(
        suite_result({"added": 2, "changed": 2, "typed": "1"}),
        source,
        tmp_path,
        [],
    )

    assert {(item.path, item.kind) for item in differences} >= {
        ("steps:get user.body.added", "added"),
        ("steps:get user.body.removed", "removed"),
        ("steps:get user.body.changed", "changed"),
        ("steps:get user.body.typed", "type_changed"),
    }


def test_unknown_ignore_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RegressionFailure, match="did not match"):
        save_snapshot(suite_result({"id": 1}), Path("users.yaml"), tmp_path, ["body.nope"])


def test_normalize_supports_array_indexes_without_mutating_input() -> None:
    original = {"body": {"items": [{"timestamp": "dynamic", "id": 1}]}}

    normalized, matched = normalize(original, ["body.items[0].timestamp"])

    assert normalized == {"body": {"items": [{"id": 1}]}}
    assert original["body"]["items"][0]["timestamp"] == "dynamic"
    assert matched == {"body.items[0].timestamp"}
