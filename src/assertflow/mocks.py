"""Deterministic in-process mock HTTP service backed by Starlette."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from assertflow.errors import MockError
from assertflow.models import MockResponseConfig, MockRouteConfig
from assertflow.secrets import MASK, is_secret_name, redact


class _RoutingTransport(httpx.AsyncBaseTransport):
    def __init__(self, app: Starlette) -> None:
        self._mock = httpx.ASGITransport(app=app)
        self._network: dict[bool, httpx.AsyncHTTPTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host in {"mock", "assertflow.mock"}:
            timeout = request.extensions.get("timeout")
            read_timeout = timeout.get("read") if isinstance(timeout, dict) else None
            try:
                if isinstance(read_timeout, (int, float)):
                    return await asyncio.wait_for(
                        self._mock.handle_async_request(request),
                        timeout=read_timeout,
                    )
                return await self._mock.handle_async_request(request)
            except TimeoutError as exc:
                raise httpx.ReadTimeout(
                    "mock response exceeded request timeout", request=request
                ) from exc
        verify_tls = bool(request.extensions.get("assertflow_verify_tls", True))
        if verify_tls not in self._network:
            self._network[verify_tls] = httpx.AsyncHTTPTransport(
                verify=verify_tls,
                http2=True,
            )
        return await self._network[verify_tls].handle_async_request(request)

    async def aclose(self) -> None:
        await self._mock.aclose()
        for transport in self._network.values():
            await transport.aclose()


class MockService:
    """Match exact method/path pairs and retain suite-local call counts."""

    def __init__(
        self,
        routes: Sequence[MockRouteConfig],
        secret_names: set[str] | None = None,
    ) -> None:
        self._routes = list(routes)
        self._secret_names = secret_names or set()
        self._calls = [0] * len(self._routes)
        self._request_failures: list[str] = []
        keys = [(route.request.method, route.request.path) for route in self._routes]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            rendered = ", ".join(f"{method} {path}" for method, path in duplicates)
            raise MockError(f"duplicate mock routes: {rendered}")
        self._app = Starlette(
            routes=[
                Route(
                    "/{path:path}",
                    self._handle,
                    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                )
            ]
        )

    def transport(self) -> httpx.AsyncBaseTransport:
        return _RoutingTransport(self._app)

    async def _handle(self, request: Request) -> Response:
        method = request.method.upper()
        path = request.url.path
        for index, route in enumerate(self._routes):
            if route.request.method == method and route.request.path == path:
                call_index = self._calls[index]
                self._calls[index] += 1
                self._request_failures.extend(await self._verify_request(route, request))
                response = self._response_for(route, call_index)
                if response.delay_ms:
                    await asyncio.sleep(response.delay_ms / 1000)
                headers = dict(response.headers)
                if response.json_body is not None:
                    return JSONResponse(
                        response.json_body,
                        status_code=response.status,
                        headers=headers,
                    )
                return PlainTextResponse(
                    response.body or "",
                    status_code=response.status,
                    headers=headers,
                )
        return JSONResponse(
            {"error": "unmatched mock request", "method": method, "path": path},
            status_code=404,
        )

    async def _verify_request(self, route: MockRouteConfig, request: Request) -> list[str]:
        name = route.name or f"{route.request.method} {route.request.path}"
        failures: list[str] = []
        for header, expected in route.request.headers.items():
            actual = request.headers.get(header)
            if actual != expected:
                if is_secret_name(header, self._secret_names):
                    expected_display: Any = MASK
                    actual_display: Any = MASK if actual is not None else None
                else:
                    expected_display = expected
                    actual_display = actual
                failures.append(
                    f"{name}: header {header!r} expected {expected_display!r}, "
                    f"observed {actual_display!r}"
                )
        for key, expected in route.request.params.items():
            actual = request.query_params.get(key)
            expected_display = MASK if is_secret_name(key, self._secret_names) else expected
            actual_display = (
                MASK if actual is not None and is_secret_name(key, self._secret_names) else actual
            )
            if actual != expected:
                failures.append(
                    f"{name}: query {key!r} expected {expected_display!r}, "
                    f"observed {actual_display!r}"
                )
        if route.request.json_body is not None:
            try:
                actual_json = await request.json()
            except json.JSONDecodeError:
                failures.append(f"{name}: expected JSON request body, observed malformed JSON")
            else:
                if actual_json != route.request.json_body:
                    failures.append(
                        f"{name}: JSON body expected "
                        f"{redact(route.request.json_body, self._secret_names)!r}, observed "
                        f"{redact(actual_json, self._secret_names)!r}"
                    )
        elif route.request.body is not None:
            actual_body = (await request.body()).decode("utf-8", errors="replace")
            if actual_body != route.request.body:
                failures.append(f"{name}: raw request body did not match")
        return failures

    @staticmethod
    def _response_for(route: MockRouteConfig, call_index: int) -> MockResponseConfig:
        if route.response is not None:
            return route.response
        if not route.responses:
            raise MockError("validated mock route has no response")
        return route.responses[min(call_index, len(route.responses) - 1)]

    def verify(self) -> list[str]:
        failures: list[str] = list(self._request_failures)
        for route, actual in zip(self._routes, self._calls, strict=True):
            if route.expect_calls is not None and actual != route.expect_calls:
                name = route.name or f"{route.request.method} {route.request.path}"
                failures.append(f"{name}: expected {route.expect_calls} call(s), observed {actual}")
        return failures
