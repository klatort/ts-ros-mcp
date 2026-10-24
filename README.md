# ts-ros-mcp

MCP server that lets AI agents **manage a Thinkskink TS-ROS (Request
Orchestrator System) deployment** — the low-code integration/orchestration
platform used by telecom CSPs — through its REST/OpenAPI surface.

TS-ROS is proprietary and **per-deployment** (every client publishes its own
process endpoints, usually behind an API gateway), so this server is
config-driven: it never guesses a URL. Point it at a deployment's config and,
optionally, its OpenAPI spec — and your agent gets governed read/execute/retry
control over the orchestrator.

**Status:** fully verified against the bundled mock backend (21/21 e2e
checks, plus a live connection test from Hermes Agent's own MCP client). Not
yet wired to a real TS-ROS instance — see
[Configure a real deployment](#configure-a-real-deployment).

## Tool contract

Every tool returns a JSON object with `ok` (bool); failures add `error`
(string). `body` carries the deployment's response.

| Tool | Kind | Arguments | Does |
|---|---|---|---|
| `tsros_health` | read | — | Deployment liveness |
| `tsros_list_processes` | read | — | Published business processes/flows |
| `tsros_execute_process` | **write** | `process: str`, `payload: object` | Execute a process |
| `tsros_get_execution` | read | `execution_id: str` | One execution's status/trace |
| `tsros_search_executions` | read | `status?`, `since?`, `limit=50` | Filter executions (e.g. `FAILED`) |
| `tsros_retry_execution` | **write** | `execution_id: str` | Retry a failed execution |
| `tsros_bulk_retry` | **write** | `status="FAILED"`, `since?`, `limit=100` | Bulk-retry by filter |
| `tsros_list_operations` | read | — | All operations in the OpenAPI spec |
| `tsros_call_operation` | GET free, else **write** | `operation_id`, `path_params?`, `query_params?`, `body?` | Invoke any spec operation |

Semantics an agent can rely on:
- **Write tools are blocked** until `allow_writes: true` — they return
  `{"ok": false, "error": "Writes disabled..."}` instead of mutating.
- **Unmapped endpoints return `not configured`** — the server never fires a
  guessed path at a production orchestrator.
- Prefer `tsros_list_operations` → `tsros_call_operation` when a spec is
  configured: those reflect the deployment's real surface.

## Install & verify (one command)

```bash
./install.sh
```

Creates `.venv` (Python 3.11), installs pinned deps, runs the 21-check e2e
suite against the bundled mock. Expected tail: `21/21 checks passed` /
`ALL CHECKS PASSED`, exit 0. Manual equivalent:
`.venv/bin/python test_client.py`. With `mise`: `mise run test`.

## Register with your agent

All snippets assume the repo at `/path/to/ts-ros-mcp` — replace with the real
path. `TSROS_CONFIG` selects the config; `TSROS_TOKEN` (or whichever env var
your config's `auth.token_env` names) carries the credential.

**Hermes Agent** — `~/.hermes/config.yaml`, then restart Hermes; tools appear
as `mcp_tsros_*` in every conversation:

```yaml
mcp_servers:
  tsros:
    command: /path/to/ts-ros-mcp/.venv/bin/python
    args: [/path/to/ts-ros-mcp/server.py]
    env:
      TSROS_CONFIG: /path/to/ts-ros-mcp/config.yaml
      TSROS_TOKEN: "<deployment API token>"
    timeout: 60
```

**Claude Code**:

```bash
claude mcp add ts-ros \
  -e TSROS_CONFIG=/path/to/ts-ros-mcp/config.yaml \
  -e TSROS_TOKEN=<deployment-api-token> \
  -- /path/to/ts-ros-mcp/.venv/bin/python /path/to/ts-ros-mcp/server.py
```

**Claude Desktop / Cursor / any `.mcp.json` client** — same JSON shape
(Claude Desktop: `claude_desktop_config.json`; Cursor: `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "ts-ros": {
      "command": "/path/to/ts-ros-mcp/.venv/bin/python",
      "args": ["/path/to/ts-ros-mcp/server.py"],
      "env": {
        "TSROS_CONFIG": "/path/to/ts-ros-mcp/config.yaml",
        "TSROS_TOKEN": "<deployment API token>"
      }
    }
  }
}
```

**Any MCP client (stdio)**:

```bash
TSROS_CONFIG=/path/to/ts-ros-mcp/config.yaml \
  /path/to/ts-ros-mcp/.venv/bin/python /path/to/ts-ros-mcp/server.py
```

## Configure a real deployment

1. `cp config.example.yaml config.yaml` (gitignored — safe for real endpoint
   paths; keep secret *values* in env vars, never in the file).
2. Set `base_url` to the deployment's REST surface (direct or via its API
   gateway — DataPower/APIM/Kong as the deployment uses).
3. If the deployment publishes an OpenAPI/Swagger spec, set `spec:` to it
   (file or URL) — spec-driven tools then cover everything it describes.
4. Set `auth:` — `bearer` / `header` / `basic`; each names an **env var**,
   never a value.
5. Confirm each `endpoints:` path with the client's integration team; delete
   the ones you can't confirm (their tools will report `not configured`).
6. Keep `allow_writes: false` until reads are verified, then flip it.

## Verify your deployment (post-registration checklist)

Run these tool calls in order; expected results:

1. `tsros_health` → `ok: true` and the deployment's health payload.
2. `tsros_list_processes` → `ok: true` + process list (or `not configured` if
   `endpoints.processes` is unmapped — that's the safe failure, not a bug).
3. `tsros_search_executions {status: "FAILED"}` → current failed transactions.
4. Any write tool → expect the `Writes disabled` error while
   `allow_writes: false`. Seeing it means the gate works.

## Security model

- **Read-only by default** — writes gated by `allow_writes`, including
  non-GET spec calls.
- **No guessed URLs** — unmapped endpoints fail closed with `not configured`.
- **Secrets only in env vars** — configs carry env var names, never values;
  `config.yaml` is gitignored.
- **Bounded blast radius** — one config per deployment; per-request timeout
  configurable (default 30 s).

## For coding agents working on this repo

Read [`docs/agents-manual.md`](docs/agents-manual.md) first — commands,
architecture map, hard invariants, the add-a-tool pattern, and the
verification protocol. To make it auto-load in agent sessions, the repo owner
promotes it:

```bash
cp docs/agents-manual.md AGENTS.md
printf 'See AGENTS.md — the operating manual for this repo.\n' > CLAUDE.md
```

## Files

- `server.py` — the MCP server (stdio, mcp SDK 2.x)
- `mock_tsros.py` — zero-dependency mock TS-ROS backend
- `test_client.py` — 21-check e2e suite (writable + read-only modes)
- `install.sh` — one-command install + verification
- `config.example.yaml` — real-deployment template · `config.test.yaml` /
  `config.readonly.test.yaml` — mock configs
- `openapi.example.yaml` — spec matching the mock (proves spec-driven mode)
- `docs/agents-manual.md` — manual for coding agents
- `requirements.txt` (pinned: mcp==2.3.0, httpx, PyYAML) · `mise.toml` (tasks)
