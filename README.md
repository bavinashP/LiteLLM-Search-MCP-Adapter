# LiteLLM Search MCP Adapter

A standalone Model Context Protocol (MCP) Streamable HTTP server that dynamically exposes explicitly approved LiteLLM Search Tools. It does not register with LiteLLM's MCP Gateway and never calls search providers directly.

## Status

The initial implementation has been committed, but **it is not yet certified for production**. The repository CI and real-client integration tests must pass before deployment. See [implementation and verification status](docs/IMPLEMENTATION_AND_VERIFICATION_STATUS.md).

## Architecture

```text
OpenHands / VS Code / MCP clients
          |
          | HTTPS + adapter Bearer token
          v
Standalone Adapter : /mcp
  - MCP Streamable HTTP (official Python SDK)
  - Bearer authentication
  - explicit default-deny Search Tool allowlist
  - dynamic discovery and registry refresh
  - bounded retries, timeouts, response limits
          |
          | LiteLLM Virtual Key (server-side only)
          v
LiteLLM /v1/search/tools
        /v1/search/{search_tool_name}
```

## Requirements

- Docker Engine and Docker Compose plugin.
- A LiteLLM Proxy exposing the Search API.
- A dedicated least-privilege LiteLLM Virtual Key.
- A separate high-entropy MCP client token.
- A private network or VPN and HTTPS termination for traffic crossing hosts.

The SDK v2.3.0 supports Python 3.10 and newer. The container image uses Python 3.12.

## Configuration

1. Clone this repository on the adapter host.
2. Copy the sample environment file and restrict its permissions:

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

3. Generate a dedicated adapter token locally:

   ```bash
   openssl rand -hex 32
   ```

4. Edit `.env` locally. Never paste secrets into chat, commit them, or put them in a Dockerfile.
5. Set:
   - `LITELLM_BASE_URL`: LiteLLM Proxy root URL, without a path/query/fragment.
   - `LITELLM_API_KEY`: dedicated LiteLLM Virtual Key, never the master key.
   - `MCP_BEARER_TOKEN`: the independently generated adapter token.
   - `LITELLM_ALLOWED_SEARCH_TOOLS`: comma-separated exact Search Tool names. Empty means **deny all**.
   - `ADAPTER_ALLOWED_HOSTS`: exact Host values expected at the adapter, including the port when present.
   - `ADAPTER_ALLOWED_ORIGINS`: exact browser Origin values if browser-based clients need them; leave empty when no browser Origin is expected.

The current environment example lists `spp_Parallel` and `openhands_tavily` because those names were observed in the earlier live test. Confirm that both still exist and are approved before enabling them in a real deployment.

## Run with Docker Compose

```bash
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 litellm-search-mcp-adapter
```

The Compose file binds the adapter only to `127.0.0.1:8088` on the host. Put a trusted reverse proxy in front of it for remote clients. Do not publish the MCP port directly to the public Internet.

Check liveness locally:

```bash
curl --fail http://127.0.0.1:8088/health/live
```

Readiness is a minimal status endpoint:

```bash
curl -i http://127.0.0.1:8088/health/ready
```

The readiness endpoint is intentionally unauthenticated and reveals only a status and registered tool count. It does not expose configuration, provider names, keys, or diagnostics.

## Reverse proxy requirements

Configure HTTPS at the reverse proxy and forward requests to `http://127.0.0.1:8088/mcp`. Preserve the `Host` and `Authorization` headers, disable response buffering for the MCP endpoint, allow long-lived responses, and do not cache MCP traffic. Add the public hostname (and exact port if present) to `ADAPTER_ALLOWED_HOSTS`.

Example Nginx location (adapt hostnames and TLS configuration to your environment):

```nginx
location /mcp {
    proxy_pass http://127.0.0.1:8088/mcp;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}
```

Do not expose the readiness endpoint through the public reverse proxy unless required.

## MCP clients

### OpenHands Agent Canvas / local backend

In the OpenHands MCP settings, add a custom server:
- Transport: **SHTTP**
- URL: `https://<your-adapter-host>/mcp`
- Authentication: **Bearer token**
- API Key: the value of `MCP_BEARER_TOKEN`

OpenHands documents that Bearer authentication sends `Authorization: Bearer <token>`. MCP servers added or changed apply to new conversations; start a new conversation after changing the server configuration.

### Visual Studio Code

Use the user-level MCP configuration (not a shared workspace file) and supply the token through an environment variable available to the VS Code/Copilot process:

```json
{
  "servers": {
    "litellm-search": {
      "type": "http",
      "url": "https://<your-adapter-host>/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_ADAPTER_TOKEN}"
      }
    }
  }
}
```

Set `MCP_ADAPTER_TOKEN` in the environment of the host process that launches VS Code. Current VS Code Copilot documentation supports environment-variable references in HTTP header values; do not assume that an input prompt or VS Code SecretStorage value can be interpolated into a Copilot HTTP header. Do not commit a populated credential.

### Other MCP clients

Any client that supports MCP Streamable HTTP and can send an Authorization Bearer header can use the same endpoint. Compatibility with each client/version must still be tested.

## Tool behavior and security

- Tools are exposed only when both discovered from LiteLLM and named in the server-side allowlist.
- Discovery failure retains the last-known-good registry; a malformed discovery response is never treated as an empty inventory.
- LiteLLM independently enforces authorization on each Search API execution. Discovery is not authorization.
- The LiteLLM key is never returned to MCP clients.
- The initial input schema accepts a non-empty query (maximum 4,000 characters) and `max_results` from 1 to 20 (default 5).
- Provider responses must contain a `results` array; empty arrays are valid.
- Network retries are bounded to two retries for discovery GET requests. Search POST requests are not retried after ambiguous network/5xx failures to avoid duplicate paid searches; HTTP 429 may be retried with a bounded Retry-After delay.
- MCP requests are globally rate-limited per adapter process (default 120 requests/minute across clients sharing the token).
- Response size is bounded; secrets and search queries are not logged.
- Health routes are intentionally minimal.
- SDK registry changes publish the 2026 subscription notification. Older clients that do not implement this notification may need a reconnect/new conversation to see changes; this is not silently represented as automatic live refresh.

## Development and tests

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
python -m compileall -q adapter tests
pytest -q
```

The exact MCP SDK version is pinned. Transitive dependency locking is a separate release gate; do not claim reproducible locked builds until a generated and reviewed lock file is committed.

## Operational safety

- Never use a LiteLLM master key.
- Never commit `.env`, credentials, or populated client configuration.
- Rotate the adapter token independently of the LiteLLM key.
- Restrict the LiteLLM Virtual Key to the intended Search Tools.
- Keep the adapter on a private network/VPN behind HTTPS.
- Do not disable Host/Origin protections to make a client connect; configure the correct allowlists.
- Do not enable an empty or broad Search Tool allowlist.
- Monitor readiness and container health; liveness alone does not prove LiteLLM connectivity.
