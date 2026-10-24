#!/usr/bin/env python3
"""Mock TS-ROS backend for local testing of ts-ros-mcp.

Simulates the operational surface an MCP server would manage on a real
Thinkskink TS-ROS deployment: published processes, executions with status
transitions, retry and bulk-retry. Zero dependencies (stdlib only).

Run:  python mock_tsros.py [port]     (default port 8931)
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = int(__import__("sys").argv[1]) if len(__import__("sys").argv) > 1 else 8931

LOCK = threading.Lock()

PROCESSES = [
    {"name": "ProductActivation", "version": "2.1", "description": "TMF622-style product order orchestration"},
    {"name": "NumberPortability", "version": "1.4", "description": "Full-suite number portability flow"},
    {"name": "GREGeneration", "version": "1.0", "description": "SUNAT electronic shipping guide generation"},
    {"name": "SubscriptionManager", "version": "3.0", "description": "Product subscription fulfillment"},
]

EXECUTIONS: dict[str, dict] = {}


def _seed() -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for i, status in enumerate(["COMPLETED", "COMPLETED", "FAILED", "RUNNING", "FAILED"]):
        eid = f"exec-{1000 + i}"
        EXECUTIONS[eid] = {
            "id": eid,
            "process": PROCESSES[i % len(PROCESSES)]["name"],
            "status": status,
            "startedAt": now,
            "finishedAt": now if status != "RUNNING" else None,
            "attempts": 1,
            "error": "Adapter timeout calling BSCS SOI" if status == "FAILED" else None,
        }


_seed()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def log_message(self, fmt, *args):  # quieter
        print(f"[mock] {self.command} {self.path}")

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if parsed.path == "/health":
            return self._send(200, {"status": "UP", "service": "mock-ts-ros"})
        if parsed.path == "/api/processes":
            return self._send(200, {"processes": PROCESSES})
        if parsed.path == "/api/executions":
            with LOCK:
                rows = list(EXECUTIONS.values())
            if qs.get("status"):
                rows = [r for r in rows if r["status"] == qs["status"].upper()]
            rows.sort(key=lambda r: r["id"], reverse=True)
            return self._send(200, {"executions": rows[: int(qs.get("limit", 50))],
                                    "total": len(rows)})
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "executions":
            row = EXECUTIONS.get(parts[2])
            if row:
                return self._send(200, row)
            return self._send(404, {"error": "execution not found"})
        return self._send(404, {"error": f"no route {parsed.path}"})

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "processes" and parts[3] == "execute":
            name = parts[2]
            if not any(p["name"] == name for p in PROCESSES):
                return self._send(404, {"error": f"unknown process '{name}'"})
            payload = self._body()
            eid = f"exec-{uuid.uuid4().hex[:8]}"
            failed = bool(payload.get("fail"))
            row = {
                "id": eid, "process": name,
                "status": "FAILED" if failed else "COMPLETED",
                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "attempts": 1,
                "error": "Simulated failure (payload had fail=true)" if failed else None,
            }
            with LOCK:
                EXECUTIONS[eid] = row
            return self._send(201, row)
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "executions" and parts[3] == "retry":
            with LOCK:
                row = EXECUTIONS.get(parts[2])
                if not row:
                    return self._send(404, {"error": "execution not found"})
                row["status"] = "COMPLETED"
                row["attempts"] += 1
                row["error"] = None
                row["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            return self._send(200, row)
        if parsed.path == "/api/executions/bulk-retry":
            body = self._body()
            status = (body.get("status") or "FAILED").upper()
            limit = int(body.get("limit") or 100)
            with LOCK:
                targets = [r for r in EXECUTIONS.values() if r["status"] == status][:limit]
                for r in targets:
                    r["status"] = "COMPLETED"
                    r["attempts"] += 1
                    r["error"] = None
            return self._send(200, {"retried": len(targets), "status_filter": status})
        return self._send(404, {"error": f"no route {parsed.path}"})


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[mock] TS-ROS mock listening on http://{HOST}:{PORT} "
          f"({len(EXECUTIONS)} seeded executions, {len(PROCESSES)} processes)")
    server.serve_forever()
