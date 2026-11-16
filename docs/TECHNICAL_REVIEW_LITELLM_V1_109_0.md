# Technical Review — LiteLLM v1.109.0 Search API and MCP SDK

**Review date:** 2026-11-17  
**Repository:** `bavinashP/LiteLLM-Search-MCP-Adapter`  
**Review status:** Source-level review completed for LiteLLM Search endpoints; live smoke tests completed by project owner; initial adapter implementation committed; CI and deployment verification pending.

## 1. Executive conclusion

The standalone adapter design remains viable. The project owner's live tests establish that the supplied test Virtual Key can:

- list the two configured Search Tools through `GET /v1/search/tools`;
- execute `spp_Parallel` through `POST /v1/search/spp_Parallel`;
- execute `openhands_tavily` through `POST /v1/search/openhands_tavily`.

The exact LiteLLM v1.109.0 source confirms the endpoint paths and documents the request contract. One important security/design clarification was found: **the v1.109.0 `GET /v1/search/tools` implementation lists configured router Search Tools; it does not apply the key/team Search Tool allowlist filter.** The POST execution endpoint separately calls the Search Tool authorization check. Therefore, discovery output must never be treated as proof that the current Virtual Key is authorized to execute every listed tool.

At the time of this source/live-API review, no application code had been changed. The first implementation has since been committed and is tracked separately in `docs/IMPLEMENTATION_AND_VERIFICATION_STATUS.md`.

## 2. Live test evidence supplied by the project owner

### 2.1 Discovery

Request:
- `GET http://192.168.1.233:4000/v1/search/tools`
- Bearer authentication using the test Virtual Key.

Observed:
- HTTP `200 OK`
- `object: "list"`
- two entries:
  - `spp_Parallel`, provider `parallel_ai`
  - `openhands_tavily`, provider `tavily`

### 2.2 Parallel execution

Request:
- `POST /v1/search/spp_Parallel`
- body: `{"query":"MCP protocol Streamable HTTP specification","max_results":3}`

Observed:
- HTTP `200 OK`
- `x-litellm-version: 1.109.0`
- `x-litellm-model-group: spp_Parallel`
- `x-litellm-response-cost: 0.005`
- `x-litellm-response-duration-ms: 1675.414...`
- `x-litellm-attempted-retries: 0`
- `x-litellm-attempted-fallbacks: 0`
- JSON body has top-level `results`; returned result fields include `title`, `url`, and `snippet`.
- The provider returned one result although `max_results` was 3. The adapter must treat max_results as an upper bound/request, not a guarantee of exact result count.

### 2.3 Tavily execution

Request:
- `POST /v1/search/openhands_tavily`
- body: `{"query":"MCP Streamable HTTP official specification","max_results":3}`

Observed:
- HTTP `200 OK`
- `x-litellm-version: 1.109.0`
- `x-litellm-model-group: openhands_tavily`
- `x-litellm-response-cost: 0.008`
- `x-litellm-response-duration-ms: 2161.980...`
- `x-litellm-attempted-retries: 0`
- `x-litellm-attempted-fallbacks: 0`
- JSON body has top-level `results`; returned result fields include `title`, `url`, and `snippet`.
- Three results were returned.

The two live responses support a common initial MCP output shape based on a list of results with title, URL, and snippet. They do not prove that all optional fields or all future providers have identical response shapes. Preserve provider-returned fields safely rather than destructively normalizing to only three keys.

## 3. LiteLLM v1.109.0 source review

The following files were inspected at the exact `v1.109.0` Git tag in the official LiteLLM repository:

- `litellm/proxy/search_endpoints/endpoints.py`
- `litellm/proxy/auth/auth_checks.py`
- `litellm/types/search.py`

### 3.1 Search execution endpoint

The source defines:
- `POST /v1/search/{search_tool_name}`
- also legacy/alternate routes `/search/{search_tool_name}`, `/v1/search`, and `/search`.

For this adapter, continue using the explicit path form:
`POST /v1/search/{search_tool_name}`.

The URL path tool name takes precedence over a body `search_tool_name`. The adapter should use the configured registry name in the path and should not accept a caller-supplied tool name in the body.

### 3.2 Request parameters documented in the v1.109.0 endpoint source

The endpoint docstring specifies:

| Field | Type | Documented behavior |
|---|---|---|
| `query` | string or list of strings | Required |
| `max_results` | integer | Optional; 1–20; default 10 |
| `search_domain_filter` | list of strings | Optional; maximum 20 domains |
| `max_tokens_per_page` | integer | Optional; default 1024 |
| `country` | string | Optional country code |

Important qualification: these are LiteLLM's documented common/Perplexity-compatible request fields. They are not proof that every provider accepts every optional field identically. Initial adapter schema should expose only the required query and bounded max_results until provider-by-provider compatibility is verified. Add optional fields later only with tests for both configured providers and a clear provider-compatibility policy.

The source also documents `query` as accepting a string or list of strings. The first adapter release may deliberately expose a single non-empty string for a simpler, predictable cross-provider contract; document this as an adapter constraint, not as a LiteLLM limitation.

### 3.3 Discovery endpoint and authorization — important correction

The `GET /v1/search/tools` handler in `endpoints.py`:
- authenticates the request through `user_api_key_auth`;
- reads `llm_router.settings().search_tools`;
- returns each configured tool's `search_tool_name` and `search_provider`;
- may add `description` if configured;
- does not call `_filter_visible_search_tools` or `can_token_call_search_tool`.

A separate management route in `search_tool_management.py` has a different behavior and includes grant filtering, but it is not the `/v1/search/tools` endpoint used by this adapter.

