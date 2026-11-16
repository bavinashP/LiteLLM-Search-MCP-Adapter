# Dynamic LiteLLM Search MCP Adapter — Technical Design

**Status:** Design baseline — implementation not started  
**Date:** 2026-11-17  
**Target repository:** `bavinashP/LiteLLM-Search-MCP-Adapter`  
**Target LiteLLM version:** `v1.109.0`

---

## 1. Executive Summary

This project will provide a standalone, reusable MCP server that exposes the Search Tools configured in LiteLLM as MCP tools. It is not an OpenHands-specific extension and it will not be registered inside LiteLLM's own `/mcp` gateway.

The MCP server will run as a small, independent Linux container and expose Streamable HTTP. Any MCP client that supports this transport and the selected authentication method can connect to it. OpenHands and Visual Studio Code are initial compatibility targets, not hard-coded dependencies.

The adapter will discover tools through LiteLLM's Search Tools listing endpoint and execute calls through the corresponding Search API endpoint. It will not call search-provider APIs directly and will not store provider credentials.

**Core design principle:** LiteLLM remains the system of record for Search Tool configuration, provider credentials, and Search Tool access policy. The adapter is a protocol bridge only.

## 2. Confirmed Scope and Decisions

| Item | Decision |
|---|---|
| Product | Standalone Dynamic LiteLLM Search MCP Adapter |
| LiteLLM version in current environment | v1.109.0 |
| LiteLLM base URL currently reported | `http://192.168.1.233:4000` |
| Existing Search Tools reported | `spp_Parallel`, `openhands_tavily` |
| Discovery API | `GET /v1/search/tools` |
| Search API | `POST /v1/search/{search_tool_name}` |
| MCP deployment | Independent Linux container |
| Primary MCP transport | Streamable HTTP |
| MCP endpoint proposed | `/mcp` |
| LiteLLM MCP Gateway | Explicitly not used |
| Initial clients | OpenHands (native Linux installation), VS Code |
| Future clients | Any client compatible with the selected MCP transport and authentication profile |
| Implementation status | Not started; this document is design only |

The LiteLLM base URL and current Search Tool names above are environment details supplied by the project owner. They have not been independently queried from the private server during this design pass.

## 3. Verified LiteLLM Search API Contract

### 3.1 Tool discovery

- Method: `GET`
- Path: `/v1/search/tools`
- Authentication: `Authorization: Bearer <LITELLM_VIRTUAL_KEY>`
- Purpose: list Search Tools configured in LiteLLM.

The project owner's successful response for the current key was:

```json
{
  "object": "list",
  "data": [
    {
      "search_tool_name": "spp_Parallel",
      "search_provider": "parallel_ai"
    },
    {
      "search_tool_name": "openhands_tavily",
      "search_provider": "tavily"
    }
  ]
}
```

This observed response exposes the tool name and provider identifier. It does not expose a provider-specific JSON input schema or provider credentials. The adapter must not assume that this listing endpoint provides either.

### 3.2 Search execution

- Method: `POST`
- Path: `/v1/search/{search_tool_name}`
- Headers:
  - `Authorization: Bearer <LITELLM_VIRTUAL_KEY>`
  - `Content-Type: application/json`
- Documented common request fields include `query` and `max_results`.

Example request body:

```json
{
  "query": "Latest Laravel 12 features",
  "max_results": 5
}
```

The exact response structure can vary by provider and LiteLLM implementation. The adapter should preserve useful returned content and source metadata without assuming every provider returns identical optional fields.

### 3.3 LiteLLM permissions

LiteLLM Search Tools have their own Team allowlist and optional Virtual Key-level restrictions. A Virtual Key's Search Tool allowlist must remain within its Team's allowlist when the Team list is non-empty.

The adapter must use a dedicated, least-privilege LiteLLM Virtual Key whose access is limited to the Search Tools intended for this adapter. It must not use a LiteLLM master key.

Because discovery is not permission-filtered, the adapter must also maintain an explicit server-side Search Tool exposure allowlist (for example, `LITELLM_ALLOWED_SEARCH_TOOLS`). If the allowlist is missing or empty, expose no Search Tools. Register only the intersection of the latest valid discovery inventory and this allowlist. Never accept this allowlist from an MCP client.

