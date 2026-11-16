from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from mcp.server import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from adapter.config import Settings
from adapter.litellm_client import LiteLLMClient
from adapter.registry import DynamicToolRegistry
from adapter.security import BearerAuthMiddleware

logger = logging.getLogger("adapter.server")


def build_app(settings: Settings):
    subscriptions = InMemorySubscriptionBus()
    client = LiteLLMClient(
        settings.litellm_base_url,
        settings.litellm_api_key,
        settings.request_timeout_seconds,
        settings.max_response_bytes,
    )
    registry_holder: dict[str, DynamicToolRegistry] = {}
    refresh_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        nonlocal refresh_task
        registry = registry_holder["registry"]
        refresh_task = asyncio.create_task(registry.run_refresh_loop(settings.refresh_seconds))
        try:
            yield {}
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass
            await client.aclose()

    server = MCPServer(
        name="LiteLLM Search MCP Adapter",
        version="0.1.0",
        instructions="Search using explicitly approved LiteLLM Search Tools.",
        subscriptions=subscriptions,
        lifespan=lifespan,
        log_level=settings.log_level,
    )
    registry = DynamicToolRegistry(server, subscriptions, client, settings.allowed_search_tools)
    registry_holder["registry"] = registry

    @server.custom_route("/health/live", methods=["GET"], include_in_schema=False)
    async def liveness(_request: Request):
        return JSONResponse({"status": "alive"})

    @server.custom_route("/health/ready", methods=["GET"], include_in_schema=False)
    async def readiness(_request: Request):
        status = registry.status
        ready = status.ready and status.last_refresh_ok
        body = {
            "status": "ready" if ready else "degraded",
            "registered_tools": status.tool_count,
        }
        return JSONResponse(body, status_code=200 if ready else 503)

    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=False,
        max_request_body_size=1_048_576,
        transport_security=transport_security,
        host=settings.host,
    )
    return BearerAuthMiddleware(app, settings.mcp_bearer_token)


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = build_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
