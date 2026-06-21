#!/usr/bin/env bash
# Run every R-suite live eval and capture a per-suite results store.
#
# Usage:
#     bash tests/llama_eval/run_all_r_scenarios.sh
#     GRC_AGENT_LIVE_LLAMA_MODEL=qwen3:8b bash tests/llama_eval/run_all_r_scenarios.sh
#
# Output: R_test_results/ at the repo root, with one <phase>.json per suite
# and one <phase>.md human-readable report.

set -euo pipefail

# Resolve repo root from this script's location.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

# Configuration (env wins, fall back to defaults).
MODEL="${GRC_AGENT_LIVE_LLAMA_MODEL:-gemma4:e4b-it-qat}"
SERVER_URL="${GRC_AGENT_LIVE_LLAMA_URL:-http://localhost:11434}"
RESULTS_DIR="${REPO_ROOT}/R_test_results"
N_RUNS="${GRC_AGENT_R_RUNS:-1}"

mkdir -p "${RESULTS_DIR}"

echo "Repo:        ${REPO_ROOT}"
echo "Model:       ${MODEL}"
echo "Server URL:  ${SERVER_URL}"
echo "Results dir: ${RESULTS_DIR}"
echo "n-runs:      ${N_RUNS}"
echo

# Each phase invocation writes to its own JSON store.
# Failures are logged but do not stop the overall run.
run_phase () {
    local module="$1"
    local label="$2"
    local results_path="${RESULTS_DIR}/${label}.json"
    echo "============================================================"
    echo "Phase: ${module}  ->  ${results_path}"
    echo "============================================================"
    local resume_args=()
    if [[ "${GRC_AGENT_R_RESUME:-0}" == "1" && -f "${results_path}" ]]; then
        resume_args=(--resume)
        echo "  [resume] Reusing cached runs from ${results_path}"
    fi
    if ! uv run python -m "${module}" \
            --model "${MODEL}" \
            --server-url "${SERVER_URL}" \
            --n-runs "${N_RUNS}" \
            --results-path "${results_path}" \
            "${resume_args[@]}"; then
        echo "  [warn] ${module} exited non-zero; continuing." >&2
    fi
    echo
}

run_phase tests.llama_eval.run_r0_release          r0_release
run_phase tests.llama_eval.run_r1_release          r1_release
run_phase tests.llama_eval.run_r2_release          r2_release
run_phase tests.llama_eval.run_dsp_gauntlet        dsp_gauntlet
run_phase tests.llama_eval.scenario11_nbfm_pivot   scenario11_nbfm
run_phase tests.llama_eval.scenario12_fft_pipeline scenario12_fft

echo "============================================================"
echo "Rendering Markdown reports"
echo "============================================================"
uv run python -m tests.llama_eval.render_results "${RESULTS_DIR}"

echo
echo "Done. Reports in ${RESULTS_DIR}/"
