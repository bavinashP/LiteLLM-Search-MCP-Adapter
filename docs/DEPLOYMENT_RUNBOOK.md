# Deployment Runbook

**Target:** dedicated Linux Docker host  
**Adapter endpoint:** `https://<adapter-host>/mcp` through a trusted reverse proxy  
**Status:** operational instructions; actual private-host deployment is not yet performed.

## 1. Prepare secrets and configuration on the Linux host

Clone the repository and enter its root directory.

```bash
git clone https://github.com/bavinashP/LiteLLM-Search-MCP-Adapter.git
cd LiteLLM-Search-MCP-Adapter
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
```

Generate the adapter token on the Linux host. Put the generated value directly into the local `.env` file as `MCP_BEARER_TOKEN`. Do not paste it into a chat, terminal screenshot, ticket, or GitHub issue.

Configure the following values in `.env`:

- `LITELLM_BASE_URL`: actual LiteLLM Proxy root URL, with no path.
- `LITELLM_API_KEY`: a newly created dedicated least-privilege LiteLLM Virtual Key.
- `MCP_BEARER_TOKEN`: the independently generated 64-character hexadecimal adapter token.
- `LITELLM_ALLOWED_SEARCH_TOOLS`: exact Search Tool names explicitly approved for MCP exposure.
- `ADAPTER_ALLOWED_HOSTS`: include the public hostname received from the reverse proxy, plus local health-check host values.
- `ADAPTER_ALLOWED_ORIGINS`: add exact browser origins only if the actual client sends an Origin header.

The old disposable test key was revoked. Do not reuse it. Do not use the LiteLLM master key.

## 2. Verify LiteLLM permissions before starting

Using the new dedicated Virtual Key locally (never in this chat):

1. Confirm it can call `GET /v1/search/tools`.
2. Confirm its Team and key-level Search Tool grants permit only the intended tools.
3. Ensure the adapter allowlist is the intersection of the intended exposure policy and LiteLLM's intended grants.
4. Remember: LiteLLM discovery is not permission-filtered; LiteLLM enforces permission on actual Search API execution.

An empty adapter allowlist intentionally exposes no MCP tools.

## 3. Build and start

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose ps
```

The Compose service binds to `127.0.0.1:8088` only. It is not directly exposed to the public network.

## 4. Verify health

```bash
curl --fail http://127.0.0.1:8088/health/live
curl -i http://127.0.0.1:8088/health/ready
docker compose ps
docker compose logs --tail=100 litellm-search-mcp-adapter
```

Expected:
- Liveness returns HTTP 200 when the process is running.
- Readiness returns HTTP 200 only after a successful Search Tool discovery.
- Readiness returns HTTP 503 while discovery is unavailable or degraded.
- Health output must not contain secrets, tool credentials, query contents, or raw upstream diagnostics.

## 5. Configure HTTPS reverse proxy

Terminate TLS on a trusted reverse proxy and forward `/mcp` to `http://127.0.0.1:8088/mcp`.

Required behavior:
- Preserve the Authorization header.
- Preserve the expected Host header and add it to `ADAPTER_ALLOWED_HOSTS`.
- Disable proxy buffering for MCP streaming.
- Set suitable read/send timeouts for Streamable HTTP.
- Disable caching.
- Restrict public access to the MCP endpoint as appropriate.
- Do not expose health routes publicly unless there is a specific operational need.

The adapter's DNS-rebinding protection is enabled. Do not turn it off to bypass a Host/Origin error; correct the allowlists.

## 6. Connect OpenHands

For a current Agent Canvas/local-backend installation:
1. Open `Customize > MCP Servers`.
2. Add a custom server.
3. Select SHTTP.
4. URL: `https://<adapter-host>/mcp`.
5. Authentication: Bearer token.
6. Enter the locally stored `MCP_BEARER_TOKEN` in the API Key field.
7. Test the connection and verify the reported tools.
8. Start a new conversation after changing MCP server settings; OpenHands documents that server changes apply to new conversations.

Do not use the old `openhands mcp` CLI instructions as the default path; its documentation marks that CLI as no longer actively maintained.

## 7. Connect Visual Studio Code

Use the user-level MCP configuration, not a shared workspace configuration. Configure the remote HTTP server and an Authorization header using an environment-variable reference:

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

Set `MCP_ADAPTER_TOKEN` in the environment of the process that launches VS Code/Copilot. Current Copilot configuration supports environment-variable references in HTTP headers; do not assume an input prompt or VS Code SecretStorage interpolation is available for this header path.

## 8. End-to-end acceptance tests

For each client separately:
- Confirm server connection.
- Confirm only allowlisted tools are listed.
- Run a low-cost query with `max_results=1`.
- Confirm the response includes a results array and that each returned result is passed through.
- Confirm a valid no-result search is represented as an empty array, not an exception.
- Confirm invalid/unauthorized LiteLLM tool calls return sanitized errors.
- Add/remove an allowlisted Search Tool in LiteLLM and wait at least one discovery interval; confirm the MCP registry changes.
- If the client does not react to the 2026 list-change subscription, reconnect/start a new conversation and verify the new list.
- Confirm a missing/incorrect adapter token returns HTTP 401.
- Confirm the configured rate limit returns HTTP 429 with Retry-After when exceeded.
- Confirm invalid Host/Origin is rejected.

Do not run repeated search tests unnecessarily; provider calls may incur cost.

## 9. Operations

### View logs and status

```bash
docker compose ps
docker compose logs --tail=100 litellm-search-mcp-adapter
```

Do not enable debug logging in production unless necessary and reviewed. The adapter intentionally does not log search query text or secret values.

### Rotate the adapter token

1. Generate a new token locally with `openssl rand -hex 32`.
2. Replace `MCP_BEARER_TOKEN` in the protected `.env` file.
3. Update the OpenHands and VS Code user-level client settings/environment.
4. Recreate the container:

   ```bash
   docker compose up -d --force-recreate
   ```

No image rebuild is required for secret rotation.

### Rotate the LiteLLM Virtual Key

1. Create a replacement dedicated key with the same least-privilege Search Tool grants.
2. Update `LITELLM_API_KEY` in `.env`.
3. Recreate the container.
4. Verify readiness and one low-cost search.
5. Revoke the old key only after the replacement is verified.

### Update the adapter

Review release notes and dependency changes, then:

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose ps
```

Do not update automatically in production without reviewing changes and passing CI.

## 10. Explicit limitations

- One shared static Bearer token is used by the initial deployment profile; there is no individual user identity or per-client quota.
- The request rate limit is global per adapter process, not per client.
- Old MCP clients may need reconnect/new conversation after dynamic registry changes.
- The adapter does not implement OAuth 2.1.
- A generated dependency lock file and image vulnerability scan remain release-hardening tasks.
- This runbook does not claim that the user's private Linux deployment or either actual client has already been completed.


## 11. Dependency and image reproducibility

The repository commits `uv.lock` and pins uv 0.13.8. The Dockerfile installs runtime dependencies using `uv sync --locked`; do not run an unlocked `pip install` in production builds. CI runs `uv lock --check` and builds the image from the committed lock. Update dependencies only through a reviewed lockfile change.
