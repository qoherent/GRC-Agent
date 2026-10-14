#!/usr/bin/env bash
# ==============================================================================
# GRC Agent - Office Machine Fresh Installation & One-Shot Trace Collection
# ==============================================================================
# Usage:
#   ./experiments/atomic_dataset/setup_and_run.sh [run_scenarios args...]
#
# Examples:
#   # Full bootstrap + run all 5 benchmark scenarios:
#   ./experiments/atomic_dataset/setup_and_run.sh
#
#   # Run a single scenario against a custom endpoint/model:
#   ./experiments/atomic_dataset/setup_and_run.sh --scenario 01_add_cosine_source \
#       --endpoint http://192.168.0.234:9000/v1 --model qwen3.8-27b
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

echo "=========================================================================="
echo " [1/5] Checking System Prerequisites (GNU Radio + PyGObject + uv)"
echo "=========================================================================="

if [ ! -x /usr/bin/python3 ]; then
    echo "[ERROR] /usr/bin/python3 not found."
    exit 1
fi

if ! /usr/bin/python3 -c "import gi, gnuradio.gr, gnuradio.grc" 2>/dev/null; then
    echo "[ERROR] System Python (/usr/bin/python3) is missing GNU Radio or PyGObject."
    echo "        Install them on Ubuntu/Debian with:"
    echo "          sudo apt update && sudo apt install -y gnuradio python3-gi python3-gi-cairo"
    exit 1
fi

GR_VERSION="$(/usr/bin/python3 -c 'from gnuradio import gr; print(gr.version())')"
echo "  -> System Python: $(/usr/bin/python3 --version)"
echo "  -> GNU Radio:     v${GR_VERSION}"

if ! command -v uv >/dev/null 2>&1; then
    echo "  -> 'uv' not found on PATH; installing uv to ~/.local/bin ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "  -> uv version:    $(uv --version)"

echo ""
echo "=========================================================================="
echo " [2/5] Provisioning System-Bridged Virtual Environment (.venv)"
echo "=========================================================================="

if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
    uv venv --system-site-packages --python /usr/bin/python3 "$REPO_ROOT/.venv"
fi

UV_LINK_MODE=copy uv sync --extra dev --locked --python "$REPO_ROOT/.venv/bin/python"

echo ""
echo "=========================================================================="
echo " [3/5] Verifying Python Environment & Native GRC Bridge"
echo "=========================================================================="

"$REPO_ROOT/.venv/bin/python" -c "
import gi
from gnuradio import gr
import pydantic_ai
import pydantic_ai_harness
import sqlite_vec
from grc_agent.adapter import get_platform
platform = get_platform()
block_count = sum(1 for b in platform.blocks if not str(b).startswith('_'))
print(f'  -> Verified ABI bridge: GNU Radio {gr.version()} ({block_count} catalog blocks)')
print(f'  -> Verified pydantic-ai {pydantic_ai.__version__} & sqlite-vec')
"

echo ""
echo "=========================================================================="
echo " [4/5] Pre-building Lexical Knowledge Indexes (Catalog + Wiki Docs)"
echo "=========================================================================="

"$REPO_ROOT/.venv/bin/python" -c "
import os, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as tmp:
    env_file = Path(tmp) / '.env'
    env_file.write_text('GRC_EMBED_BACKEND=lexical\n', encoding='utf-8')
    os.environ['GRC_AGENT_ENV'] = str(env_file)
    from grc_agent.adapter import query_catalog, query_docs
    c_res = query_catalog('analog_sig_source_x', 1)
    d_res = query_docs('sample rate', 1)
    assert c_res.get('ok'), f'Catalog index build failed: {c_res}'
    assert d_res.get('ok'), f'Docs index build failed: {d_res}'
    print('  -> Catalog & Docs FTS5 databases ready in src/grc_agent/vectors/')
"

echo ""
echo "=========================================================================="
echo " [5/5] Running Harness-Driven One-Shot Trajectory Collector"
echo "=========================================================================="

export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
"$REPO_ROOT/.venv/bin/python" -m experiments.atomic_dataset.run_scenarios "$@"