The Search execution handler in `endpoints.py` explicitly calls `can_token_call_search_tool` before routing the search. That function resolves key/team/user grants and calls `check_search_tool_grants`.

**Adapter requirement:** treat discovery as configuration inventory only. Do not assume every discovered tool is executable by the configured key. The adapter may initially register discovered tools and let LiteLLM enforce authorization at execution, returning a sanitized authorization error. If hiding unauthorized tools is required, the adapter needs a separately verified authorization-aware discovery strategy; do not infer permissions from the current discovery response.

### 3.4 Discovery response metadata

The source can include a `description` if `search_tool_info.description` exists, but the live response supplied for this environment did not include one. Tool registration must therefore have a safe fallback description.

The endpoint returns only a limited inventory, not a JSON Schema for tool arguments. Dynamic discovery cannot currently derive a provider-specific input schema from this endpoint alone.

### 3.5 Response contract

The v1.109.0 endpoint docstring describes a Perplexity-compatible response with:
- top-level `object`;
- top-level `results` array;
- result fields including `title`, `url`, `snippet`;
- optional date/update metadata in its example.

The two live provider responses confirm the `results` array and the three common fields. Provider-specific optional fields must be preserved where safe. Empty `results` is a valid search outcome and must not be treated as a transport error.

## 4. MCP Python SDK review

The official MCP Python SDK repository identifies Python 3.10+ as the runtime requirement. Its release notes identify v2.0.0 as the stable v2 line, with v1.x in maintenance mode. The release history currently surfaced v2.3.0 as a later v2 release.

Recommendation:
- use the official `mcp` Python SDK;
- select an exact tested v2 release and lock all dependencies;
- do not write implementation code against an assumed SDK method signature;
- first create a minimal isolated proof of concept for dynamic tool registration, tool-list-change notifications, and Streamable HTTP using the exact pinned SDK;
- run protocol tests against the chosen SDK before integrating LiteLLM calls.

The SDK's major v2 protocol support includes the 2026-07-28 revision and compatibility with earlier 2025-era clients. The actual initial clients (OpenHands and VS Code) still require version-specific connection and authentication tests.

## 5. Design decisions confirmed by this review

1. Keep the adapter standalone and independent from LiteLLM's MCP Gateway.
2. Continue to use `GET /v1/search/tools` for configuration discovery.
3. Continue to use `POST /v1/search/{search_tool_name}` for execution.
4. Keep the LiteLLM Virtual Key only in adapter-side secret configuration.
5. Start with a common input schema containing:
   - required non-empty `query` string;
   - optional `max_results` integer constrained to 1–20, with a deliberate adapter default of 5 or 10 to be selected before implementation.
6. Do not assume that max_results produces exactly that number of results.
7. Preserve the provider's returned result data and support a valid empty result list.
8. Treat discovery as an inventory, not an authorization-filtered list.
9. Rely on LiteLLM's execution-time Search Tool authorization; sanitize and map 401/403-style failures.
10. Keep dynamic registry refresh last-known-good behavior: a failed discovery call must not erase working registered tools.

## 6. Remaining verification gates

The following work remains before implementation:

- Verify actual behavior when the Virtual Key is denied access to one configured Search Tool. This should be done only with an intentionally restricted disposable key or a controlled permission change; do not weaken the existing test key or production permissions just for this test.
- Verify malformed arguments and invalid max_results behavior, ideally against a disposable test setup.
- Verify empty-result handling if a safe no-result query can be constructed.
- Inspect the exact SDK v2.3.0 dynamic registration API and build a protocol-only proof of concept.
- Confirm exact OpenHands and VS Code versions and their HTTP header/auth configuration support.
- Finalize adapter host, network exposure, TLS, and client authentication.
- Confirm whether the test Virtual Key has been revoked after test completion; it was disclosed in chat and must not be used for production.

## 7. Sources

### LiteLLM exact tag v1.109.0
- Search endpoints source: https://github.com/BerriAI/litellm/blob/v1.109.0/litellm/proxy/search_endpoints/endpoints.py
- Search authorization source: https://github.com/BerriAI/litellm/blob/v1.109.0/litellm/proxy/auth/auth_checks.py
- Search types: https://github.com/BerriAI/litellm/blob/v1.109.0/litellm/types/search.py
- Search Tools admin/API docs: https://docs.litellm.ai/docs/proxy/ui_search_tools

### MCP SDK
- Official SDK repository: https://github.com/modelcontextprotocol/python-sdk
- Official SDK releases: https://github.com/modelcontextprotocol/python-sdk/releases

---

**Review outcome:** the architecture is feasible and the core LiteLLM API path is confirmed against both live tests and the exact v1.109.0 source. The discovery/authorization distinction above is a required security clarification. The initial implementation is now committed. Remaining release gates are CI on the locked build (now passed), protocol-level client tests, private LiteLLM integration, and actual OpenHands/VS Code/Linux deployment.


## 8. Follow-up corrections

The exact MCP SDK v2.3.0 package metadata confirms `Requires-Python: >=3.10`; the container uses Python 3.12 as a project runtime choice. Current OpenHands setup guidance should target Agent Canvas/local-backend `Customize > MCP Servers` with SHTTP and Bearer authentication; the older CLI MCP guide states that the CLI is no longer actively maintained. Current VS Code Copilot HTTP headers should use environment-variable references rather than assuming input prompts or SecretStorage interpolation.


## 9. Initial implementation follow-up

The initial adapter source now avoids retrying Search POST requests after ambiguous network or 5xx failures, because LiteLLM/provider execution may already have occurred and could be billed. HTTP 429 may be retried with a bounded delay; discovery GET requests may retry transient failures. The adapter also enforces a response-size cap and a global per-process MCP request rate limit. These source changes still require successful CI and private end-to-end verification.
