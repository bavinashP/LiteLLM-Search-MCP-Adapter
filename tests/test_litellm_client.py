import httpx
import pytest

from adapter.litellm_client import LiteLLMAPIError, LiteLLMClient


@pytest.mark.asyncio
async def test_discovery_parses_tools():
    def handler(request):
        assert request.url.path == "/v1/search/tools"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"object": "list", "data": [
            {"search_tool_name": "spp_Parallel", "search_provider": "parallel_ai"}
        ]})

    client = LiteLLMClient("http://litellm.test", "test-key", transport=httpx.MockTransport(handler))
    try:
        tools = await client.discover_tools()
        assert [(tool.name, tool.provider) for tool in tools] == [("spp_Parallel", "parallel_ai")]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_encodes_tool_name_and_preserves_results():
    def handler(request):
        assert request.url.raw_path == b"/v1/search/a%2Fb"
        assert request.read() == b'{"query":"mcp","max_results":3}'
        return httpx.Response(200, json={"results": [{"title": "A", "url": "https://example.test", "snippet": "B"}]})

    client = LiteLLMClient("http://litellm.test", "test-key", transport=httpx.MockTransport(handler))
    try:
        response = await client.search("a/b", "mcp", 3)
        assert len(response["results"]) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_rejects_invalid_max_results():
    client = LiteLLMClient("http://litellm.test", "test-key", transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    try:
        with pytest.raises(ValueError):
            await client.search("tool", "query", 21)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_discovery_rejects_duplicate_names():
    payload = {"object": "list", "data": [
        {"search_tool_name": "same", "search_provider": "one"},
        {"search_tool_name": "same", "search_provider": "two"},
    ]}
    client = LiteLLMClient(
        "http://litellm.test", "test-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    try:
        with pytest.raises(LiteLLMAPIError):
            await client.discover_tools()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_does_not_retry_server_errors():
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "unavailable"})

    client = LiteLLMClient("http://litellm.test", "test-key", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(LiteLLMAPIError):
            await client.search("tool", "query", 2)
        assert calls == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_retries_rate_limit_once_then_succeeds():
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "limited"})
        return httpx.Response(200, json={"results": []})

    client = LiteLLMClient("http://litellm.test", "test-key", transport=httpx.MockTransport(handler))
    try:
        response = await client.search("tool", "query", 2)
        assert response == {"results": []}
        assert calls == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_response_size_is_bounded():
    client = LiteLLMClient(
        "http://litellm.test",
        "test-key",
        max_response_bytes=1024,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'{"results":[' + b" " * 2048 + b"]}")),
    )
    try:
        with pytest.raises(LiteLLMAPIError, match="size limit"):
            await client.search("tool", "query", 2)
    finally:
        await client.aclose()
