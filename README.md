# AssertFlow

AssertFlow is a Python 3.12+ command-line framework for declarative API integration and
regression testing. It executes ordered YAML workflows, carries extracted response data between
steps, validates responses, compares readable snapshots, and emits deterministic CI reports.

It is designed for tests such as:

```text
authenticate -> create order -> read -> update -> cancel -> verify -> clean up
```

AssertFlow is not a load-testing platform, browser runner, traffic recorder, or custom networking
stack. It focuses on API workflow correctness and deterministic automation.

## Installation

```bash
python -m pip install -e ".[dev]"
assertflow --help
```

The runtime dependencies each have a narrow purpose: `httpx` sends async HTTP requests, Pydantic
validates suite models, PyYAML safely parses YAML, `jsonschema` validates contracts, Starlette
powers in-process mocks, Typer exposes the CLI, and Rich renders terminal results.

## Quick start

Run the exact self-contained user lifecycle command. AssertFlow automatically finds the nearest
`environments.yaml`; declared mocks make the example deterministic and offline:

```bash
assertflow run examples/user-lifecycle.yaml --env local
```

The larger order and authentication examples are also runnable:

```bash
assertflow run examples/order-flow/order-lifecycle.yaml -v
assertflow run examples/auth-flow/auth-lifecycle.yaml \
  --env local \
  --env-file examples/environments.yaml
```

## Suite format

A suite may contain environment selection, suite variables, mocks, setup, ordered workflow steps,
teardown, and regression rules:

```yaml
name: order-lifecycle
variables:
  base_url: http://mock
  symbol: AAPL

setup:
  - name: login
    request:
      method: POST
      url: ${base_url}/login
      json: {username: tester, password: test-password}
    assert: {status: 200}
    extract: {token: body.access_token}

steps:
  - name: create order
    request:
      method: POST
      url: ${base_url}/orders
      headers: {Authorization: "Bearer ${token}"}
      json: {symbol: "${symbol}", quantity: 10}
    assert:
      status: 201
      json:
        id: {exists: true, type: string}
        status: OPEN
    extract: {order_id: body.id}

teardown:
  - name: cleanup
    request: {method: DELETE, url: "${base_url}/test-data/${order_id}"}
```

Unknown fields and duplicate step names are rejected during validation. Supported methods are
`GET`, `POST`, `PUT`, `PATCH`, and `DELETE`. A request can define headers, query `params`, one of
`json`, `form`, or raw `body`, a positive `timeout`, `follow_redirects`, and `verify_tls`.

TLS verification defaults to `true`. Disabling it must be explicit in the individual request.

## Assertions

Response assertions use a mapping so paths remain easy to scan:

```yaml
assert:
  status: 200
  headers:
    content-type: {contains: application/json}
  json:
    user.id: {exists: true, type: integer}
    user.role: tester
    user.score: {greater_than_or_equal: 80}
    user.tags: {contains: api, not_empty: true}
    user.email: {regex: '^[^@]+@[^@]+$'}
```

A scalar rule means `equals`. Explicit operators are:

- `equals`, `not_equals`, and `exists`;
- `type`: `null`, `boolean`, `integer`, `number`, `string`, `array`, or `object`;
- `greater_than`, `greater_than_or_equal`, `less_than`, and `less_than_or_equal`;
- `contains`, `starts_with`, `ends_with`, and `regex`;
- `length`, `empty`, and `not_empty`.

Failures report the step, response path, operator, expected value, and actual value. Header lookup is
case-insensitive. A malformed body produces one clear JSON diagnostic instead of a stack trace.

## JSON Schema contracts

Schema files resolve relative to the suite and may not escape its directory:

```yaml
assert:
  status: 200
  schema: order.schema.json
```

AssertFlow uses JSON Schema Draft 2020-12 and prints the failing body path. See
`examples/schema-validation/` for a runnable contract test.

## Variables

`${NAME}` placeholders are resolved recursively. Precedence from lowest to highest is:

1. process environment;
2. the selected environment mapping;
3. suite `variables`;
4. values extracted by completed steps.

