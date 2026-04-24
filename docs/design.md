# AssertFlow design

## Reference audit

The Rust reference was inspected at `D:\reqrun` before substantial implementation. The review
covered its collection schema and loader, template context, runner/extraction/assertion pipeline,
replay diff, mock server, reporters, integration tests, examples, manifest, and user documentation.
It is treated strictly as a product reference; no Rust code is copied or mechanically translated.

## Keep

Concrete Rust product ideas worth retaining are:

- strict, declarative YAML collections with unknown-field rejection;
- response extraction and ordered request chaining;
- compact value, header, response-time, and JSON Schema assertions;
- explicit replay diffs with deterministic ignore rules;
- deterministic exit behavior and terminal, JSON, and JUnit reports;
- bounded parallel execution, visible retries, and fail/skip results;
- mock route matching, overrides, call verification, and deterministic unmatched responses.

## Remove

The Rust manifest and source show substantial dependencies and modules for capture and transport.
AssertFlow deliberately excludes those concerns because they do not improve its API integration
testing goal:

- recording proxies, CONNECT MITM, generated CA, and interception filters;
- custom ALPN, QUIC, HTTP/3 negotiation, and fallback diagnostics;
- browser parity fixtures and external Chromium probes;
- captured-session schema versioning and compatibility migrations;
- filesystem watch mode and record/replay-specific override languages;
- dependency-graph parallelism inside a stateful workflow.

TLS verification remains enabled by default and HTTP/2 is delegated to `httpx`.

## Redesign

The Python architecture is an explicit pipeline:

```text
YAML -> Pydantic models -> variable context -> ordered runner
     -> httpx executor -> assertions/extraction -> regression -> result reporters
```

Each suite owns its variables, HTTP client, mock state, and results. Independent suite instances
may run as asyncio tasks behind a bounded semaphore; steps inside one suite remain sequential.
This is intentionally simpler than the Rust runner's intra-collection dependency scheduler and
matches AssertFlow's integration-workflow semantics. It prevents cross-suite state leaks without
requiring global locks.

## YAML schema

A suite contains `name`, optional `environment`, `variables`, `mocks`, `setup`, ordered `steps`,
`teardown`, and optional `regression` rules. Every request is validated before execution. Assertion
rules are data-only; arbitrary Python expressions are never evaluated.

Variable precedence, from lowest to highest, is process environment, selected environment file,
suite variables, then values extracted by completed steps. An exact placeholder preserves its
native value type; an embedded placeholder becomes text. `$${NAME}` escapes a literal placeholder.

Extraction paths start with `body`, `headers`, or `status` and use dot-separated object keys and
integer array indexes. Teardown always runs after execution starts, even after setup or workflow
failure.

## Error and result model

Configuration, validation, variable, request, assertion, extraction, regression, mock, and
reporting failures are distinct exception categories. Expected test failures are captured in step
results; invalid input and infrastructure errors map to separate CLI exit codes.

Results are plain Pydantic models so terminal, JSON, and JUnit reporters consume the same stable
representation.

## Test strategy

Unit tests cover parsing, substitution, extraction, every assertion family, normalization, and
report serialization. Integration tests use in-process ASGI/mock transports for deterministic
workflow, timeout, teardown, retry, concurrency, and CLI behavior. No test depends on a public
network service or arbitrary long sleeps.