**Verified security finding:** the exact LiteLLM v1.109.0 `GET /v1/search/tools` handler returns configured Search Tool inventory and does not apply the Search Tool grant filter. The `POST /v1/search/{search_tool_name}` execution handler separately enforces Search Tool authorization. Discovery must never be treated as proof of execution permission. The adapter must apply its own explicit server-side exposure allowlist (default deny) before registering discovered tools, and the dedicated LiteLLM Virtual Key must be restricted to the same intended tool set.

## 4. Proposed Architecture

```text
MCP Clients
  |-- OpenHands (native installation)
  |-- VS Code
  |-- Other compatible MCP clients
  |
  | Streamable HTTP / MCP
  v
Standalone LiteLLM Search MCP Adapter
  |-- MCP transport and protocol handling
  |-- Client authentication
  |-- Dynamic Search Tool registry
  |-- Input validation and tool-name mapping
  |-- LiteLLM API client
  |-- Timeouts, bounded retries, structured logs
  |
  | HTTPS/HTTP + LiteLLM Virtual Key
  v
LiteLLM v1.109.0
  |-- GET  /v1/search/tools
  |-- POST /v1/search/{search_tool_name}
  |
  +-- Parallel, Tavily, and future configured Search Tools
```

The adapter must not:
- register itself with LiteLLM's MCP Gateway;
- call Parallel, Tavily, or other provider endpoints directly;
- contain provider API keys;
- expose LiteLLM's Virtual Key to MCP clients;
- accept arbitrary URLs, arbitrary HTTP methods, or caller-supplied LiteLLM paths.

## 5. Framework and Runtime Recommendation

### 5.1 Recommended implementation baseline

Use Python and the official Model Context Protocol Python SDK, with Streamable HTTP as the deployed transport.

The official SDK v2.3.0 release and tagged source have now been reviewed. Implementation baseline: Python 3.10+ and exact dependency pin `mcp==2.3.0`, with a committed dependency lock file. Use `MCPServer` (not the v1 `FastMCP` import path). The exact v2.3.0 APIs `add_tool()` and `remove_tool()` are available. These methods update the registry but do not automatically notify connected clients; notification publication must be implemented explicitly for both 2025-era and 2026-07-28 clients.

Use an asynchronous HTTP client for LiteLLM requests. Keep the LiteLLM API client isolated from MCP protocol code so that it can be unit-tested independently.

### 5.2 Why this is the preferred option

- Official MCP SDK rather than a hand-written JSON-RPC implementation.
- Streamable HTTP is intended for deployed/network-accessible servers.
- Dynamic tool registration and list-change notifications are documented.
- Python is suitable for a small Linux container and straightforward HTTP integration.

Use `MCPServer.streamable_http_app(streamable_http_path="/mcp", stateless_http=False)` initially to preserve the ability to test legacy 2025-era clients alongside 2026-07-28 clients. The SDK v2.3.0 supports both protocol eras from the same server. OpenHands SHTTP configuration maps `api_key` to an Authorization Bearer header; VS Code supports `type: "http"` and custom HTTP headers. Exact installed client versions and dynamic-notification behavior still require integration tests before compatibility can be declared.

## 6. Dynamic Discovery and Tool Registry

### 6.1 Discovery lifecycle

1. On startup, call LiteLLM `GET /v1/search/tools`.
2. Validate the response shape and reject malformed entries.
3. For each valid entry, create a deterministic MCP tool identifier.
4. Register or update the MCP tool in the server registry.
5. Remove tools that disappeared from the latest successful discovery response.
6. Publish the MCP tool-list-changed notification after a real registry change.
7. Refresh periodically using a configurable interval.
8. If refresh fails, retain the last-known-good registry, mark discovery as degraded in health/diagnostics, and do not erase working tools.

A failed or malformed discovery response must never be interpreted as an authoritative empty list.

### 6.2 Naming

Use a stable namespace prefix, for example:

`litellm_search__spp_parallel`

The original LiteLLM `search_tool_name` must be stored separately and used unchanged in the LiteLLM URL path after safe URL path-segment encoding. MCP identifiers must be deterministic and collision-checked. If two original names normalize to the same identifier, fail the conflicting registration visibly rather than silently routing to the wrong provider.

