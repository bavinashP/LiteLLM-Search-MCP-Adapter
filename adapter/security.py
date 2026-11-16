from __future__ import annotations

import asyncio
import hmac
import time
from collections import deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuthMiddleware:
    """Protect the MCP endpoint with a deployment-managed bearer token and global rate limit."""

    def __init__(self, app: ASGIApp, token: str, requests_per_minute: int = 120) -> None:
        self.app = app
        self._token = token
        self._limit = requests_per_minute
        self._requests: deque[float] = deque()
        self._rate_lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in {"/health/live", "/health/ready"}:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, supplied = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not hmac.compare_digest(supplied, self._token):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        if path == "/mcp":
            now = time.monotonic()
            retry_after: int | None = None
            async with self._rate_lock:
                while self._requests and now - self._requests[0] >= 60:
                    self._requests.popleft()
                if len(self._requests) >= self._limit:
                    retry_after = max(1, int(60 - (now - self._requests[0])))
                else:
                    self._requests.append(now)
            if retry_after is not None:
                response = JSONResponse(
                    {"error": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
