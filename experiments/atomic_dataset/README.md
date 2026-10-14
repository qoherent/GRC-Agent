# GRC Agent — Harness-Driven One-Shot Dataset Collector

Collects verified single-turn ("one-shot" user prompt) agent trajectories driven through the **real GRC Agent production harness** (`build_agents_from_cfg(cfg).executor`) using **Ollama Cloud** (`deepseek-v4.1-flash` via `OllamaModel` + `OllamaProvider`), capturing:
- Intermediate grounding tool calls (`inspect_graph`, `query_knowledge`)
- Graph mutations (`change_graph`) with automatic human-in-the-loop approval resolution (`DeferredToolRequests` $\rightarrow$ `ToolApproved`)
- Guardrail errors and self-corrections (`RetryPromptPart` from Pydantic schema validation, domain `ModelRetry`, or `validate_flowgraph_state`, plus `ToolReturnPart(outcome='failed')` from `ToolFailed`)
- Post-edit verification calls (`inspect_graph`, `generate_python`)
- Native reasoning traces (`ThinkingPart` / `reasoning`)
- Wire-accurate OpenAI `messages` and `tools` (23 tool schemas) plus lossless canonical `ModelMessagesTypeAdapter` serialization

---

## 1. Office Machine Quickstart

### System Prerequisites (Ubuntu / Debian)
1. Ensure system GNU Radio 3.10+ and PyGObject bindings are installed for `/usr/bin/python3`:
   ```bash
   sudo apt update && sudo apt install -y gnuradio python3-gi python3-gi-cairo curl git
   ```
2. Set `OLLAMA_API_KEY` (or `OLLAMA_CLOUD_API_KEY`) in `.env` at the repo root (or export it in your shell):
   ```bash
   echo "OLLAMA_API_KEY=<your-ollama-cloud-api-key>" >> .env
   ```

### One-Command Setup & Run
From the repository root:
```bash
./experiments/atomic_dataset/setup_and_run.sh
```
`setup_and_run.sh` automatically:
1. Verifies `/usr/bin/python3`, `gnuradio`, `gi`, and `uv` (installs `uv` if absent).
2. Creates `.venv` with `--system-site-packages --python /usr/bin/python3` and runs `uv sync --extra dev --locked`.
3. Verifies the C++ ABI bridge (584 catalog blocks, `pydantic_ai`, `sqlite_vec`).
4. Pre-builds the lexical FTS5 indexes (`catalog_lexical.db` and `docs_lexical.db`) in `src/grc_agent/vectors/`.
5. Runs `experiments.atomic_dataset.run_scenarios` on **Ollama Cloud** (`https://ollama.com/v1`, model `deepseek-v4.1-flash`).

---

## 2. Common Commands

```bash
# Run a specific subset of scenarios (comma-separated):
./experiments/atomic_dataset/setup_and_run.sh \
    --scenario 01_add_cosine_source,03_lookup_and_add_agc,04_guardrail_error_recovery

# Override model or output directory on Ollama Cloud:
./experiments/atomic_dataset/setup_and_run.sh \
    --provider ollama_cloud \
    --model deepseek-v4.1-flash \
    --max-tokens 8192 \
    --out-dir experiments/atomic_dataset/output

# Verify an exported SFT JSONL file:
.venv/bin/python -m experiments.atomic_dataset.verify_traces \
    experiments/atomic_dataset/output/sft_trajectories.jsonl

# Extract the full ~584-block GNU Radio catalog taxonomy:
.venv/bin/python -m experiments.atomic_dataset.taxonomy \
    --output experiments/atomic_dataset/output/catalog_taxonomy.jsonl

# Run hermetic unit tests (offline, no LLM call):
.venv/bin/python -m pytest experiments/atomic_dataset/tests/ -v
```

---

## 3. Directory Layout & Outputs

| File / Path | Purpose |
| :--- | :--- |
| `setup_and_run.sh` | One-command environment bootstrap, index warmup, and scenario runner |
| `harness_driver.py` | Isolated `build_agents_from_cfg` runner (`OllamaModel` + `OllamaProvider`), guardrail & trajectory extractor |
| `scenarios.py` | Benchmark `ScenarioSpec` definitions (add scenarios here) |
| `verify_traces.py` | Structural and SFT invariant verifier for `messages`, `tools`, and `canonical_messages` |
| `taxonomy.py` | Introspects all ~584 installed GRC catalog blocks via `get_platform()` |
| `fixtures/dial_tone.grc` | Self-contained base flowgraph fixture |
| `output/sft_trajectories.jsonl` | Unsloth-ready SFT dataset (`messages`, `tools`, `system`, `canonical_messages`, `guardrail_events`, `usage`) |
| `output/<scenario_id>.md` | Step-by-step human-readable inspection report (reasoning, tool calls, guardrail retries, flowgraph diff) |
| `output/<scenario_id>.json` | Full structured telemetry record for the scenario |
