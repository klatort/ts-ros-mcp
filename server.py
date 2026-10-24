#!/usr/bin/env python3
"""ts-ros-mcp — MCP server for managing Thinkskink TS-ROS via its REST/OpenAPI surface.

Design notes
------------
TS-ROS is a proprietary low-code orchestration platform; its REST surface is
per-deployment (each client publishes its own process endpoints, usually behind
an API gateway). So this server is CONFIG-DRIVEN:

* If the deployment publishes an OpenAPI/Swagger spec, point `spec:` at it and
  every operation becomes callable through `tsros_list_operations` /
  `tsros_call_operation` (discovered, zero guessing).
* The opinionated management tools (health, list/execute process, search/get/
  retry executions, bulk retry) map to endpoint templates you define once in
  the config. Nothing is hardcoded to invented paths — unmapped tools report
  "not configured" instead of firing blind requests.
* All tools are read-only until `allow_writes: true`.
* Credentials never live in the config file — only env var NAMES.

Run:  TSROS_CONFIG=config.yaml python server.py     (stdio MCP transport)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml
from mcp.server.mcpserver import MCPServer

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG_CANDIDATES = ("config.yaml", "config.example.yaml")


def _load_config() -> dict[str, Any]:
    raw = os.environ.get("TSROS_CONFIG")
    if raw:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise SystemExit(f"TSROS_CONFIG points to missing file: {path}")
    else:
        here = Path(__file__).parent
        path = next((here / c for c in DEFAULT_CONFIG_CANDIDATES if (here / c).is_file()), None)
        if path is None:
            raise SystemExit("No config found: set TSROS_CONFIG or place config.yaml next to server.py")
    cfg = yaml.safe_load(path.read_text()) or {}
    cfg["_config_path"] = str(path)
    return cfg


CFG = _load_config()
BASE_URL: str = str(CFG.get("base_url", "")).rstrip("/")
ALLOW_WRITES: bool = bool(CFG.get("allow_writes", False))
TIMEOUT: float = float(CFG.get("timeout", 30))
ENDPOINTS: dict[str, str] = dict(CFG.get("endpoints") or {})
AUTH: dict[str, Any] = dict(CFG.get("auth") or {})
SPEC_REF = CFG.get("spec")  # file path or URL to OpenAPI/Swagger

mcp = MCPServer(name="ts-ros", instructions=(
    "Tools for managing a Thinkskink TS-ROS (Request Orchestrator System) deployment. "
    "Read tools are always available; write tools (execute/retry/bulk) only work when "
    "allow_writes is enabled in the server config. Use tsros_list_operations first when "
    "an OpenAPI spec is configured — it lists every discovered operation."
))


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #

def _auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    kind = (AUTH.get("type") or "none").lower()
    if kind == "bearer":
        token = os.environ.get(AUTH.get("token_env", "TSROS_TOKEN"), "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif kind == "header":
        name = AUTH.get("header_name", "X-API-Key")
        token = os.environ.get(AUTH.get("token_env", "TSROS_TOKEN"), "")
        if token:
            headers[name] = token
    elif kind == "basic":
        user = os.environ.get(AUTH.get("username_env", "TSROS_USER"), "")
        pwd = os.environ.get(AUTH.get("password_env", "TSROS_PASS"), "")
        if user:
            headers["Authorization"] = (
                "Basic " + __import__("base64").b64encode(f"{user}:{pwd}".encode()).decode()
            )
    return headers


def _request(method: str, path_or_url: str, *, params: dict | None = None,
             json_body: Any = None) -> dict[str, Any]:
    url = path_or_url if path_or_url.startswith("http") else f"{BASE_URL}{path_or_url}"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.request(method, url, params=params, json=json_body,
                                  headers=_auth_headers())
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "url": url}
    body: Any
    try:
        body = resp.json()
    except Exception:
        body = resp.text[:2000]
    return {"ok": resp.status_code < 400, "status": resp.status_code, "url": url, "body": body}


def _endpoint(key: str) -> str | None:
    val = ENDPOINTS.get(key)
    return val if val else None


def _not_configured(tool: str, key: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"Endpoint '{key}' not configured for tool {tool}. "
                 f"Add it under 'endpoints:' in {CFG.get('_config_path')} "
                 f"(paths are per-deployment in TS-ROS).",
    }


def _writes_blocked() -> dict[str, Any] | None:
    if ALLOW_WRITES:
        return None
    return {"ok": False, "error": "Writes disabled: set allow_writes: true in the server config."}


# --------------------------------------------------------------------------- #
# OpenAPI spec discovery (optional)
# --------------------------------------------------------------------------- #

_SPEC_CACHE: dict[str, dict[str, Any]] | None = None


def _load_spec() -> dict[str, dict[str, Any]] | None:
    """Return {operation_id: {method, path, summary, has_body}} or None."""
    global _SPEC_CACHE
    if _SPEC_CACHE is not None:
        return _SPEC_CACHE or None
    if not SPEC_REF:
        _SPEC_CACHE = {}
        return None
    try:
        if str(SPEC_REF).startswith(("http://", "https://")):
            text = httpx.get(str(SPEC_REF), timeout=TIMEOUT).text
        else:
            text = Path(str(SPEC_REF)).expanduser().read_text()
        spec = yaml.safe_load(text) if str(SPEC_REF).endswith((".yaml", ".yml")) else json.loads(text)
    except Exception as exc:
        _SPEC_CACHE = {}
        print(f"[ts-ros-mcp] WARNING: could not load spec {SPEC_REF}: {exc}", file=sys.stderr)
        return None
    ops: dict[str, dict[str, Any]] = {}
    base = (spec.get("servers") or [{}])[0].get("url", "")
    for path, methods in (spec.get("paths") or {}).items():
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            op_id = op.get("operationId") or f"{method.lower()}_{path.replace('/', '_')}"
            ops[op_id] = {
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary") or op.get("description") or "",
                "has_body": "requestBody" in op,
            }
    ops["__base__"] = {"method": "BASE", "path": base, "summary": "", "has_body": False}
    _SPEC_CACHE = ops
    return ops


def _spec_op(operation_id: str) -> dict[str, Any] | None:
    ops = _load_spec()
    if not ops:
        return None
    return ops.get(operation_id)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

@mcp.tool()
def tsros_health() -> dict[str, Any]:
    """Liveness check of the TS-ROS deployment (maps to the configured health endpoint)."""
    ep = _endpoint("health")
    if not ep:
        return _not_configured("tsros_health", "health")
    return _request("GET", ep)


@mcp.tool()
def tsros_list_processes() -> dict[str, Any]:
    """List the business processes/flows published in this TS-ROS deployment."""
    ep = _endpoint("processes")
    if not ep:
        return _not_configured("tsros_list_processes", "processes")
    return _request("GET", ep)


@mcp.tool()
def tsros_execute_process(process: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute (write) a published TS-ROS business process synchronously.

    Args:
        process: process/flow name as routed by the configured endpoint template.
        payload: JSON body for the process invocation.
    """
    blocked = _writes_blocked()
    if blocked:
        return blocked
    ep = _endpoint("execute")
    if not ep:
        return _not_configured("tsros_execute_process", "execute")
    path = ep.replace("{process}", process)
    return _request("POST", path, json_body=payload)


