"""Validated suite configuration and stable execution result models."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
Phase = Literal["setup", "steps", "teardown"]
StepStatus = Literal["passed", "failed", "error", "skipped"]
ErrorKind = Literal["configuration", "infrastructure"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RetryConfig(StrictModel):
    attempts: int = Field(default=1, ge=1, le=20)
    delay_ms: int = Field(default=0, ge=0, le=60_000)
    on_status: list[int] = Field(default_factory=list)

    @field_validator("on_status")
    @classmethod
    def valid_statuses(cls, value: list[int]) -> list[int]:
        invalid = [status for status in value if not 100 <= status <= 599]
        if invalid:
            raise ValueError(f"retry status codes must be between 100 and 599: {invalid}")
        return list(dict.fromkeys(value))


class RequestConfig(StrictModel):
    method: HttpMethod
    url: str = Field(min_length=1)
    headers: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    json_body: JsonValue | None = Field(default=None, alias="json")
    form: dict[str, Any] | None = None
    body: str | None = None
    timeout: float = Field(default=10.0, gt=0, le=300)
    follow_redirects: bool = False
    verify_tls: bool = True

    @model_validator(mode="after")
    def one_body(self) -> RequestConfig:
        bodies = (self.json_body is not None, self.form is not None, self.body is not None)
        if sum(bodies) > 1:
            raise ValueError("request may define only one of json, form, or body")
        return self

    @field_validator("method", mode="before")
    @classmethod
    def uppercase_method(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("url")
    @classmethod
    def absolute_http_url_or_template(cls, value: str) -> str:
        if "${" in value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("request URL must be an absolute http:// or https:// URL")
        return value


JsonTypeName = Literal["null", "boolean", "integer", "number", "string", "array", "object"]


class AssertionRule(StrictModel):
    equals: Any | None = None
    not_equals: Any | None = None
    exists: bool | None = None
    type_name: JsonTypeName | None = Field(default=None, alias="type")
    greater_than: float | None = None
    greater_than_or_equal: float | None = None
    less_than: float | None = None
    less_than_or_equal: float | None = None
    contains: Any | None = None
    starts_with: str | None = None
    ends_with: str | None = None
    regex: str | None = None
    length: int | None = Field(default=None, ge=0)
    empty: bool | None = None
    not_empty: bool | None = None

    @model_validator(mode="after")
    def has_operator(self) -> AssertionRule:
        if not self.model_fields_set:
            raise ValueError("assertion rule must define at least one operator")
        return self


def _coerce_rules(value: object) -> object:
    if not isinstance(value, dict):
        return value
    converted: dict[str, object] = {}
    for path, rule in value.items():
        converted[path] = rule if isinstance(rule, dict) else {"equals": rule}
    return converted


class AssertionsConfig(StrictModel):
    status: int | list[int] | None = None
    headers: dict[str, AssertionRule] = Field(default_factory=dict)
    json_values: dict[str, AssertionRule] = Field(default_factory=dict, alias="json")
    schema_path: str | None = Field(default=None, alias="schema")

    @field_validator("headers", "json_values", mode="before")
    @classmethod
    def scalar_rule_means_equals(cls, value: object) -> object:
        return _coerce_rules(value)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: int | list[int] | None) -> int | list[int] | None:
        statuses = [value] if isinstance(value, int) else value or []
        invalid = [status for status in statuses if not 100 <= status <= 599]
        if invalid:
            raise ValueError(f"asserted status codes must be between 100 and 599: {invalid}")
        return value


class StepConfig(StrictModel):
    name: str = Field(min_length=1)
    request: RequestConfig
    assertions: AssertionsConfig = Field(default_factory=AssertionsConfig, alias="assert")
    extract: dict[str, str] = Field(default_factory=dict)
    retry: RetryConfig = Field(default_factory=RetryConfig)


class RegressionConfig(StrictModel):
    ignore: list[str] = Field(default_factory=list)


class MockRequestConfig(StrictModel):
    method: HttpMethod
    path: str = Field(pattern=r"^/")
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    json_body: JsonValue | None = Field(default=None, alias="json")
    body: str | None = None

    @field_validator("method", mode="before")
    @classmethod
    def uppercase_method(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def one_body(self) -> MockRequestConfig:
        if self.json_body is not None and self.body is not None:
            raise ValueError("mock request may define json or body, not both")
        return self


class MockResponseConfig(StrictModel):
    status: int = Field(default=200, ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: JsonValue | None = Field(default=None, alias="json")
    body: str | None = None
    delay_ms: int = Field(default=0, ge=0, le=60_000)

    @model_validator(mode="after")
    def one_body(self) -> MockResponseConfig:
        if self.json_body is not None and self.body is not None:
            raise ValueError("mock response may define json or body, not both")
        return self


class MockRouteConfig(StrictModel):
    name: str | None = None
    request: MockRequestConfig
    response: MockResponseConfig | None = None
    responses: list[MockResponseConfig] = Field(default_factory=list)
    expect_calls: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def response_source(self) -> MockRouteConfig:
        if (self.response is None) == (not self.responses):
            raise ValueError("mock route must define exactly one of response or responses")
        return self


class SuiteConfig(StrictModel):
    name: str = Field(min_length=1)
    environment: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    secrets: list[str] = Field(default_factory=list)
    mocks: list[MockRouteConfig] = Field(default_factory=list)
    setup: list[StepConfig] = Field(default_factory=list)
    steps: list[StepConfig] = Field(min_length=1)
    teardown: list[StepConfig] = Field(default_factory=list)
    regression: RegressionConfig = Field(default_factory=RegressionConfig)

    @model_validator(mode="after")
    def unique_step_names(self) -> SuiteConfig:
        names = [step.name for step in [*self.setup, *self.steps, *self.teardown]]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"step names must be unique: {', '.join(duplicates)}")
        return self


class AttemptResult(StrictModel):
    attempt: int
    status_code: int | None = None
    duration_ms: float
    error: str | None = None


class AssertionIssue(StrictModel):
    path: str
    assertion: str
    expected: Any
    actual: Any
    message: str


class StepResult(StrictModel):
    name: str
    phase: Phase
    status: StepStatus
    method: str
    url: str
    duration_ms: float
    status_code: int | None = None
    attempts: list[AttemptResult] = Field(default_factory=list)
    failures: list[AssertionIssue] = Field(default_factory=list)
    error: str | None = None
    error_kind: ErrorKind | None = None
    extracted: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] | None = None


class RegressionDifference(StrictModel):
    path: str
    kind: Literal["added", "removed", "changed", "type_changed"]
    expected: Any | None = None
    actual: Any | None = None


class SuiteResult(StrictModel):
    name: str
    source: str
    status: StepStatus
    duration_ms: float
    steps: list[StepResult]
    cleanup_failures: list[str] = Field(default_factory=list)
    mock_failures: list[str] = Field(default_factory=list)
    regression_failures: list[RegressionDifference] = Field(default_factory=list)


class RunResult(StrictModel):
    status: StepStatus
    duration_ms: float
    suites: list[SuiteResult]


class BenchmarkResult(StrictModel):
    requests: int
    passed: int
    failed: int
    concurrency: int
    duration_ms: float
    throughput: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    exit_code: int
