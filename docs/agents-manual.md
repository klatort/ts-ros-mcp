# ts-ros-mcp — agent operating manual

This is the manual for **coding agents working ON this repo**. Deploying
agents that just install and drive the server: see README.md.

(To make this file auto-load in agent sessions, copy it to `AGENTS.md` at the
repo root — the repo owner does this deliberately, see README.)

You are working on **ts-ros-mcp**: an MCP server that lets AI agents manage a
Thinkskink TS-ROS (Request Orchestrator System) deployment through its
REST/OpenAPI surface. TS-ROS is a proprietary, per-deployment telecom
orchestration platform — **there is no fixed API**, which drives every design
decision in this repo.

Current status: fully working against the bundled mock backend (21/21 e2e
checks). **Never tested against a real TS-ROS instance** — treat real-endpoint
behavior as unknown until a deployment spec exists.

## Commands

```bash
# one-time setup (or just: ./install.sh — does setup + verification)
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# THE verification command — run before declaring any change done
.venv/bin/python test_client.py        # expect: "21/21 checks passed", exit 0

# manual exploration (test_client.py does this itself; only for debugging)
.venv/bin/python mock_tsros.py 8931    # mock backend on 127.0.0.1:8931
TSROS_CONFIG=config.test.yaml .venv/bin/python server.py   # MCP server, stdio
```

With `mise`: `mise run test` · `mise run mock` · `mise run server`.

## Architecture

- `server.py` — the whole MCP server. Layers, top to bottom:
  config loader (`TSROS_CONFIG` env → config.yaml → config.example.yaml
  fallback) → HTTP layer (`_request`, `_auth_headers`, `_not_configured`) →
  OpenAPI spec layer (`_load_spec`, cached) → 9 `@mcp.tool()` functions.
- `mock_tsros.py` — zero-dependency (stdlib) fake TS-ROS: 4 seeded processes,
  5 seeded executions (2 FAILED), execute/retry/bulk-retry routes.
- `test_client.py` — spawns mock + server over stdio, exercises all 9 tools in
  writable AND read-only configs, exits non-zero on any failure.
- `install.sh` — one-command setup + verification (what a deploying agent runs).
- `config.test.yaml` / `config.readonly.test.yaml` — mock configs
  (writes ON / OFF). `config.example.yaml` — template for real deployments.
- `openapi.example.yaml` — spec matching the mock; proves spec-driven mode.
- `config.yaml` (real deployments) is **gitignored** — never commit it.

## Hard invariants — do not break these

1. **Never hardcode or invent TS-ROS endpoint paths.** Paths live only in the
   config's `endpoints:` templates. A tool whose endpoint key is missing
   returns the `_not_configured(...)` dict — it must never fire a guessed URL.
2. **Writes are gated.** Every mutating tool checks `_writes_blocked()` first,
   including non-GET `tsros_call_operation`. Reads work with writes off.
3. **Secrets are env var NAMES in config** (`token_env: TSROS_TOKEN`), values
   exported at launch. Never put a credential value in any committed file.
4. **mcp SDK is 2.x** (`mcp==2.3.0`): `from mcp.server.mcpserver import
   MCPServer`. `mcp.server.fastmcp` does NOT exist — most online tutorials
   show the 1.x import and will crash. Tool dict returns arrive at clients as
   JSON in `res.content[0].text`; `structuredContent` may be null.
5. **Mock port is 8931.** Do not "fix" it back to common ports — 8099 collided
   with an already-listening dashboard and produced confusing failures (health
   probe answered by the wrong service → `body` came back as HTML string).
6. **Test determinism:** `test_client.py` restarts the mock between the
   writable and read-only sessions so seeded FAILED counts hold. If you add
   state-mutating tests, keep that pattern.

## Documentation convention (this repo)

Every doc is written for TWO agent audiences and must stay machine-parseable:
headings per concern, exact commands in fenced blocks, tables for contracts,
no prose walls. (1) **Consumer agents** deploy and drive the server — they get
README.md (tool contracts, per-platform registration, verification sequence).
(2) **Coding agents** modify this repo — they get this file. When you change
behavior, update BOTH docs and the test expectations in the same commit.

## Adding a tool (the pattern)

1. Add an endpoint key to the configs' `endpoints:` maps (test + example).
2. Write the tool in `server.py`:
   `@mcp.tool()` → writes? `blocked = _writes_blocked(); if blocked: return blocked`
   → `ep = _endpoint("<key>"); if not ep: return _not_configured(...)`
   → `return _request(...)`. Always return a dict with `ok` (and `error` on
   failure). Docstring = the tool's description; first line matters.
3. Extend `test_client.py` in BOTH sessions (writable + read-only) and bump
   the expected tool count (currently 9).
4. Run the test command above; all checks must pass.

## Verification protocol

Before declaring any change done:
- `test_client.py` → 21/21 (or your updated count), exit 0.
- If you touched `server.py` imports or startup: also smoke-run
  `TSROS_CONFIG=config.test.yaml .venv/bin/python server.py </dev/null` and
  confirm the stderr banner prints config/base_url/writes/spec.
- Never push with a failing suite. Never register a mock config in a live
  Hermes gateway — only real endpoints belong in `mcp_servers:`.

## Real-deployment wiring (when a client spec arrives)

Prefer spec-driven mode: set `spec:` to the deployment's OpenAPI document —
`tsros_list_operations` / `tsros_call_operation` then cover everything
discovered. Map `endpoints:` templates only for the management operations the
spec confirms; delete any you can't confirm. Keep `allow_writes: false` until
reads are verified against the real instance.
