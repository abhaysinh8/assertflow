# Prompt compliance review

This review maps the supplied AssertFlow requirements to the implemented version 0.1 repository.
It was last verified on 2026-08-27.

| Requirement area | Implementation evidence | Status |
| --- | --- | --- |
| Python project and CLI | `pyproject.toml`, `assertflow run`, `validate`, `snapshot`, `benchmark` | Complete |
| Strict YAML and models | safe PyYAML loader, unknown-field rejection, Pydantic v2 models | Complete |
| HTTP execution | async `httpx`, reusable clients, five required methods, JSON/form/raw bodies, query, redirects, timeouts, secure TLS default | Complete |
| Assertions | status, headers, JSON paths, existence, type, numeric, string, collection, JSON Schema | Complete |
| Variables and extraction | typed `${name}` substitution, precedence, recursion detection, escaping, body/header/status extraction | Complete |
| Integration workflows | ordered setup, steps, state chaining, teardown-after-failure | Complete |
| Failure testing and retries | deterministic mock delays/errors/malformed bodies, timeout enforcement, explicit status retries and attempt history | Complete |
| Mocks | static/sequential responses, method/path routing, header/query/JSON/raw-body verification, exact call counts | Complete |
| Regression testing | readable snapshots, added/removed/value/type diffs, exact validated ignore paths | Complete |
| Concurrency | bounded independent-suite/repeat concurrency with isolated state; ordered dependent steps | Complete |
| Reporting and CI | terminal, JSON, JUnit, deterministic exit codes, GitHub Actions | Complete |
| Secrets and safety | built-in and configurable redaction, safe schema paths, no expression evaluation, sanitized errors | Complete |
| Benchmarking | measured single-request scenario benchmark with concurrency, throughput, mean, p50/p95/p99 | Complete |
| Examples and documentation | auth, CRUD, order, regression, mock failures, schema, benchmark, exact user-lifecycle quick start | Complete |
| Framework tests | 61 deterministic tests plus Ruff, formatter, strict mypy, and wheel build | Complete |

HTML reporting remains intentionally absent because the prompt made it conditional on adding genuine
value; terminal, JSON, and JUnit are the three required formats. Custom TLS/ALPN, MITM, HTTP/3,
QUIC, packet capture, browsers, Kubernetes, and distributed workers are explicit non-goals.

The Rust reference at `D:\reqrun` was inspected for product behavior. AssertFlow retains strict
collections, chaining, assertions, mock verification, diffs, reports, retries, and CI semantics.
It does not copy the recording proxy, certificate authority, QUIC/H3, browser parity, or
session-version architecture.

The final concurrency rule is deliberately simple: suite instances may overlap; steps within a
suite never do. Regression comparison operates on secret-redacted response documents, and ignore
paths must match so stale exclusions cannot silently weaken coverage.
