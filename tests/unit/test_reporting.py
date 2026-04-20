from xml.etree import ElementTree as ET

from assertflow.models import RunResult, StepResult, SuiteResult
from assertflow.reporting import exit_code, json_report, junit_report


def make_result(status: str = "passed", error_kind: str | None = None) -> RunResult:
    step_status = "error" if error_kind else status
    return RunResult.model_validate(
        {
            "status": status,
            "duration_ms": 5,
            "suites": [
                SuiteResult(
                    name="suite",
                    source="suite.yaml",
                    status=status,
                    duration_ms=5,
                    steps=[
                        StepResult(
                            name="step",
                            phase="steps",
                            status=step_status,
                            method="GET",
                            url="http://mock/step",
                            duration_ms=5,
                            error="boom" if error_kind else None,
                            error_kind=error_kind,
                        )
                    ],
                )
            ],
        }
    )


def test_json_and_junit_are_machine_readable() -> None:
    result = make_result()

    assert '"name": "suite"' in json_report(result)
    root = ET.fromstring(junit_report(result))
    assert root.attrib["tests"] == "1"
    assert root.find(".//testcase").attrib["name"] == "step"


def test_exit_codes_distinguish_failures_config_and_infrastructure() -> None:
    assert exit_code(make_result()) == 0
    assert exit_code(make_result("failed")) == 1
    assert exit_code(make_result("failed", "configuration")) == 2
    assert exit_code(make_result("failed", "infrastructure")) == 3
