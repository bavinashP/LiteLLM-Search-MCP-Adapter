from __future__ import annotations

import asyncio
import json
import socket

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.client.subscriptions import ToolsListChanged
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from adapter.config import Settings
from adapter.server import build_app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_started(server: uvicorn.Server, task: asyncio.Task) -> None:
    for _ in range(100):
        if server.started:
            return
        if task.done():
            await task
        await asyncio.sleep(0.05)
    raise AssertionError("Uvicorn server did not start")


def _assert_search_result(result, expected_title: str, forbidden_secret: str) -> None:
    assert result.is_error is not True
    assert len(result.content) == 1
    text = result.content[0].text
    payload = json.loads(text)
    assert payload["results"][0]["title"] == expected_title
    assert forbidden_secret not in text


@pytest.mark.asyncio
async def test_streamable_http_search_and_dynamic_notifications():
    adapter_port = _free_port()
    litellm_port = _free_port()
    adapter_token = "a" * 64
    litellm_key = "ci-dummy-litellm-key"
    search_calls: list[dict] = []
    discovered_tool_names = ["spp_Parallel"]

    async def discovery(request: Request):
        if request.headers.get("authorization") != f"Bearer {litellm_key}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({
            "object": "list",
            "data": [
                {
                    "search_tool_name": name,
                    "search_provider": "parallel_ai" if name == "spp_Parallel" else "tavily",
                }
                for name in discovered_tool_names
            ],
        })

    async def search(request: Request):
        if request.headers.get("authorization") != f"Bearer {litellm_key}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        tool_name = request.path_params["tool_name"]
        body = await request.json()
        search_calls.append({"tool": tool_name, **body})
        return JSONResponse({
            "results": [
                {"title": f"Search via {tool_name}", "url": "https://example.test/mcp", "snippet": "verified"}
            ]
        })

    litellm_app = Starlette(routes=[
        Route("/v1/search/tools", discovery, methods=["GET"]),
        Route("/v1/search/{tool_name}", search, methods=["POST"]),
    ])
    litellm_server = uvicorn.Server(
        uvicorn.Config(litellm_app, host="127.0.0.1", port=litellm_port, log_level="error", lifespan="off")
    )
    litellm_task = asyncio.create_task(litellm_server.serve())

    settings = Settings(
        litellm_base_url=f"http://127.0.0.1:{litellm_port}",
        litellm_api_key=litellm_key,
        mcp_bearer_token=adapter_token,
        allowed_search_tools=frozenset({"spp_Parallel", "openhands_tavily"}),
        allowed_hosts=(f"127.0.0.1:{adapter_port}",),
        allowed_origins=(),
        refresh_seconds=10,
        request_timeout_seconds=5,
        max_response_bytes=1024 * 1024,
        mcp_requests_per_minute=120,
        host="127.0.0.1",
        port=adapter_port,
        log_level="ERROR",
    )
    adapter_app = build_app(settings)
    adapter_server = uvicorn.Server(
        uvicorn.Config(adapter_app, host="127.0.0.1", port=adapter_port, log_level="error", lifespan="on")
    )
    adapter_task = asyncio.create_task(adapter_server.serve())

    try:
        await _wait_started(litellm_server, litellm_task)
        await _wait_started(adapter_server, adapter_task)

        async with httpx.AsyncClient(timeout=2) as poll_client:
            ready = False
            for _ in range(100):
                try:
                    response = await poll_client.get(f"http://127.0.0.1:{adapter_port}/health/ready")
                    if response.status_code == 200:
                        ready = True
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
            assert ready is True

        # Verify the legacy 2025-11-25 ClientSession path still works.
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {adapter_token}"},
            timeout=httpx2.Timeout(10, read=20),
        ) as legacy_http:
            legacy_transport = streamable_http_client(
                url=f"http://127.0.0.1:{adapter_port}/mcp",
                http_client=legacy_http,
            )
            async with legacy_transport as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as legacy_session:
                    await legacy_session.initialize()
                    assert legacy_session.protocol_version == "2025-11-25"
                    legacy_tools = await legacy_session.list_tools()
                    assert [tool.name for tool in legacy_tools.tools] == ["litellm_search__spp_parallel"]
                    legacy_result = await legacy_session.call_tool(
                        "litellm_search__spp_parallel",
                        {"query": "legacy protocol", "max_results": 1},
                    )
                    _assert_search_result(legacy_result, "Search via spp_Parallel", litellm_key)

        # Verify the modern 2026-07-28 Client API and live tools-changed subscription.
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {adapter_token}"},
            timeout=httpx2.Timeout(10, read=20),
        ) as modern_http:
            modern_transport = streamable_http_client(
                url=f"http://127.0.0.1:{adapter_port}/mcp",
                http_client=modern_http,
            )
            async with Client(modern_transport) as client:
                assert client.protocol_version == "2026-07-28"
                listed = await client.list_tools()
                assert [tool.name for tool in listed.tools] == ["litellm_search__spp_parallel"]

                async with client.listen(tools_list_changed=True) as subscription:
                    discovered_tool_names.append("openhands_tavily")
                    event = await asyncio.wait_for(subscription.__anext__(), timeout=15)
                    assert isinstance(event, ToolsListChanged)
                    updated = await client.list_tools()
                    updated_names = [tool.name for tool in updated.tools]
                    assert updated_names == [
                        "litellm_search__spp_parallel",
                        "litellm_search__openhands_tavily",
                    ]

                modern_result = await client.call_tool(
                    "litellm_search__openhands_tavily",
                    {"query": "modern protocol", "max_results": 1},
                )
                _assert_search_result(modern_result, "Search via openhands_tavily", litellm_key)

        assert search_calls == [
            {"tool": "spp_Parallel", "query": "legacy protocol", "max_results": 1},
            {"tool": "openhands_tavily", "query": "modern protocol", "max_results": 1},
        ]
    finally:
        adapter_server.should_exit = True
        litellm_server.should_exit = True
        await asyncio.wait_for(adapter_task, timeout=10)
        await asyncio.wait_for(litellm_task, timeout=10)