An exact placeholder preserves its native JSON type. If `quantity` is the integer `10`, then
`quantity: ${quantity}` remains an integer. A placeholder embedded in a larger string becomes text.
Use `$${NAME}` to produce the literal `${NAME}`. Undefined, recursive, or structurally embedded
variables fail before the request is sent.

Environment files contain named mappings:

```yaml
local:
  BASE_URL: http://localhost:8000
staging:
  BASE_URL: https://staging.example.test
```

Select one with:

```bash
assertflow run tests/ --env staging --env-file environments.yaml
```

When `--env-file` is omitted, AssertFlow searches the suite directory and its parents for the
nearest `environments.yaml`. Directory discovery distinguishes named suites from environment
mapping files.

## Response extraction and chaining

Extraction paths are intentionally small and non-executable:

```yaml
extract:
  token: body.access_token
  order_id: body.order.id
  first_item: body.items[0].id
  request_id: headers.x-request-id
  created_status: status
```

Paths support nested object properties and numeric array indexes. Missing paths and malformed JSON
produce configuration-class errors. Extracted values are suite-local and become available only to
later ordered steps.

## Setup and teardown

`setup` prepares deterministic state, `steps` describes the integration workflow, and `teardown`
cleans resources. A setup failure skips the main workflow. By default, a workflow failure skips
later dependent steps; `--continue-on-fail` is available for intentionally independent checks.

Teardown is attempted after execution begins even when setup or workflow assertions fail. Cleanup
failures are reported separately and still fail the suite.

## Mock dependencies and failure scenarios

Suites can declare exact in-process mock routes. Use `http://mock` or `http://assertflow.mock` as
the request host:

```yaml
mocks:
  - name: payment dependency
    request: {method: GET, path: /payments/123}
    responses:
      - {status: 503, json: {error: unavailable}}
      - {status: 200, json: {status: confirmed}}
    expect_calls: 2
```

Each route supports a static `response` or ordered `responses`, status, headers, JSON or raw body,
and deterministic `delay_ms`. The last sequential response repeats after the sequence is exhausted.
`expect_calls` verifies the exact observed count at suite completion. Mock requests can declare
expected `headers`, query `params`, and either exact `json` or raw `body`; mismatches fail mock
verification with redacted diagnostics. Unmatched requests receive a deterministic 404. Duplicate
method/path routes are rejected.

This supports stable 5xx, 429, malformed-body, and slow-dependency scenarios without random chaos.
It does not simulate a full remote service.

## Explicit retries

Retries are opt-in and status-specific:

```yaml
retry:
  attempts: 3
  delay_ms: 200
  on_status: [502, 503]
```

`attempts` is the total number of attempts, including the first. Connection and timeout errors are
not silently retried. `-v` displays each observed attempt, which keeps resilience tests auditable.

## Regression snapshots

Create a baseline only from a passing suite:

```bash
assertflow snapshot examples/regression/user-contract.yaml
```

Compare later responses:

```bash
assertflow run examples/regression/user-contract.yaml --regression
```

Snapshots are readable JSON under `.assertflow/snapshots/` by default. AssertFlow detects added and
removed fields, changed values, changed types, list changes, response status, and headers.

Dynamic response fields must be declared:

```yaml
regression:
  ignore:
    - headers.date
    - body.timestamp
    - body.trace_id
```

Every ignore path must match at least one current response when creating or checking a baseline.
This catches misspellings and stale ignore rules instead of silently weakening regression coverage.
Secret-looking keys are redacted before snapshots are stored.

## Concurrency and repeated scenarios

```bash
assertflow run tests/ --workers 8
assertflow run tests/create-order.yaml --repeat 100 --workers 20
```

Independent suite instances are asyncio tasks behind a bounded semaphore. Steps inside one suite
remain ordered because they can depend on extracted state. `--repeat` creates isolated suite
contexts and mock counters. This is useful for exposing state-sharing and duplicate-handling bugs,
but it is not a load-testing engine and does not publish throughput claims.

## Reports

Terminal output is the default:

```bash
assertflow run tests/
```

JSON and JUnit reports can be printed or written to a file:

