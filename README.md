# Qoherent GRC Agent

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

An autonomous AI agent for GNU Radio Companion. It reasons over your `.grc`
flowgraph, edits it through validated tool calls, and grounds every answer in
a RAG-searchable GNU Radio block catalog and docs wiki — chatting alongside a
live, directly-editable canvas.

See [`AGENTS.md`](AGENTS.md) for architecture and design decisions — this file covers install + run.

---

## Architecture at a glance

Everything lives in the installable `grc_agent` package (`src/grc_agent/`):

| File | Role |
|------|------|
| `adapter.py` | Sole `gnuradio` import. Flowgraph load/save, param/port filtering, the `change_graph` mutation engine, catalog/docs vector RAG. |
| `agent.py` | Wires `adapter.py` into PydanticAI `Tool`s, defines the agent's system prompt and `WebSearch`/`WebFetch` capabilities. |
| `web.py` | Starlette app: chat API, GNU Radio dashboard (`panel.html`), `/grc/*` JSON API, broadwayd/canvas subprocess lifecycle. |
| `panel.html` / `panel.js` | The dashboard UI — plain HTML/CSS/vanilla JS, no build step. |
| `canvas_app.py` | The live GTK canvas subprocess, embedded via Broadway (GTK's HTML5 backend). |
| `ingest.py` | Builds the catalog/docs vector databases on first use. |
| `settings.py` | Persisted chat-agent preferences (provider, models, API keys) — all in `.env`. |

Data flow: `.grc` file → `adapter.load_flow_graph()` → live `FlowGraph` → `inspect_graph()` → JSON tool result.

---

## Installation

### 1. Prerequisites
- **GNU Radio 3.10+** with Python bindings:
  ```bash
  sudo apt install gnuradio gnuradio-dev libgtk-3-bin  # Ubuntu/Debian
  ```
  `libgtk-3-bin` provides `broadwayd` (the dashboard's live-canvas backend) —
  explicit here since a minimal/`--no-install-recommends` setup can miss it.
- **Python >= 3.12** and **[uv](https://docs.astral.sh/uv/)**.

### 2. Clone & Setup
```bash
git clone https://github.com/qoherent/grc-agent.git
cd grc-agent
uv venv --system-site-packages --python 3.12
uv sync --extra dev --python .venv/bin/python
```
`--system-site-packages` bridges the venv to your system-installed GNU Radio.

### 3. Setup LLM Backend
Three chat providers, switchable anytime from the dashboard GUI. The active
provider and model names persist in `.env` (restart the app to apply a change).

#### Option A: Ollama (Local & Free)
```bash
ollama pull qwen3.6:35b-a3b-q4_K_M   # chat model
ollama pull embeddinggemma:latest    # embedding model
```

<details>
<summary>⚙️ Required: increase Ollama's context window (click to expand)</summary>

Ollama's default context window is too small for multi-turn tool-calling.
Set it to `120000`:
- **Linux:** `sudo systemctl edit ollama`, add under `[Service]`:
  `Environment="OLLAMA_CONTEXT_LENGTH=120000"`, then
  `sudo systemctl daemon-reload && sudo systemctl restart ollama`.
- **macOS:** `launchctl setenv OLLAMA_CONTEXT_LENGTH 120000`, then restart the Ollama app.
- **Windows:** add `OLLAMA_CONTEXT_LENGTH` = `120000` to User Environment Variables, then restart Ollama.
</details>

#### Option B: OpenRouter (Cloud)
Get a key at [OpenRouter](https://openrouter.ai/), then `cp .env.example .env`
and add it (or set it from the dashboard's model selector).

#### Option C: Ollama Cloud (Cloud)
Get a key at [Ollama Cloud](https://ollama.cloud), then `cp .env.example .env`
and set `OLLAMA_CLOUD_API_KEY` (or set it from the dashboard).

> Even under Ollama Cloud/OpenRouter, `query_knowledge`'s vector search always
> uses your **local** Ollama for embeddings — keep `ollama serve` running with
> `embeddinggemma:latest` pulled.

---

## Usage

### Launch the web GUI
```bash
uv run grc-agent-web
```
Opens the dashboard at **http://127.0.0.1:7932/grc/panel**. Override the
host/port with `GRC_AGENT_HOST`/`GRC_AGENT_PORT`.

Click **Browse** to load a `.grc` file. Once a conversation starts, Browse
locks until you start a new one, so a chat never spans two flowgraphs.

- **First run** builds the catalog/docs vector databases (a few minutes,
  needs a reachable embeddings backend — see Option A). Cached afterward, and
  rebuilt automatically if the embedding model or source data changes.
- **Model settings:** switch provider/model anytime from the dashboard;
  changes write to `.env` and need a restart (a badge flags a pending one).
- **Undo/redo & validation:** one shared history across the agent's edits and
  manual canvas edits. Validate re-runs GNU Radio's own native validation.
- **Canvas:** fully editable — drag, double-click for properties, right-click
  menu — same as native GRC. Every edit writes straight to the `.grc` file.
  New blocks the agent adds are placed automatically, clear of existing ones.

### Run the tests
```bash
uv run playwright install chromium      # one-time, for test_frontend.py
uv run pytest tests/test_unit.py        # fast, no LLM
uv run pytest tests/test_web_app.py     # web endpoints, no LLM
uv run pytest tests/test_frontend.py    # dashboard UI, real Chromium, no LLM
uv run pytest tests/test_integration.py # live model, ~15-20 min
```

### Example prompts
- `Summarize this graph.`
- `Show parameters for analog_sig_source_x_0.`
- `Find a low-pass filter block.`
- `Change samp_rate to 48000 and validate.`
- `Change the signal source frequency from 440 to 1000.`

---

## More docs

- [`AGENTS.md`](AGENTS.md) — architecture, engineering rules, and live-verified design decisions.
- [`docs/technical_overview.md`](docs/technical_overview.md) — a deeper architecture writeup with diagrams and benchmarks.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, test gate, conventions.
- [`SECURITY.md`](SECURITY.md) — how to report a vulnerability.
- [`LICENSE`](LICENSE) / [`NOTICE.md`](NOTICE.md) — MIT-licensed; the bundled GNU Radio docs corpus is CC BY-SA 3.0.
