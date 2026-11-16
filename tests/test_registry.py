import pytest
from mcp.server import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus

from adapter.litellm_client import LiteLLMAPIError
from adapter.models import SearchTool
from adapter.registry import DynamicToolRegistry


class FakeClient:
    def __init__(self):
        self.tools = [SearchTool("allowed_search", "test")]
        self.fail = False

    async def discover_tools(self):
        if self.fail:
            raise LiteLLMAPIError("temporary failure")
        return self.tools

    async def search(self, tool_name, query, max_results):
        return {"results": []}


@pytest.mark.asyncio
async def test_registry_exposes_only_allowlisted_tools_and_retains_last_good():
    bus = InMemorySubscriptionBus()
    server = MCPServer("registry-test", subscriptions=bus)
    client = FakeClient()
    registry = DynamicToolRegistry(server, bus, client, frozenset({"allowed_search"}))

    assert await registry.refresh() is True
    tools = await server.list_tools()
    assert [tool.name for tool in tools] == ["litellm_search__allowed_search"]

    client.fail = True
    assert await registry.refresh() is False
    tools_after_failure = await server.list_tools()
    assert [tool.name for tool in tools_after_failure] == ["litellm_search__allowed_search"]


@pytest.mark.asyncio
async def test_empty_allowlist_exposes_no_tools():
    bus = InMemorySubscriptionBus()
    server = MCPServer("registry-test", subscriptions=bus)
    client = FakeClient()
    registry = DynamicToolRegistry(server, bus, client, frozenset())

    assert await registry.refresh() is True
    assert await server.list_tools() == []