@mcp.tool()
def tsros_get_execution(execution_id: str) -> dict[str, Any]:
    """Get status/trace of one process execution by its ID."""
    ep = _endpoint("execution")
    if not ep:
        return _not_configured("tsros_get_execution", "execution")
    return _request("GET", ep.replace("{id}", execution_id))


@mcp.tool()
def tsros_search_executions(status: str | None = None, since: str | None = None,
                            limit: int = 50) -> dict[str, Any]:
    """Search process executions — e.g. status=FAILED to find transactions needing attention.

    Args:
        status: optional status filter (e.g. FAILED, COMPLETED, RUNNING).
        since: optional ISO date/time lower bound, passed through as query param.
        limit: max results to request (default 50).
    """
    ep = _endpoint("executions_search")
    if not ep:
        return _not_configured("tsros_search_executions", "executions_search")
    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    if since:
        params["since"] = since
    return _request("GET", ep, params=params)


@mcp.tool()
def tsros_retry_execution(execution_id: str) -> dict[str, Any]:
    """Retry (write) a failed process execution by ID."""
    blocked = _writes_blocked()
    if blocked:
        return blocked
    ep = _endpoint("retry")
    if not ep:
        return _not_configured("tsros_retry_execution", "retry")
    return _request("POST", ep.replace("{id}", execution_id))


@mcp.tool()
def tsros_bulk_retry(status: str = "FAILED", since: str | None = None,
                     limit: int = 100) -> dict[str, Any]:
    """Bulk-retry (write) many executions at once, e.g. all FAILED since a date.

    Args:
        status: status filter selecting the executions to retry (default FAILED).
        since: optional ISO date/time lower bound.
        limit: cap on the number of executions retried in one action.
    """
    blocked = _writes_blocked()
    if blocked:
        return blocked
    ep = _endpoint("bulk_retry")
    if not ep:
        return _not_configured("tsros_bulk_retry", "bulk_retry")
    body: dict[str, Any] = {"status": status, "limit": limit}
    if since:
        body["since"] = since
    return _request("POST", ep, json_body=body)


@mcp.tool()
def tsros_list_operations() -> dict[str, Any]:
    """List every REST operation discovered from the deployment's OpenAPI spec (if configured)."""
    ops = _load_spec()
    if not ops:
        return {"ok": True, "operations": [],
                "note": "No OpenAPI spec configured. Set 'spec:' (file or URL) in the config "
                        "to enable spec-driven discovery."}
    base = ops.pop("__base__", {}).get("path", "")
    return {"ok": True, "base_url_from_spec": base, "count": len(ops),
            "operations": ops}


@mcp.tool()
def tsros_call_operation(operation_id: str, path_params: dict[str, str] | None = None,
                         query_params: dict[str, Any] | None = None,
                         body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Invoke any operation discovered from the OpenAPI spec, by operationId.

    Non-GET operations require allow_writes: true. Path params fill {placeholders}
    in the operation path; query params and body are passed through.
    """
    op = _spec_op(operation_id)
    if not op:
        return {"ok": False,
                "error": f"Unknown operation '{operation_id}'. Call tsros_list_operations first."}
    if op["method"] != "GET":
        blocked = _writes_blocked()
        if blocked:
            return blocked
    path = op["path"]
    for key, val in (path_params or {}).items():
        path = path.replace("{" + key + "}", str(val))
    return _request(op["method"], path, params=query_params or None,
                    json_body=body if op["has_body"] else None)


if __name__ == "__main__":
    print(f"[ts-ros-mcp] config={CFG.get('_config_path')} base_url={BASE_URL or '(unset)'} "
          f"writes={'ON' if ALLOW_WRITES else 'off'} spec={SPEC_REF or 'none'}", file=sys.stderr)
    mcp.run()
