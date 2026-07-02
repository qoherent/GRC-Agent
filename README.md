# GRC Agent
uv run python scripts/clean_cache.py
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
    `OPENROUTER_MODEL`) in a `.env` file at the repo root. Web search is
    enabled by default on OpenRouter via the `web` plugin (the model's
    answers are grounded with live results and cite sources). To disable,
    set `OPENROUTER_WEB_SEARCH=false`; tune with
    `OPENROUTER_WEB_SEARCH_MAX_RESULTS` (1–10) and
    `OPENROUTER_WEB_SEARCH_INCLUDE_DOMAINS`/`_EXCLUDE_DOMAINS` (CSV).
    Note: each grounded turn incurs OpenRouter credit charges.

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

## Exploratory experiments

The autonomous agent-flow experiment harness lives at
`tests/agent_flow/run_agent_flow.py` (tracked). It runs the full scenario
suite against a live model and writes Markdown transcripts + a metrics summary
(`metrics.json`, `METRICS.md`) to the gitignored `tests/output/agent_flow/`
(regenerated each run). The gated live test `tests/test_agent_flow_live.py`
asserts the per-scenario expect verdicts and refreshes the same metrics files.
These are read-only consumers of the agent — useful for smoke-testing the tool
surface, not part of the default CI gate.

Because the live eval is model-dependent and nondeterministic, engine
correctness is anchored deterministically by
`tests/test_agent_flow_engine_core.py` (`grc_native` marker): it applies each
scenario's intended `change_graph` batch directly (no model) and asserts the
`expect` predicate via a fresh native `validate()`. Run the live smoke with
`GRC_AGENT_LIVE_MODEL=1` (Ollama) and the retrieval tests with
`GRC_AGENT_LIVE_EMBED=1`.

See `docs/AGENT_RUNTIME.md` for the runtime architecture (execution loop,
loop-detection, context/compaction, session/integrity/rollback, tool dispatch,
retrieval).

---

## Example Prompts
- `Summarize this graph.`
- `Show parameters for analog_sig_source_x_0.`
- `Find a low-pass filter block.`
- `Change samp_rate to 48000 and validate.`
- `Change the signal source frequency from 440 to 1000.`