```bash
assertflow run tests/ --report json --output reports/results.json
assertflow run tests/ --report junit --output reports/results.xml
```

All reporters consume the same validated result model. Reports contain suite, phase, step, attempt,
duration, status, assertion, cleanup, mock, and regression information. Sensitive-looking headers,
body keys, and URL query parameters are masked.

Use `-v` to display sanitized request URLs, extracted values, and retry attempts. Use `-vv` to add
the sanitized captured response. Suite-specific secret field names can be added with:

```yaml
secrets:
  - account_number
  - x-customer-id
```

These names participate in body, extraction, URL-query, mock-diagnostic, and header redaction.

## Lightweight benchmarking

After a single-request suite is correct, run measured repetitions with bounded concurrency:

```bash
assertflow benchmark examples/benchmark/health.yaml \
  --requests 500 \
  --concurrency 20
```

The command reports actual successes, failures, throughput, mean latency, and nearest-rank p50,
p95, and p99 latency for that invocation. It accepts exactly one suite containing one workflow
step and no setup or teardown. Each iteration has isolated variables, mock counters, and client
lifecycle, so this is a reproducible scenario benchmark—not a high-fidelity load generator. The
documentation publishes only measured results with their command and environment.

### Reference benchmark

The following results were measured on 2026-08-27 with the command above. The benchmark used the
in-process `examples/benchmark/health.yaml` mock scenario; it measures AssertFlow scenario overhead,
assertions, isolation, and scheduling without network latency.

Environment: Windows NT 10.0.26200, Python 3.14.3, AMD64, Intel64 Family 6 Model 140 Stepping 1.

| Metric | Run 1 | Run 2 | Run 3 | Three-run median |
| --- | ---: | ---: | ---: | ---: |
| Requests | 500 | 500 | 500 | 500 |
| Passed | 500 | 500 | 500 | 500 |
| Failed | 0 | 0 | 0 | 0 |
| Throughput | 1,446.39 req/s | 1,538.48 req/s | 1,548.45 req/s | 1,538.48 req/s |
| Mean latency | 0.65 ms | 0.62 ms | 0.62 ms | 0.62 ms |
| p50 latency | 0.48 ms | 0.52 ms | 0.48 ms | 0.48 ms |
| p95 latency | 1.38 ms | 1.17 ms | 1.26 ms | 1.26 ms |
| p99 latency | 2.43 ms | 2.43 ms | 2.67 ms | 2.43 ms |

These figures are a development-machine reference, not a product performance guarantee. Real API
results will include DNS, connection establishment, TLS, server work, and network variance. Compare
results only when the suite, machine, Python version, request count, and concurrency are equivalent.

### Benchmark optimization work

Planned improvements are deliberately separated from the current measured behavior:

1. Add explicit warm-up iterations excluded from measured samples, reducing cold-start distortion.
2. Add a reusable-client mode to measure persistent HTTP connections and HTTP/2 multiplexing,
   while retaining the current isolated-client mode for scenario-level costs.
3. Split latency into preparation, client setup, network, assertion, and reporting phases so
   bottlenecks can be attributed rather than guessed.
4. Add JSON benchmark output with environment metadata, committed baselines, and opt-in CI
   regression thresholds for throughput and p95/p99 latency.
5. Use a streaming histogram for larger runs instead of retaining every latency sample in memory.
6. Add fixed-rate scheduling and saturation reporting to distinguish target throughput from achieved
   throughput and expose queueing delay.
7. Add reproducible matrices across Python versions, concurrency levels, mock versus real endpoints,
   HTTP/1.1 versus HTTP/2, and secure versus intentionally insecure local TLS.
8. Profile variable rendering, Pydantic validation, response capture, and snapshot normalization
   before optimizing; changes should be accepted only with repeatable before/after evidence.

AssertFlow will remain a workflow correctness tool with lightweight benchmarking rather than trying
to replace dedicated load generators such as k6, Locust, or wrk.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | every suite passed |
| 1 | assertion, mock-verification, or regression failure |
| 2 | invalid YAML, configuration, variable, extraction, or schema path |
| 3 | HTTP/infrastructure execution failure |

The CLI never prompts for input, so the same behavior applies locally and in CI.

