#!/usr/bin/env bash
# Usage: scripts/run_server.sh [port]   (starts the portal; Ctrl+C to stop)
cd "$(dirname "$0")/.." && exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${1:-8000}"
