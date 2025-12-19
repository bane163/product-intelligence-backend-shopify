#!/usr/bin/env bash
set -euo pipefail

# Run this script from anywhere; it will cd to the script's directory (the backend folder)
cd "$(dirname "$0")"

# Allow overriding host/port via env vars
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
VENV="./.venv"

# Enable debug mode for local development (starts cloudflared tunnel for Collabora)
export DEBUG=true

# Prefer running uvicorn via the venv python (avoids broken script shebangs if the venv was moved/renamed)
if [ -x "$VENV/bin/python" ]; then
  exec "$VENV/bin/python" -m uvicorn main:app --reload --host "$HOST" --port "$PORT"
elif [ -x "$VENV/bin/uvicorn" ]; then
  # Fallback to the uvicorn script if the python module path is not available
  exec "$VENV/bin/uvicorn" main:app --reload --host "$HOST" --port "$PORT"
else
  # Final fallback to system python/module; useful if you rely on a global env
  exec python -m uvicorn main:app --reload --host "$HOST" --port "$PORT"
fi
