from __future__ import annotations

import asyncio
import socket

import httpx2
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from adapter.config import Settings
from adapter.server import build_app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_streamable_http_protocol_handshake_and_tools_list():
    port = _free_port()
    token = "a" * 64
    settings = Settings(
        litellm_base_url="http://127.0.0.1:9",
        litellm_api_key="ci-dummy-litellm-key",
        mcp_bearer_token=token,
        allowed_search_tools=frozenset(),
        allowed_hosts=(f"127.0.0.1:{port}",),
        allowed_origins=(),
        refresh_seconds=30,
        request_timeout_seconds=5,
        max_response_bytes=1024 * 1024,
        mcp_requests_per_minute=120,
        host="127.0.0.1",
        port=port,
        log_level="ERROR",
    )
    app = build_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    )
    server_task = asyncio.create_task(server.serve())

    try:
        for _ in range(100):
            if server.started:
                break
            if server_task.done():
                await server_task
            await asyncio.sleep(0.05)
        assert server.started is True

        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx2.Timeout(10, read=20),
        ) as http_client:
            async with streamable_http_client(
                url=f"http://127.0.0.1:{port}/mcp",
                http_client=http_client,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    assert result.tools == []
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
