# MCP SDK and Client Compatibility Review

**Review date:** 2026-11-17  
**Repository:** `bavinashP/LiteLLM-Search-MCP-Adapter`  
**Review scope:** Official MCP Python SDK v2.3.0, Streamable HTTP, dynamic tool registry, OpenHands native installation, and Visual Studio Code  
**Implementation status:** Initial implementation committed — CI, integration tests, and deployment verification pending

---

## 1. Executive conclusion

The standalone LiteLLM Search MCP Adapter remains technically feasible.

The review confirms that the initial client targets can use a remote Streamable HTTP MCP endpoint with a Bearer credential:

- **OpenHands:** its current configuration model supports Streamable HTTP through `shttp_servers`. The OpenHands SDK maps the configured `api_key` to `Authorization: Bearer <value>`.
- **Visual Studio Code:** its MCP configuration supports `type: "http"`, a remote MCP URL, and custom HTTP headers. The Authorization header can carry a Bearer token.
- **MCP Python SDK:** v2.3.0 provides the required dynamic server tool-management APIs and Streamable HTTP application support.

However, two implementation details must be handled explicitly:

1. Calling SDK `add_tool()` or `remove_tool()` changes the server's live registry, but does **not** automatically notify connected clients.
2. The 2026-07-28 MCP protocol delivers list-change notifications through an opt-in `subscriptions/listen` stream. Older 2025-era clients use the legacy notification behavior. The server must test both paths instead of assuming one notification method works for every client.

**Recommendation:** pin the implementation to `mcp==2.3.0`, use a single `/mcp` Streamable HTTP endpoint, and begin with a server-managed Bearer token over a private network/VPN with HTTPS. Do not implement OAuth in the first release unless deployment requirements later demand it.

---

## 2. Official MCP Python SDK

### 2.1 Version and runtime

The official SDK release history identifies v2.3.0 as a stable release. The SDK v2 documentation requires Python 3.11 or newer. SDK v2 is the current stable line; v1.x is in maintenance mode.

**Implementation baseline:**

| Item | Decision |
|---|---|
| Python | 3.11+ |
| MCP package | `mcp==2.3.0` |
| Server class | `MCPServer` |
| HTTP transport | Streamable HTTP |
| MCP endpoint | `/mcp` |
| Dependency policy | Exact direct dependency pin plus committed lock file |
| v1 SDK APIs | Do not use |
| v2 migration guide | Mandatory reference during implementation |

The v2 API renamed `FastMCP` to `MCPServer`; old v1 imports and examples must not be copied into the implementation.

### 2.2 Dynamic tool registration API — source verified at v2.3.0

The exact v2.3.0 source exposes these public methods on `MCPServer`:

- `add_tool(fn, name=..., title=..., description=..., ...)`
- `remove_tool(name)`

The server's tool manager updates its in-memory registry. A subsequent `tools/list` reads the current registry, so the registry itself can be changed while the server is running.

**Important limitation:** the v2.3.0 implementations of `add_tool()` and `remove_tool()` do not automatically emit a tools-list-changed notification. The adapter must explicitly publish the correct notification after a real registry change.

Implementation requirements:

1. Use deterministic MCP tool names and retain the original LiteLLM Search Tool name separately.
2. Add only tools accepted by the adapter's exposure policy.
3. Update/remove registry entries only after a fully validated successful discovery response.
4. Do not clear the live registry when discovery fails or returns malformed data.
5. Publish a notification only after an actual add/remove/change operation.
6. Make refresh and registry mutation concurrency-safe.
7. Detect normalized-name collisions before changing the live registry.

### 2.3 Notifications across protocol revisions

This is a critical compatibility point.

**2025-era clients:**
- The SDK migration documentation describes the legacy `ctx.session.send_tool_list_changed()` behavior.
- A legacy client receiving the notification should request `tools/list` again.
- A refresh worker running outside a tool request does not naturally have a request-scoped Context. The implementation must deliberately manage the active legacy connection/session notification path or document a reconnect fallback.

