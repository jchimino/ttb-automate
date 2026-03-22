#!/bin/bash
# TTB Automate — startup script
#
# Frees ports 8004 (ttb-app) and 5678 (n8n) before bringing up the stack,
# preventing "port already allocated" errors from a previous session or any
# other process that grabbed those ports.
#
# Usage:
#   ./start.sh              # standard start
#   ./start.sh --build      # rebuild images first
#   ./start.sh -d           # detached (background) mode
#   ./start.sh --build -d   # rebuild + detached

set -e

PORTS=(8004 5678)

echo "→ Checking for port conflicts..."
for PORT in "${PORTS[@]}"; do
  PIDS=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
  if [ -n "$PIDS" ]; then
    echo "  Port $PORT in use (PID $PIDS) — releasing..."
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
    sleep 1
  else
    echo "  Port $PORT is free."
  fi
done

echo ""
echo "→ Starting TTB Automate..."
echo "   Web UI:       http://localhost:8004"
echo "   n8n editor:   http://localhost:5678"
echo ""

docker compose up "$@"