### 6.3 Tool descriptions and input schema

The discovery response currently observed contains provider and tool names, but no description or JSON Schema. Therefore:

- Generate a neutral description such as “Search the web using the LiteLLM Search Tool '<name>' (provider: <provider>).”
- Start with a documented common input contract:
  - `query`: required non-empty string.
  - `max_results`: optional bounded positive integer; use a conservative default only after validating the LiteLLM v1.109.0 behavior.
- Reject unknown fields in the first implementation unless the exact LiteLLM/provider schema is verified.
- Do not invent provider-specific parameters.
- Keep schema construction isolated so verified provider-specific options can be added later without changing the discovery mechanism.

### 6.4 Refresh and client behavior

The server should advertise tool-list change support and emit the protocol notification when the registry changes. Clients that do not handle the notification may require a manual refresh or reconnect; this limitation must be documented and tested.

Use a configurable refresh interval and a deterministic ordering of tools. Do not perform an unbounded LiteLLM discovery request on every individual tool call.

## 7. Authentication and Security

There are two independent trust boundaries:

### 7.1 MCP client to adapter

The adapter must authenticate callers before serving tools or executing searches. The production deployment must use HTTPS whenever traffic crosses an untrusted network.

For broad client compatibility, clients commonly support an HTTP Authorization header; VS Code's official MCP configuration supports custom HTTP headers, and OpenHands documents MCP server configuration. However, support for a particular authentication flow varies by client and version.

**First-release decision:** use a deployment-managed static Bearer credential for the private-network/VPN deployment. Enforce a minimum 32-character token, require it to differ from the LiteLLM Virtual Key, and compare it in constant time. This is not OAuth 2.1 and must not be represented as OAuth or full MCP OAuth authorization compliance. Consider OAuth 2.1 only if centrally managed identities or internet-scale third-party access become a real requirement.

### 7.2 Adapter to LiteLLM

- Store the dedicated LiteLLM Virtual Key only in server-side secret configuration.
- Send it only to the configured LiteLLM base URL.
- Never return it in tool output, exceptions, health responses, or logs.
- Never accept a LiteLLM key from an MCP tool argument.
- Do not expose the key to OpenHands, VS Code, or other clients.
- Prefer HTTPS between adapter and LiteLLM when the network path is not fully trusted. If private-network HTTP is temporarily used, document the risk and restrict network access.

### 7.3 Network and request hardening

- Configure a single trusted LiteLLM base URL at deployment time.
- Do not allow tool callers to choose the host, URL, port, path, or provider endpoint.
- Validate tool names against the current registry before constructing a request.
- Apply request-size limits, query-length limits, bounded result counts, connection/read timeouts, and concurrency limits.
- Do not follow redirects to arbitrary hosts while sending the LiteLLM credential.
- Do not log query contents by default unless explicitly enabled with privacy controls.
- Avoid returning raw internal exception details to MCP clients.
- Run the container as a non-root user, use a read-only filesystem where practical, drop unnecessary Linux capabilities, and pin the image/dependencies.

## 8. Error Handling and Reliability

| Condition | Required behavior |
|---|---|
| LiteLLM discovery unavailable at startup | Fail readiness; report a safe diagnostic; retry with bounded backoff |
| Discovery fails after successful startup | Retain last-known-good registry and mark degraded |
| Malformed discovery response | Reject update; retain previous registry |
| Unknown or removed MCP tool | Return a clear tool-not-found error; do not call LiteLLM |
| LiteLLM 401/403 | Return sanitized authorization error; do not retry blindly |
| LiteLLM 429 | Respect Retry-After when present; bounded retry only when safe |
| LiteLLM 5xx/network timeout | Bounded retry with jitter for eligible requests; return sanitized failure |
| Provider returns no results | Return a valid empty result, not a transport failure |
| Invalid arguments | Reject before making an HTTP request |
| Duplicate normalized tool name | Reject conflicting registration and emit an operator-visible diagnostic |

Search calls are POST requests. Retry behavior must be conservative and verified against LiteLLM semantics; never create an unbounded retry loop.

## 9. Container and Deployment Design

### 9.1 Container

