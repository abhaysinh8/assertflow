"""Async HTTP execution with explicit retry behavior."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

import httpx

from assertflow.errors import RequestExecutionError, RequestTimeoutError
from assertflow.models import AttemptResult, RequestConfig, RetryConfig
from assertflow.secrets import redact, sanitize_url


class HttpExecutor:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        secret_names: set[str] | None = None,
    ) -> None:
        self._transport = transport
        self._secret_names = secret_names or set()
        self._clients: dict[bool, httpx.AsyncClient] = {}

    def _client(self, verify_tls: bool) -> httpx.AsyncClient:
        key = True if self._transport is not None else verify_tls
        if key not in self._clients:
            self._clients[key] = httpx.AsyncClient(
                verify=verify_tls,
                http2=True,
                transport=self._transport,
            )
        return self._clients[key]

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    async def execute(
        self,
        request: RequestConfig,
        retry: RetryConfig,
    ) -> tuple[httpx.Response, list[AttemptResult]]:
        attempts: list[AttemptResult] = []
        for number in range(1, retry.attempts + 1):
            started = perf_counter()
            try:
                response = await self._client(request.verify_tls).request(
                    method=request.method,
                    url=request.url,
                    headers=request.headers,
                    params=request.params,
                    json=request.json_body,
                    data=request.form,
                    content=request.body,
                    timeout=request.timeout,
                    follow_redirects=request.follow_redirects,
                    extensions={"assertflow_verify_tls": request.verify_tls},
                )
            except httpx.TimeoutException as exc:
                duration = (perf_counter() - started) * 1000
                attempts.append(
                    AttemptResult(attempt=number, duration_ms=duration, error="request timed out")
                )
                raise RequestTimeoutError(
                    f"{request.method} {sanitize_url(request.url, self._secret_names)} timed out "
                    f"after {request.timeout:g}s"
                ) from exc
            except httpx.HTTPError as exc:
                duration = (perf_counter() - started) * 1000
                message = type(exc).__name__
                attempts.append(AttemptResult(attempt=number, duration_ms=duration, error=message))
                raise RequestExecutionError(
                    f"{request.method} {sanitize_url(request.url, self._secret_names)} "
                    "could not be completed "
                    f"({type(exc).__name__})"
                ) from exc

            duration = (perf_counter() - started) * 1000
            attempts.append(
                AttemptResult(
                    attempt=number,
                    status_code=response.status_code,
                    duration_ms=duration,
                )
            )
            should_retry = response.status_code in retry.on_status and number < retry.attempts
            if not should_retry:
                return response, attempts
            if retry.delay_ms:
                await asyncio.sleep(retry.delay_ms / 1000)
        raise AssertionError("retry loop must return or raise")


def capture_response(
    response: httpx.Response,
    secret_names: set[str] | None = None,
) -> dict[str, Any]:
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    captured = redact(
        {
            "status": response.status_code,
            "headers": {key.lower(): value for key, value in response.headers.items()},
            "body": body,
        },
        secret_names,
    )
    if not isinstance(captured, dict):
        raise AssertionError("captured response must remain a mapping")
    return captured
