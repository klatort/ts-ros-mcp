# ts-ros-mcp

MCP server for managing a **Thinkskink TS-ROS** (Request Orchestrator System)
deployment — the low-code integration/orchestration platform used by telecom
CSPs — through its REST/OpenAPI surface.

No MCP server exists for TS-ROS publicly (it's a proprietary, per-deployment
platform), so this one is **config-driven** rather than hardcoded:

- **Spec-driven mode** — if the deployment publishes an OpenAPI/Swagger spec,
  set `spec:` and every operation is discovered and callable through
  `tsros_list_operations` / `tsros_call_operation`. No path guessing.
- **Opinionated management tools** — health, list/execute process,
  search/get/retry executions, bulk retry. Endpoint templates are mapped once
  in the config; an unmapped tool reports `not configured` and never fires a
  guessed path.
- **Read-only by default** — write tools (execute/retry/bulk) stay blocked
  until `allow_writes: true`.
- **Secrets stay in env vars** — the config only holds env var *names*.
- **Ships with a mock backend** — the full chain is testable without a real
  TS-ROS instance.

## Tools

| Tool | Kind | Purpose |
|---|---|---|
| `tsros_health` | read | Deployment liveness |
| `tsros_list_processes` | read | Published business processes/flows |
| `tsros_execute_process` | **write** | Execute a process (JSON payload) |
| `tsros_get_execution` | read | One execution's status/trace by ID |
| `tsros_search_executions` | read | Filter by status/date (e.g. `FAILED`) |
| `tsros_retry_execution` | **write** | Retry a failed execution |
| `tsros_bulk_retry` | **write** | Bulk-retry by status/date/limit |
| `tsros_list_operations` | read | All operations from the OpenAPI spec |
| `tsros_call_operation` | GET free / **write** otherwise | Invoke any spec operation by ID |

## Quickstart

```bash
cd ts-ros-mcp
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# end-to-end test against the built-in mock (starts/stops its own mock):
.venv/bin/python test_client.py          # expect: 21/21 checks passed

# or drive it manually:
.venv/bin/python mock_tsros.py 8931      # mock backend
TSROS_CONFIG=config.test.yaml .venv/bin/python server.py   # MCP server (stdio)
```

With `mise`: `mise run test` · `mise run mock` · `mise run server`.

## Register in Hermes Agent

Add to `~/.hermes/config.yaml` (then restart Hermes):

```yaml
mcp_servers:
  tsros:
    command: /home/hermes/ts-ros-mcp/.venv/bin/python
    args: [/home/hermes/ts-ros-mcp/server.py]
    env:
      TSROS_CONFIG: /home/hermes/ts-ros-mcp/config.yaml
      TSROS_TOKEN: "<the deployment's API token>"   # only if auth.type uses it
    timeout: 60
```

Tools then appear as `mcp_tsros_*` in every conversation.

## Wiring a real TS-ROS deployment

1. Copy `config.example.yaml` → `config.yaml`.
2. Set `base_url` to the client's TS-ROS REST surface (direct or through their
   API gateway — DataPower/APIM/Kong as the deployment uses).
3. Get the deployment's OpenAPI/Swagger spec from the client's integration
   team (or Thinkskink) and point `spec:` at it — this is the reliable path;
   the endpoint templates below it are the fallback for the handful of
   management operations.
4. Fill `auth:` (bearer / header / basic — values exported as env vars).
5. Confirm each `endpoints:` path against the deployment. TS-ROS paths are
   **per-deployment**: every client publishes its own process endpoints.
   Anything you can't confirm, delete — the tool will say "not configured"
   instead of calling a wrong URL.
6. Keep `allow_writes: false` until you've verified reads, then flip it.

## Files

- `server.py` — the MCP server (stdio transport, mcp SDK 2.x)
- `mock_tsros.py` — zero-dependency mock TS-ROS backend for testing
- `test_client.py` — end-to-end test (21 checks: reads, writes, write-blocking,
  spec discovery, spec-driven calls; exits non-zero on any failure)
- `config.example.yaml` — template for a real deployment
- `config.test.yaml` / `config.readonly.test.yaml` — mock configs (writable / read-only)
- `openapi.example.yaml` — minimal spec matching the mock, proves spec-driven mode
- `requirements.txt` — pinned deps (mcp==2.3.0, httpx, PyYAML)
- `mise.toml` — run tasks

## Status

- ✅ 21/21 end-to-end checks pass against the mock (both writable and
  read-only modes, including write-blocking and spec-driven calls)
- ✅ Verified connectable by Hermes Agent's own MCP client
- ⏳ Untested against a real TS-ROS instance — needs a deployment URL, spec,
  and credentials. When the real spec is available, prefer spec-driven mode;
  adjust or delete the endpoint templates based on what it actually exposes.