- Small, maintained Python Linux base image.
- Python 3.11 or newer, matching the pinned MCP SDK requirement.
- Multi-stage build where useful.
- Non-root runtime user.
- No secrets baked into the image or committed to Git.
- Health and readiness checks.
- Graceful shutdown and bounded request draining.
- Explicit CPU/memory limits and restart policy in deployment configuration.

### 9.2 Network placement

The adapter may run on the same Linux host as OpenHands or on another host. It is not coupled to OpenHands' native installation.

The container must be able to reach the LiteLLM host at `192.168.1.233:4000` (or the configured replacement address). The MCP clients must be able to reach the adapter's published HTTPS endpoint. Container networking, firewall rules, DNS, and TLS termination must be tested in the actual deployment environment.

Do not assume that a container's `localhost` refers to the LiteLLM host.

### 9.3 Operational endpoints

Proposed:
- MCP: `/mcp`
- Liveness: `/health/live`
- Readiness: `/health/ready`

Health responses must not disclose secrets, internal stack traces, or full configuration values. Readiness should reflect whether the adapter has a usable discovery state and can serve the registry.

## 10. Observability

Use structured logs with:
- timestamp, severity, component;
- request/correlation identifier;
- MCP tool identifier;
- LiteLLM HTTP status and duration;
- discovery refresh result and tool-count changes.

Do not log Authorization headers, API keys, provider credentials, or unrestricted raw search payloads. Provide counters/timers for discovery success/failure, search success/failure, latency, rate limits, and active requests. Metrics exposure should be optional and protected.

## 11. Testing and Acceptance Criteria

### 11.1 Unit tests

- Parse valid and malformed discovery responses.
- Generate deterministic tool identifiers and detect collisions.
- Validate required and optional arguments.
- Build the correct LiteLLM path without accepting arbitrary paths.
- Redact secrets from logs and errors.
- Apply bounded timeout/retry behavior.
- Preserve last-known-good registry after failed refresh.

### 11.2 Contract/integration tests

Against a controlled LiteLLM test instance or an explicitly approved test environment:
- authenticate to `GET /v1/search/tools`;
- discover the two existing tools;
- call each Search API with a harmless query;
- verify response normalization/preservation;
- verify 401, 403, 429, 5xx, timeout, malformed JSON, and empty-result handling;
- verify that a disallowed Search Tool cannot be executed;
- add a new Search Tool and verify it appears without rebuilding/restarting the adapter;
- remove a Search Tool and verify it disappears after refresh;
- verify a failed discovery refresh does not erase the registry.

### 11.3 MCP protocol tests

Use MCP Inspector and a protocol-level test client to verify:
- initialization and capability declaration;
- `tools/list`;
- `tools/call`;
- Streamable HTTP behavior;
- tool-list-changed notifications;
- invalid tool and invalid argument handling;
- authentication failure behavior;
- concurrent clients and graceful shutdown.

### 11.4 Client compatibility tests

Test the exact deployed versions/configurations of:
1. OpenHands native Linux installation.
2. VS Code MCP client.

Record client version, connection settings, authentication method, discovered tool names, successful calls, and known limitations. Only after these tests should the project claim compatibility with those clients. Other MCP clients are expected to work only if they support the server's MCP protocol version, Streamable HTTP, and authentication profile.

### 11.5 Release acceptance

A release is acceptable only when:
- all automated tests pass;
- no secret is present in source, image layers, logs, or Git history;
- dynamic add/remove behavior is demonstrated;
- LiteLLM permissions are enforced;
- both initial clients connect and execute a search;
- container health, restart, and shutdown behavior are verified;
- deployment and client setup documentation is complete.

## 12. Risks and Limitations

1. **Discovery metadata is limited:** the observed list has names and provider IDs, not schemas. A generic common input schema is required unless further API evidence establishes richer metadata.
2. **Authorization identity:** if all MCP clients share one adapter-side LiteLLM Virtual Key, LiteLLM sees the adapter's service identity, not each end user's identity. Per-user LiteLLM policy requires a deliberate identity/token mapping design.
3. **Tool-list notification support:** clients may cache tools or ignore change notifications. Reconnect/manual refresh may be required.
4. **Client authentication differences:** “any MCP client” cannot mean identical setup for every client. Transport and authentication compatibility must be qualified per client.
5. **Network reachability:** the container must reach the private LiteLLM address, and remote clients need a secure route to the adapter.
6. **Provider-specific options and result formats:** not all providers necessarily support identical parameters or response fields.
7. **Rate limits and cost:** multiple clients may share the same LiteLLM Virtual Key and therefore share its permissions, budgets, and rate limits.

