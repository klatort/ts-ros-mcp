#!/usr/bin/env bash
# ts-ros-mcp — one-command install + verification for deploying agents.
# Creates .venv, installs pinned deps, runs the full e2e suite.
set -euo pipefail
cd "$(dirname "$0")"

# locate uv (PATH first, then common install locations)
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  UV="$(ls ~/.hermes/tools/uv-*/uv ~/.local/bin/uv 2>/dev/null | head -1 || true)"
fi
if [ -z "$UV" ]; then
  echo "ERROR: uv not found. Install: https://docs.astral.sh/uv/getting-started/install/" >&2
  exit 1
fi

[ -d .venv ] || "$UV" venv --python 3.11 .venv
"$UV" pip install --python .venv/bin/python -r requirements.txt

echo "=== running e2e suite ==="
.venv/bin/python test_client.py

cat <<'EOF'

ts-ros-mcp is installed and verified.
Next: copy config.example.yaml -> config.yaml, point it at your TS-ROS
deployment, and register the server with your agent (see README.md).
EOF