**2026-07-28 clients:**
- Change notifications are delivered through `subscriptions/listen` streams.
- A client must opt in with `toolsListChanged: true`.
- The SDK migration guide directs server code to publish through `await ctx.notify_tools_changed()` or the server's subscription bus.
- A bare legacy session notification is not a substitute for the 2026 subscription path.

**Required proof-of-concept tests:**
- A legacy 2025-era MCP client receives the notification and refreshes its tool list.
- A 2026-07-28 client subscribes, receives the acknowledgement, receives a tools-changed notification, and refreshes its tool list.
- A client that does not subscribe still sees the current registry whenever it requests `tools/list`.
- If a client ignores notifications, reconnect/manual refresh behavior is documented.

Do not mark dynamic client-side refresh as complete merely because the server's internal `tools/list` result changes.

### 2.4 Streamable HTTP application

The exact v2.3.0 package metadata declares `Requires-Python: >=3.10` (not >=3.11). The v2.3.0 server exposes `streamable_http_app()` and `run_streamable_http_async()`. The application method supports:

- configurable MCP path (default `/mcp`);
- `stateless_http`;
- request body size limit;
- legacy session idle timeout and maximum sessions;
- transport security settings;
- authentication settings/token verification.

**Recommended compatibility setting:** initially use `stateless_http=False` (the SDK default) so the same server can be tested with legacy 2025-era session clients as well as 2026-era clients. Do not enable stateless mode until the exact OpenHands and VS Code versions have passed compatibility tests.

The 2026-07-28 protocol itself no longer uses protocol-level sessions; SDK v2 is designed to serve both the 2026 revision and earlier clients from the same Streamable HTTP server.

### 2.5 Health endpoints and authentication caution

The SDK's `MCPServer.custom_route()` documentation explicitly states that custom routes are not protected by the MCP authorization middleware. Therefore:

- Do not put secrets, configuration values, key metadata, or internal diagnostics in a custom health route.
- Liveness/readiness responses must contain only minimal status information.
- If an administrative or sensitive endpoint is ever added, protect it separately; do not assume the MCP route's authentication protects it.

---

## 3. OpenHands native installation compatibility

### 3.1 Transport support

Current OpenHands documentation describes Streamable HTTP (SHTTP) as the recommended remote transport. For Agent Canvas/local backend, configure the server through `Customize > MCP Servers`; the UI supports a Bearer token and sends `Authorization: Bearer <token>`. The current general MCP documentation lists Agent Canvas, SDK, and OpenHands Cloud as supported surfaces. The previously referenced `openhands mcp` CLI documentation explicitly says that CLI is no longer actively maintained, so it must not be presented as the preferred setup path for a current installation.

A remote adapter can therefore be configured as an SHTTP server. OpenHands does not need to run in the adapter container; it only needs network access to the adapter endpoint.

### 3.2 Bearer authentication mapping — source verified

The OpenHands software-agent SDK source contains the helper `_mcp_api_key_headers()`. It maps the SHTTP server's configured `api_key` value to:

```http
Authorization: Bearer <configured-api-key>
```

This confirms that a server-managed Bearer token is compatible with the documented OpenHands SHTTP `api_key` configuration path.

The token belongs in OpenHands' encrypted MCP settings/secret handling, not in a conversation, source-controlled example, or shared repository file.

### 3.3 Configuration shape

The documented UI configuration is the preferred path for a current Agent Canvas/local-backend installation. The legacy SDK/CLI configuration shape is not the default recommendation. The configuration shape for the current UI is:

```toml
[mcp]
shttp_servers = [
  { url = "https://<adapter-host>/mcp", api_key = "<adapter-bearer-token>" }
]
```

This is a shape example only. Do not copy the placeholder literally, and do not commit a populated token.

### 3.4 Version qualification

The project owner's exact installed OpenHands version has not yet been recorded. Documentation and current SDK source establish feature support, but they do not prove that the user's installed build has the same configuration schema.

