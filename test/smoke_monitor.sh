#!/bin/bash
# Run smoke tests in a loop until all pass. Useful after deploy to wait for health.
# Usage: ./smoke_monitor.sh [API_BASE_URL] [interval_seconds]
# Example: ./smoke_monitor.sh https://execflex-backend-1.onrender.com 30

API_BASE="${1:-https://execflex-backend-1.onrender.com}"
INTERVAL="${2:-60}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Monitoring $API_BASE every ${INTERVAL}s (Ctrl+C to stop)."
while true; do
    echo "--- $(date -u +%Y-%m-%dT%H:%M:%SZ) ---"
    if bash "$SCRIPT_DIR/smoke_test.sh" "$API_BASE"; then
        echo "All tests passed. Exiting."
        exit 0
    fi
    echo "Next run in ${INTERVAL}s..."
    sleep "$INTERVAL"
done
