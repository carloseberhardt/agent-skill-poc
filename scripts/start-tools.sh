#!/usr/bin/env bash
# Start all MCP tool servers in one terminal. Ctrl-C kills them all cleanly.
set -e

PIDS=()

cleanup() {
    echo ""
    echo "Stopping tools..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null
    done
    echo "All tools stopped."
}
trap cleanup EXIT INT TERM

cd "$(dirname "$0")/.."

echo "Starting MCP tool servers..."

uv run python mock-agents/cost_api.py &
PIDS+=($!)
echo "  cost-api (port 5003) — PID $!"

uv run python mock-agents/employee_lookup.py &
PIDS+=($!)
echo "  employee-lookup (port 5004) — PID $!"

uv run python mock-agents/discord_notifier.py &
PIDS+=($!)
echo "  discord-notifier (port 5005) — PID $!"

echo ""
echo "All tools running. Press Ctrl-C to stop."
wait