**Acceptance gate:** record the installed OpenHands version and verify the actual native installation can connect, list tools, call a search tool, and refresh the tool list after a registry change.

---

## 4. Visual Studio Code compatibility

### 4.1 Transport and header support

Current official VS Code MCP configuration documentation supports:

- `type: "http"` for Streamable HTTP;
- `url` for the remote MCP endpoint;
- `headers` for custom HTTP headers.

A Bearer token can be supplied through the Authorization header. For the current Copilot runtime, use an environment-variable reference such as `${MCP_ADAPTER_TOKEN}` in the header value. Do not assume `${input:...}` prompting or a bridge to VS Code SecretStorage is available to the Copilot HTTP header path. VS Code's documentation warns against storing literal credentials in shared configuration files.

### 4.2 Configuration shape

```json
{
  "servers": {
    "litellm-search": {
      "type": "http",
      "url": "https://<adapter-host>/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_ADAPTER_TOKEN}"
      }
    }
  }
}
```

This is a configuration-shape example only. Do not commit a populated token. The exact configuration destination (user profile versus workspace) must be chosen so that credentials are not shared accidentally.

### 4.3 Compatibility qualification

VS Code's HTTP transport and custom-header support are confirmed in current official documentation. The exact VS Code build, selected Copilot harness, and authentication UX used by the project owner have not been tested against the adapter because the adapter does not exist yet.

**Acceptance gate:** record the VS Code version and selected harness, configure the remote server, confirm tool discovery and execution, then test tool-list refresh behavior.

---

## 5. Recommended first-release authentication and network profile

Both initial clients have a documented path for a static Bearer credential. OAuth is therefore not required to prove the first release.

**Recommended first-release profile:**

1. Adapter accepts one dedicated Bearer credential for MCP client access. The token must be at least 32 characters and must be different from the LiteLLM Virtual Key.
2. Adapter stores a separate LiteLLM Virtual Key server-side.
3. The MCP Bearer credential and LiteLLM Virtual Key are different secrets with different purposes.
4. The adapter never forwards the client Bearer token to LiteLLM.
5. The adapter never returns the LiteLLM Virtual Key to an MCP client.
6. Use HTTPS for traffic between different hosts.
7. Restrict access to a private LAN/VPN initially; do not expose the endpoint publicly by default.
8. Keep tokens out of logs, Git, Docker image layers, shell history, and screenshots.
9. Support token rotation without rebuilding the container.
10. Rate-limit and bound concurrent calls per authenticated client.

OAuth 2.1 can be considered later if the deployment needs centrally managed user identities, per-user authorization, or third-party access at internet scale. A static Bearer check must not be described as OAuth or full MCP OAuth authorization.

---

## 6. Critical LiteLLM discovery security consequence

The previous LiteLLM v1.109.0 source review established that `GET /v1/search/tools` returns configured Search Tool inventory and does not apply the same Search Tool grant filtering that the POST execution endpoint enforces.

That means a dedicated LiteLLM Virtual Key's Search Tool restrictions alone do not guarantee that the adapter's discovery response contains only authorized tools.

**Additional adapter-side control is required:**

- Add an explicit server-side exposure allowlist, e.g. `LITELLM_ALLOWED_SEARCH_TOOLS`.
- Default to deny when the allowlist is absent or empty.
- Intersect the successfully discovered LiteLLM inventory with this allowlist before registering MCP tools.
- Never accept the allowlist from an MCP client request.
- Keep the LiteLLM Virtual Key's own Team/Key Search Tool grants restricted to the same intended tool set.
- If a listed tool is not authorized at LiteLLM execution time, return a sanitized authorization error and log a safe operator diagnostic.

This preserves dynamic discovery while ensuring that the adapter does not automatically expose every configured LiteLLM Search Tool to all MCP clients.

A new LiteLLM Search Tool should become available only after it is both discovered and explicitly approved in the adapter's exposure allowlist. This is a deliberate security boundary, not a failure of dynamic discovery.

---

## 7. Recommended implementation defaults

The following are engineering defaults recommended for the first implementation. They do not require a new provider API key to be generated now.

