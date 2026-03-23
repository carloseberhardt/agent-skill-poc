#!/usr/bin/env bash
# Start all A2A agents in one terminal. Ctrl-C kills them all cleanly.
set -e

PIDS=()

cleanup() {
    echo ""
    echo "Stopping agents..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null
    done
    echo "All agents stopped."
}
trap cleanup EXIT INT TERM

cd "$(dirname "$0")/.."

echo "Starting A2A agents..."

uv run python mock-agents/data_agent.py &
PIDS+=($!)
echo "  data-agent (port 5001) — PID $!"

uv run python mock-agents/security_agent.py &
PIDS+=($!)
echo "  security-agent (port 5002) — PID $!"

uv run python mock-agents/delivery_agent.py &
PIDS+=($!)
echo "  delivery-agent (port 5006) — PID $!"

echo ""
echo "All agents running. Press Ctrl-C to stop."
wait
