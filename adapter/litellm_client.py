from __future__ import annotations

import asyncio
import json
import logging
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from adapter.models import SearchTool

logger = logging.getLogger("adapter.litellm")


class LiteLLMAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LiteLLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int = 30,
        max_response_bytes: int = 2_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10)),
            follow_redirects=False,
            transport=transport,
        )
        self._max_response_bytes = max_response_bytes

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request_json(self, method: str, path: str, *, body: dict | None = None) -> dict:
        for attempt in range(3):
            try:
                response = await self._client.request(method, path, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                raise LiteLLMAPIError("LiteLLM request failed due to a network or timeout error") from exc

            if response.status_code in {401, 403}:
                raise LiteLLMAPIError(
                    f"LiteLLM denied this request (HTTP {response.status_code})",
                    status_code=response.status_code,
                )

            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt < 2:
                    await asyncio.sleep(self._retry_delay(response.headers.get("Retry-After"), attempt))
                    continue

            if response.status_code < 200 or response.status_code >= 300:
                raise LiteLLMAPIError(
                    f"LiteLLM returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )

            if len(response.content) > self._max_response_bytes:
                raise LiteLLMAPIError("LiteLLM response exceeded the configured size limit")
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise LiteLLMAPIError("LiteLLM returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise LiteLLMAPIError("LiteLLM returned an unexpected JSON response")
            return payload

        raise LiteLLMAPIError("LiteLLM request failed after bounded retries")

    @staticmethod
    def _retry_delay(retry_after: str | None, attempt: int) -> float:
        if retry_after:
            try:
                return min(5.0, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return min(5.0, max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds()))
                except (TypeError, ValueError, OverflowError):
                    pass
        return 0.25 * (2**attempt)

    async def discover_tools(self) -> list[SearchTool]:
        payload = await self._request_json("GET", "/v1/search/tools")
        entries = payload.get("data")
        if payload.get("object") != "list" or not isinstance(entries, list):
            raise LiteLLMAPIError("LiteLLM Search Tool discovery returned an invalid schema")

        discovered: list[SearchTool] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise LiteLLMAPIError("LiteLLM Search Tool discovery contained an invalid entry")
            name = entry.get("search_tool_name")
            provider = entry.get("search_provider")
            description = entry.get("description")
            if not isinstance(name, str) or not name.strip():
                raise LiteLLMAPIError("LiteLLM Search Tool discovery contained an invalid tool name")
            if not isinstance(provider, str) or not provider.strip():
                raise LiteLLMAPIError("LiteLLM Search Tool discovery contained an invalid provider")
            name = name.strip()
            if name in seen:
                raise LiteLLMAPIError("LiteLLM Search Tool discovery contained duplicate names")
            if description is not None and not isinstance(description, str):
                raise LiteLLMAPIError("LiteLLM Search Tool discovery contained an invalid description")
            seen.add(name)
            discovered.append(SearchTool(name=name, provider=provider.strip(), description=description))
        return discovered

    async def search(self, tool_name: str, query: str, max_results: int) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if len(query) > 4000:
            raise ValueError("query must not exceed 4000 characters")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 20:
            raise ValueError("max_results must be an integer between 1 and 20")
        encoded_name = quote(tool_name, safe="")
        payload = await self._request_json(
            "POST",
            f"/v1/search/{encoded_name}",
            body={"query": query.strip(), "max_results": max_results},
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise LiteLLMAPIError("LiteLLM Search response did not contain a results array")
        return payload