| Setting | Recommended initial value | Reason |
|---|---|---|
| MCP SDK | `mcp==2.3.0` | Exact reviewed stable release |
| Python | `3.11+` | SDK runtime requirement |
| MCP path | `/mcp` | Agreed endpoint |
| Transport | Streamable HTTP | Agreed primary transport |
| `stateless_http` | `false` initially | Preserve legacy-client compatibility testing |
| Discovery refresh | 30 seconds, configurable | Responsive without polling on every call |
| `max_results` default | 5 | Conservative result/token volume |
| `max_results` range | 1–20 | LiteLLM v1.109.0 documented range |
| MCP client authentication | Static Bearer token | Supported by both initial client paths |
| Network exposure | Private LAN/VPN; HTTPS between hosts | Minimize attack surface |
| Tool exposure | Explicit adapter allowlist | Discovery endpoint is not permission-filtered |
| LiteLLM identity | Dedicated, least-privilege Virtual Key | Separate service identity and policy |
| Discovery failure | Retain last-known-good registry | Avoid accidental tool outage |
| Retry policy | Bounded; honor `Retry-After` for 429 | Avoid retry storms |
| Container user | Non-root | Reduce container impact |

The `max_results=5` default is an adapter policy recommendation, not a LiteLLM default. LiteLLM documents a default of 10 when the field is omitted.

---

## 8. Remaining environment facts and acceptance gates

The following values cannot be truthfully confirmed from public documentation and have not yet been tested in the user's private environment:

1. Exact OpenHands distribution/surface and version installed on the native Linux server (current Agent Canvas/local backend versus legacy CLI/SDK).
2. Exact VS Code version and active Copilot harness.
3. Adapter container host and final DNS name.
4. Whether adapter-to-LiteLLM traffic can use HTTPS, or whether the private LAN path must temporarily use HTTP with strict network isolation.
5. Whether the new production LiteLLM Virtual Key will be configured with the same intended Search Tool grants as the adapter allowlist.
6. Whether both actual clients refresh tools automatically on registry changes.
7. Whether a legacy client and a 2026-era client both pass the same server compatibility suite.

These are deployment/test facts, not blockers to completing the design review. No implementation or deployment has been claimed.

---

## 9. Required test plan before release

### SDK and protocol
- [ ] Install exactly `mcp==2.3.0` in a clean Python 3.10+ environment.
- [ ] Start a minimal `MCPServer` using Streamable HTTP at `/mcp`.
- [ ] Verify authenticated `tools/list` and `tools/call`.
- [ ] Add and remove tools at runtime; verify the next `tools/list` reflects the change.
- [ ] Verify 2025-era notification delivery and client refresh.
- [ ] Verify 2026-07-28 `subscriptions/listen` acknowledgement and tools-changed delivery.
- [ ] Verify a non-subscribing client can still obtain the current list when it asks.
- [ ] Verify invalid Origin/Host handling and request-size limits.
- [ ] Verify graceful shutdown and multiple concurrent clients.

### OpenHands
- [ ] Record installed OpenHands version.
- [ ] Configure SHTTP URL and Bearer `api_key`.
- [ ] Confirm server connection and tool listing.
- [ ] Execute both LiteLLM-backed search tools.
- [ ] Verify dynamic tool refresh or document reconnect fallback.

### Visual Studio Code
- [ ] Record VS Code version and selected Copilot harness.
- [ ] Configure HTTP MCP server and Authorization header without committing credentials.
- [ ] Confirm server connection and tool listing.
- [ ] Execute both LiteLLM-backed search tools.
- [ ] Verify dynamic tool refresh or document reconnect fallback.

### Security and operations
- [ ] Confirm the adapter allowlist is enforced.
- [ ] Confirm LiteLLM key is never exposed to MCP clients.
- [ ] Confirm 401/403, 429, 5xx, timeout, malformed discovery, and malformed search responses are handled safely.
- [ ] Confirm secrets are absent from logs, Git, image layers, and health output.
- [ ] Confirm non-root container, health checks, restart behavior, and resource limits.

