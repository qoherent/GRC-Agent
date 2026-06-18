# GRC Agent

Local companion for GNU Radio Companion (`.grc`) flowgraphs. GRC Agent helps you inspect, document, and edit your graphs using local LLMs.

Runs as a sidekick window alongside GRC (GUI).

---

## Installation

### Prerequisites
- **Python >= 3.12**
- **GNU Radio 3.10.x** (with `grcc` on `PATH`)
- **An LLM backend** — choose one (the app never spawns or stops a backend daemon):
  - **Ollama** (local, default): a running `ollama serve` reachable at
    `http://localhost:11434`, with a tool-calling-capable model pulled. The
    model's chat template must include the `{{ .Tools }}` section or the agent
    cannot call its tools.
  - **OpenRouter** (cloud): set `OPENROUTER_API_KEY` (and optionally
    `OPENROUTER_MODEL`) in a `.env` file at the repo root.

### Setup
```bash
git clone https://github.com/qoherent/grc-agent.git
cd grc-agent
uv venv --system-site-packages --python /usr/bin/python3
uv sync --locked --python .venv/bin/python
```

A starter config lives at `grc_agent.toml` in the repo root; user overrides
go to `~/.config/grc_agent/config.toml`. On first launch you pick **Ollama
(local)** or **OpenRouter (cloud)** via the inline model toolbar; the choice
is persisted to `~/.config/grc_agent/preferences.json`.

---

## Usage

### GUI — Desktop Panel
Launch the sidekick panel alongside GRC:
```bash
uv run grc-agent-gui path/to/your_copy.grc
```
Features include:
- Chat interface for graph modifications
- Live flowgraph inspector (variables, blocks, connections)
- Compile, run, and stop controls for testing on the fly
- Inline model toolbar: pick Ollama (local) or OpenRouter (cloud), select a model, and live-swap the backend without restarting. The selection persists across sessions.
- Local chat-session history: `File > Recent Sessions...` to browse and reopen past conversations, persisted in a local SQLite store

If the backend is unreachable on launch, the GUI opens in degraded mode
(chat disabled, status bar reports the failure) instead of exiting —
recover via the inline model toolbar.

---

## Running the R-suite eval (live model evals)

Each R-suite (`R0` read-only, `R1` mutations, `R2` chaos monkey, the DSP
fuzzing gauntlet, and the R4 scenarios) can be run individually with the
modules under `tests/llama_eval/`. The combined runner captures every
suite's results into a per-suite JSON store plus a human-readable Markdown
report:

```bash
# Make sure Ollama is up and the model is pulled:
ollama pull gemma4:e4b-it-qat

# Run every R-suite, write R_test_results/ at the repo root:
GRC_AGENT_LIVE_LLAMA_MODEL=gemma4:e4b-it-qat \
  bash tests/llama_eval/run_all_r_scenarios.sh
```

Override defaults via env: `GRC_AGENT_LIVE_LLAMA_MODEL`, `GRC_AGENT_LIVE_LLAMA_URL`,
`GRC_AGENT_R_RUNS`. Output is one `<phase>.json` plus one `<phase>.md` per
suite (e.g. `r0_release.json` / `r0_release.md`). The Markdown reports show
per-scenario pass/fail, every turn's prompt, model reply, requested and
executed tool calls, and per-dimension pass flags.

Re-render existing JSON stores without re-running the evals:

```bash
uv run python -m tests.llama_eval.render_results R_test_results
```

---

## Example Prompts
- `Summarize this graph.`
- `Show parameters for analog_sig_source_x_0.`
- `Find a low-pass filter block.`
- `Change samp_rate to 48000 and validate.`
- `Change the signal source frequency from 440 to 1000.`