## CI integration

The repository workflow runs tests, linting, strict type checking, examples, and uploads JUnit
results. A project using AssertFlow can use:

```yaml
- name: Run API integration suites
  run: >-
    assertflow run tests/
    --env ci
    --env-file environments.yaml
    --report junit
    --output reports/results.xml
```

## Architecture

```text
safe YAML loading
  -> strict Pydantic suite model
  -> suite-local variable context
  -> ordered async runner
  -> httpx executor / Starlette mocks
  -> assertions and extraction
  -> normalized regression comparison
  -> shared results
  -> terminal / JSON / JUnit
```

Module responsibilities are deliberately direct:

- `parser.py` discovers suites, safely loads YAML, and selects environments;
- `models.py` defines configuration and result contracts;
- `variables.py` resolves typed placeholders;
- `http.py` owns reusable clients and explicit retries;
- `assertions.py` evaluates reusable operators and JSON Schema;
- `extraction.py` traverses response data;
- `runner.py` enforces phase ordering and suite isolation;
- `mocks.py` implements deterministic dependency behavior;
- `regression.py` normalizes, stores, and diffs responses;
- `reporting.py` renders one stable result model;
- `cli.py` maps commands and failures to deterministic exit codes.

The Rust product-reference audit and design rationale are in [`docs/design.md`](docs/design.md).

## Security considerations

- YAML is loaded with `yaml.safe_load`; `eval()` and arbitrary expression execution are absent.
- Unknown model fields are rejected.
- TLS verification is enabled unless a request explicitly disables it.
- Schema files cannot traverse outside the suite directory.
- Authorization, proxy authorization, cookies, passwords, API keys, and token-like keys are masked.
- Query parameters with secret-like names are redacted in results.
- Each suite owns its clients, variables, mock counters, and results; mutable state is not global.

Suites remain executable test input: they can send requests to configured URLs. Review untrusted
suites before running them, and use short-lived test credentials with limited permissions.

## Testing AssertFlow

```bash
python -m pytest
python -m ruff check .
python -m mypy src
```

The automated suite contains 61 tests covering positive, negative, and edge cases for parsing,
environments, variables, extraction, assertion families, JSON Schema, HTTP workflow chaining,
timeouts, retry sequences, teardown, mocks and request verification, snapshots, ignored fields,
reports, exit codes, CLI behavior, verbosity, custom secret masking, benchmarking, and
concurrency/repeat isolation.
Integration tests use in-process transports; they do not depend on public services or arbitrary
long sleeps.

## API integration vs other test levels

An endpoint test can validate one request in isolation. An integration workflow validates the
contract and state transitions across several API calls or dependencies. It remains narrower than
a browser-driven end-to-end test: AssertFlow starts at HTTP boundaries and does not validate a UI.

Ordered steps matter because real workflows carry state—tokens, identifiers, versions—from one
response into the next request. Mocks trade dependency realism for deterministic failure control;
real environments trade determinism for broader integration confidence. A practical test strategy
uses both.

## Limitations

- Mock routing is exact method plus path; expected body, query, and headers are verified after route
  selection rather than used to choose between duplicate routes.
- Assertions and extraction use simple dot/index paths, not JSONPath or arbitrary expressions.
- Regression ignore rules address exact paths, not wildcards.
- Retries currently target response status codes, not connection errors.
- Snapshots are local files; baseline approval workflow is left to source control review.
- HTTP behavior is delegated to `httpx`; there is no custom TLS, HTTP/3, QUIC, or packet capture.
- Parallelism is across isolated suite instances, never dependent steps within a suite.
- HTML reports are not part of version 0.1 because JSON and JUnit cover the stated CI use cases.

## Roadmap

Likely improvements, in priority order:

1. use expected request content as an optional mock routing discriminator;
2. step-scoped regression ignore rules and safe wildcard paths;
3. opt-in retry categories for selected transport failures;
4. snapshot approval and update summaries;
5. benchmark warm-up, client-reuse, JSON-baseline, and threshold modes described above;
6. HTML output only if it adds value beyond JSON/JUnit integrations.

## License

MIT