---

## 10. Source references

### Official MCP Python SDK
- SDK repository: https://github.com/modelcontextprotocol/python-sdk
- v2.3.0 release: https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0
- v2.3.0 ToolManager source: https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/tools/tool_manager.py
- v2.3.0 MCPServer source: https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py
- v2 migration guide: https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md
- v2 What's New: https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md

### Official MCP specification
- Streamable HTTP, revision 2026-07-28: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- Tools, revision 2026-07-28: https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- Subscriptions, revision 2026-07-28: https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/subscriptions

### OpenHands
- MCP usage documentation: https://github.com/OpenHands/docs/blob/main/openhands/usage/mcp.mdx
- OpenHands MCP command reference: https://github.com/OpenHands/docs/blob/main/openhands/usage/cli/command-reference.mdx
- OpenHands software-agent SDK source (Bearer mapping): https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-workspace/openhands/workspace/cloud/workspace.py

### Visual Studio Code
- MCP configuration reference: https://code.visualstudio.com/docs/agents/reference/mcp-configuration
- Add and use MCP servers: https://code.visualstudio.com/docs/agent-customization/mcp-servers
- Manage and troubleshoot MCP servers: https://code.visualstudio.com/docs/agents/run/manage-mcp-servers

---

## 11. Review outcome

**Architecture:** viable.  
**SDK baseline:** selected as `mcp==2.3.0`, subject to clean-environment proof-of-concept tests.  
**OpenHands transport/auth path:** documented and source-verified for Streamable HTTP plus Bearer `api_key`.  
**VS Code transport/auth path:** documented for Streamable HTTP plus custom Authorization header.  
**Dynamic registry:** supported by SDK APIs; notification delivery requires explicit implementation for both protocol eras.  
**Security:** adapter-side Search Tool exposure allowlist is mandatory because LiteLLM's discovery endpoint is not permission-filtered.  
**Implementation:** not started. No project code, deployment files, containers, or runtime settings were changed in this review.



## 12. Documentation corrections from current official sources

- The exact v2.3.0 SDK `pyproject.toml` declares Python `>=3.10`; Python 3.11 is not a minimum requirement. The adapter container uses Python 3.12 as its chosen runtime.
- Current OpenHands documentation recommends configuring remote SHTTP servers through Agent Canvas `Customize > MCP Servers` (or the supported SDK integration). The legacy `openhands mcp` CLI documentation says the CLI is no longer actively maintained.
- Current VS Code Copilot configuration supports environment-variable references in HTTP header values. Its documentation explicitly cautions that input prompts and VS Code SecretStorage are not generally available as a bridge for Copilot HTTP header interpolation. Use a user-level configuration and an environment variable supplied to the VS Code/Copilot host process.
- The adapter's first implementation uses a dedicated static Bearer token, enforces a minimum length of 32 characters, and rejects using the same value as the LiteLLM Virtual Key. This is a private deployment token profile, not OAuth 2.1.


## 12. Final client configuration corrections

- Current OpenHands documentation recommends Agent Canvas `Customize > MCP Servers` for local-backend SHTTP configuration and states that MCP server changes apply to new conversations. The old `openhands mcp` CLI documentation says that CLI is no longer actively maintained.
- Current VS Code Copilot documentation supports environment-variable references in HTTP header values but does not provide a general input-prompt/SecretStorage interpolation bridge for that header path.
- SDK v2.3.0 metadata confirms Python `>=3.10`; Python 3.12 is the selected container runtime, not the SDK minimum.


## 13. Implementation follow-up

The first implementation now uses a single shared static Bearer token, a 32-character minimum, constant-time comparison, and a global per-process rate limit (default 120 HTTP requests/minute). Because all clients initially share one token, this is not per-client/per-user throttling. The LiteLLM HTTP client avoids retrying Search POST requests after ambiguous transport failures or 5xx responses to reduce the risk of duplicate paid searches. CI and actual client verification remain pending.
