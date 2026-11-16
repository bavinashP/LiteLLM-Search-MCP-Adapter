from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from mcp.server.mcpserver.context import Context
from mcp.server.subscriptions import InMemorySubscriptionBus

from adapter.litellm_client import LiteLLMAPIError, LiteLLMClient
from adapter.models import SearchTool

logger = logging.getLogger("adapter.registry")


class RegistryError(RuntimeError):
    pass


def mcp_tool_name(original_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", original_name.lower()).strip("_")
    if not slug:
        raise RegistryError("Search Tool name cannot be converted to a valid MCP tool name")
    return f"litellm_search__{slug}"


@dataclass(frozen=True, slots=True)
class RegistryStatus:
    ready: bool
    tool_count: int
    last_refresh_ok: bool


class DynamicToolRegistry:
    def __init__(
        self,
        server,
        subscriptions: InMemorySubscriptionBus,
        client: LiteLLMClient,
        allowlist: frozenset[str],
    ) -> None:
        self._server = server
        self._subscriptions = subscriptions
        self._client = client
        self._allowlist = allowlist
        self._registered: dict[str, SearchTool] = {}
        self._lock = asyncio.Lock()
        self._ready = False
        self._last_refresh_ok = False

    @property
    def status(self) -> RegistryStatus:
        return RegistryStatus(self._ready, len(self._registered), self._last_refresh_ok)

    async def refresh(self) -> bool:
        try:
            discovered = await self._client.discover_tools()
            candidates = [tool for tool in discovered if tool.name in self._allowlist]
            desired: dict[str, SearchTool] = {}
            for tool in candidates:
                key = mcp_tool_name(tool.name)
                if key in desired and desired[key].name != tool.name:
                    raise RegistryError("MCP tool-name normalization collision detected")
                desired[key] = tool
        except (LiteLLMAPIError, RegistryError):
            self._last_refresh_ok = False
            logger.warning("Search Tool discovery/validation failed; retaining last-known-good registry")
            return False

        changed = False
        async with self._lock:
            for key, current in list(self._registered.items()):
                replacement = desired.get(key)
                if replacement is None or replacement != current:
                    self._server.remove_tool(key)
                    del self._registered[key]
                    changed = True

            for key, tool in desired.items():
                if key not in self._registered:
                    self._server.add_tool(
                        self._build_handler(tool.name),
                        name=key,
                        title=tool.name,
                        description="Search the web through an explicitly approved LiteLLM Search Tool.",
                        structured_output=False,
                    )
                    self._registered[key] = tool
                    changed = True

            if changed:
                context = Context(mcp_server=self._server, subscriptions=self._subscriptions)
                await context.notify_tools_changed()

        self._ready = True
        self._last_refresh_ok = True
        logger.info("Search Tool registry refresh completed; exposed_tools=%d changed=%s", len(self._registered), changed)
        return True

    def _build_handler(self, original_name: str):
        async def search(query: str, max_results: int = 5) -> str:
            import json

            response = await self._client.search(original_name, query, max_results)
            return json.dumps(response, ensure_ascii=False)

        search.__name__ = f"search_{mcp_tool_name(original_name)}"
        return search

    async def run_refresh_loop(self, interval_seconds: int) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(interval_seconds)
