#!/usr/bin/env python3
"""End-to-end test for ts-ros-mcp against the mock backend.

Starts the mock, spawns the MCP server twice (writable + read-only configs)
over stdio, exercises every tool, and prints a PASS/FAIL summary.
Exit code 0 only if every check passes.

Run:  python test_client.py     (from the project venv, mock started separately
      or auto-started here — this script starts/stops the mock itself)
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).parent
PY = sys.executable
MOCK_PORT = 8931
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


async def run_tool(session: ClientSession, tool: str, args: dict | None = None) -> dict:
    res = await session.call_tool(tool, args or {})
    text = res.content[0].text if res.content else "{}"
    return json.loads(text)


async def exercise(config: str, writable: bool) -> None:
    env = dict(os.environ, TSROS_CONFIG=str(HERE / config))
    params = StdioServerParameters(command=PY, args=[str(HERE / "server.py")], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print(f"\n== session: {config} (writes {'ON' if writable else 'OFF'}) ==")
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            check("9 tools registered", len(names) == 9, f"got {len(names)}: {', '.join(names)}")

            r = await run_tool(session, "tsros_health")
            check("health", r.get("ok") and r["body"]["status"] == "UP", str(r.get("body")))

            r = await run_tool(session, "tsros_list_processes")
            procs = r.get("body", {}).get("processes", [])
            check("list_processes", r.get("ok") and len(procs) == 4,
                  f"{len(procs)} processes")

            r = await run_tool(session, "tsros_search_executions", {"status": "FAILED"})
            failed = r.get("body", {}).get("executions", [])
            check("search FAILED", r.get("ok") and len(failed) == 2,
                  f"{len(failed)} failed (seeded 2)")

            r = await run_tool(session, "tsros_execute_process",
                               {"process": "NumberPortability",
                                "payload": {"msisdn": "51987654321"}})
            if writable:
                new_id = r.get("body", {}).get("id", "")
                check("execute_process", r.get("ok") and r.get("status") == 201
                      and r["body"]["status"] == "COMPLETED", f"id={new_id}")
                r = await run_tool(session, "tsros_get_execution", {"execution_id": new_id})
                check("get_execution", r.get("ok") and r["body"]["id"] == new_id,
                      f"attempts={r.get('body', {}).get('attempts')}")

                r = await run_tool(session, "tsros_execute_process",
                                   {"process": "GREGeneration", "payload": {"fail": True}})
                fail_id = r.get("body", {}).get("id", "")
                check("execute failing process", r.get("ok") and r["body"]["status"] == "FAILED",
                      f"id={fail_id}")

                r = await run_tool(session, "tsros_retry_execution", {"execution_id": fail_id})
                check("retry_execution", r.get("ok") and r["body"]["status"] == "COMPLETED"
                      and r["body"]["attempts"] == 2, str(r.get("body", {}).get("status")))

                r = await run_tool(session, "tsros_bulk_retry",
                                   {"status": "FAILED", "limit": 100})
                check("bulk_retry", r.get("ok") and r["body"]["retried"] >= 1,
                      f"retried={r.get('body', {}).get('retried')}")
            else:
                check("write blocked (execute)", not r.get("ok")
                      and "allow_writes" in r.get("error", ""), str(r.get("error"))[:60])
                r = await run_tool(session, "tsros_retry_execution", {"execution_id": "exec-1002"})
                check("write blocked (retry)", not r.get("ok"), str(r.get("error"))[:60])

            # spec-driven tools
            r = await run_tool(session, "tsros_list_operations")
            ops = r.get("operations", {})
            check("spec discovery", r.get("ok") and r.get("count") == 7,
                  f"{r.get('count')} operations discovered")

            r = await run_tool(session, "tsros_call_operation",
                               {"operation_id": "list_executions",
                                "query_params": {"status": "COMPLETED", "limit": "10"}})
            rows = r.get("body", {}).get("executions", [])
            check("spec call (GET)", r.get("ok") and all(
                x["status"] == "COMPLETED" for x in rows) and rows, f"{len(rows)} rows")

            r = await run_tool(session, "tsros_call_operation",
                               {"operation_id": "execute_process",
                                "path_params": {"process": "SubscriptionManager"},
                                "body": {"plan": "postpago-99"}})
            if writable:
                check("spec call (POST)", r.get("ok") and r.get("status") == 201,
                      f"id={r.get('body', {}).get('id')}")
            else:
                check("spec call (POST) blocked", not r.get("ok"), str(r.get("error"))[:60])


async def main() -> None:
    # start mock (fresh seed per session so FAILED counts are deterministic)
    def start_mock() -> subprocess.Popen:
        m = subprocess.Popen([PY, str(HERE / "mock_tsros.py"), str(MOCK_PORT)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                httpx.get(f"{MOCK_URL}/health", timeout=1)
                return m
            except Exception:
                time.sleep(0.25)
        raise SystemExit("mock did not start")

    mock = start_mock()
    try:
        print(f"[test] mock up at {MOCK_URL}")
        await exercise("config.test.yaml", writable=True)
        mock.send_signal(signal.SIGTERM)
        mock.wait(timeout=5)
        mock = start_mock()
        await exercise("config.readonly.test.yaml", writable=False)
    finally:
        mock.send_signal(signal.SIGTERM)
        mock.wait(timeout=5)

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 50}\n{passed}/{len(results)} checks passed")
    if passed != len(results):
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