## 13. Decisions Already Confirmed

- Build a reusable standalone MCP server, not an OpenHands-specific plugin.
- Run it in a small independent Linux container.
- Use Streamable HTTP as the primary transport.
- Do not integrate it into LiteLLM's `/mcp` gateway.
- Discover Search Tools dynamically from LiteLLM.
- Execute searches only through LiteLLM's Search API.
- Keep LiteLLM credentials server-side.
- Treat OpenHands and VS Code as initial client compatibility targets.

## 14. Decisions to Finalize Before Implementation

These are implementation gates, not reasons to stop the design work:

1. **Adapter host:** same Linux host as OpenHands, another server, or a dedicated container host.
2. **Public access model:** private LAN/VPN only, or internet-reachable HTTPS.
3. **Client authentication:** static deployment-managed Bearer token for a restricted network, or OAuth 2.1 for broader managed access.
4. **LiteLLM service identity:** one dedicated Virtual Key for the adapter, or separate keys/identity mapping for different client groups.
5. **Refresh interval:** choose and test a default (proposed initial range: 30–60 seconds) and make it configurable.
6. **Common input contract:** the initial adapter policy is `max_results` default 5 and range 1–20; confirm this with contract tests. Additional provider-specific fields remain disabled until verified.
7. **Initial compatibility versions:** record exact OpenHands and VS Code versions before client acceptance testing.

## 15. Official References

1. LiteLLM Search Tools Admin UI and API usage: https://docs.litellm.ai/docs/proxy/ui_search_tools
2. LiteLLM Proxy API Reference: https://docs.litellm.ai/api-reference
3. Official MCP Python SDK repository (v2 stable line, runtime requirements, transports): https://github.com/modelcontextprotocol/python-sdk
4. MCP Python SDK — running a server / Streamable HTTP: https://py.sdk.modelcontextprotocol.io/run/
5. MCP Python SDK — dynamic tool registration and notifications: https://py.sdk.modelcontextprotocol.io/handlers/context/
6. MCP Python SDK — authorization: https://py.sdk.modelcontextprotocol.io/run/authorization/
7. MCP Python SDK — deployment and Host allowlist: https://py.sdk.modelcontextprotocol.io/run/deploy/
8. OpenHands official MCP overview: https://github.com/OpenHands/docs/blob/main/overview/model-context-protocol.mdx
9. VS Code official MCP configuration reference: https://code.visualstudio.com/docs/agents/reference/mcp-configuration
10. VS Code official MCP server setup: https://code.visualstudio.com/docs/agent-customization/mcp-servers

---

## 16. Design Review Checklist

- [x] Standalone adapter architecture documented.
- [x] LiteLLM Search API endpoints and documented common request fields recorded.
- [x] Observed discovery payload distinguished from documented API behavior.
- [x] Dynamic tool registration and list-change behavior addressed.
- [x] Separate client-to-adapter and adapter-to-LiteLLM trust boundaries defined.
- [x] No direct provider API calls or arbitrary URL execution permitted.
- [x] Container, health, observability, error handling, and tests specified.
- [ ] Exact LiteLLM v1.109.0 behavior independently verified against the live server.
- [ ] Exact OpenHands and VS Code versions tested.
- [ ] Authentication profile and deployment host finalized.
- [ ] Implementation and automated test suite created.

**This document is a technical design baseline only. No application code, dependencies, container image, or server configuration have been created or changed.**


## 14. Implementation baseline corrections

The exact MCP SDK v2.3.0 `pyproject.toml` declares Python `>=3.10`; the adapter container deliberately uses Python 3.12. The first-release client configuration uses the current OpenHands Agent Canvas/local-backend MCP settings (SHTTP + Bearer) and VS Code HTTP MCP configuration with a process environment-variable reference for the Authorization header. The legacy OpenHands MCP CLI is not the recommended setup path because its documentation marks that CLI as no longer actively maintained.
