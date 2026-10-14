#!/usr/bin/env bash
# Quickstart script to collect GRC Agent traces
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Find Python interpreter (prefers repo .venv or uv)
if [ -f "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
elif command -v uv &>/dev/null; then
    PYTHON="uv run --python python"
else
    PYTHON="python3"
fi

cd "$SCRIPT_DIR"

echo "=========================================================="
echo " Starting GRC Agent Trace Collector"
echo " Python: $PYTHON"
echo " Kit:    $SCRIPT_DIR"
echo "=========================================================="

"$PYTHON" "$SCRIPT_DIR/collect_traces.py" "$@"
