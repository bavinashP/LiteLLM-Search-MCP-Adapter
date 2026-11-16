import pytest
from starlette.responses import PlainTextResponse

from adapter.security import BearerAuthMiddleware


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _run(middleware, path, headers):
    messages = []

    async def app(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "method": "POST", "path": path, "headers": headers}
    await middleware(scope, _receive, send)
    return messages


@pytest.mark.asyncio
async def test_missing_bearer_token_is_rejected():
    middleware = BearerAuthMiddleware(lambda *_: None, "s" * 64)
    messages = await _run(middleware, "/mcp", [])
    assert messages[0]["status"] == 401


@pytest.mark.asyncio
async def test_invalid_bearer_token_is_rejected():
    middleware = BearerAuthMiddleware(lambda *_: None, "s" * 64)
    messages = await _run(middleware, "/mcp", [(b"authorization", b"Bearer wrong")])
    assert messages[0]["status"] == 401


@pytest.mark.asyncio
async def test_valid_bearer_token_reaches_application():
    token = "s" * 64
    async def app(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    middleware = BearerAuthMiddleware(app, token)
    messages = await _run(middleware, "/mcp", [(b"authorization", f"Bearer {token}".encode())])
    assert messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_liveness_route_does_not_require_bearer_token():
    async def app(scope, receive, send):
        await PlainTextResponse("alive")(scope, receive, send)

    middleware = BearerAuthMiddleware(app, "s" * 64)
    messages = await _run(middleware, "/health/live", [])
    assert messages[0]["status"] == 200
