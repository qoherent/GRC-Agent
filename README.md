# GRC Agent

Local companion for GNU Radio Companion (`.grc`) flowgraphs. GRC Agent helps
you inspect, document, and edit your graphs using a local or cloud LLM, with
vector search over the full GNU Radio block catalog and docs wiki.

Runs as a sidekick window alongside GRC (GUI).

---

## Installation

GRC Agent needs three things on your machine: **GNU Radio**, an **LLM backend**
(Ollama and/or OpenRouter), and **Python + uv** for the app itself.

### 1. GNU Radio

Install GNU Radio 3.10+ (with `grcc` on `PATH`) by following the official
guide: <https://wiki.gnuradio.org/index.php?title=InstallingGR>.

On Debian/Ubuntu a system install is enough:

```bash
sudo apt install gnuradio gnuradio-dev
```

The app imports `gnuradio` from the system, so the virtualenv below is created
with `--system-site-packages`.

### 2. LLM backend (Ollama and/or OpenRouter)

Pick one — or set up both and switch between them live in the GUI.

- **Ollama (local, default):** install from <https://ollama.com/>, then pull a
  chat model and an embedding model:
  ```bash
  ollama pull gemma4:e4b-it-qat-120k     # chat (tool-calling capable)
  ollama pull embeddinggemma:latest       # embeddings
  ```
- **OpenRouter (cloud):** create a key at <https://openrouter.ai/> and put it
  in `.env` (see step 4). No local Ollama is required in OpenRouter mode —
  chat **and** embeddings go through OpenRouter.

### 3. Python + uv

Requires **Python ≥ 3.12** and [uv](https://docs.astral.sh/uv/) (install with
`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
git clone https://github.com/qoherent/grc-agent.git
cd grc-agent
uv venv --system-site-packages --python 3.12
uv sync --extra gui --locked --python .venv/bin/python
```

`--system-site-packages` is what makes the system-installed `gnuradio` visible
inside the isolated venv.

### 4. Configure models + keys

Copy the template and edit it (or just launch the GUI and use the toolbar):

```bash
cp .env.example .env
$EDITOR .env
```

`.env` is the **single source of truth for model names and API keys** for both
backends:

| Variable | Backend | Default |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenRouter | _(required on OpenRouter)_ |
| `OPENROUTER_MODEL` | OpenRouter | `deepseek/deepseek-v4-flash` |
| `OPENROUTER_EMBEDDING_MODEL` | OpenRouter | `perplexity/pplx-embed-v1-0.6b` |
| `OLLAMA_API_KEY` | Ollama | _(only for hosted web tools)_ |
| `OLLAMA_MODEL` | Ollama | `gemma4:e4b-it-qat-120k` |
| `OLLAMA_EMBEDDING_MODEL` | Ollama | `embeddinggemma:latest` |

The active backend is set in `grc_agent.toml` (`[llama].backend`) or picked in
the GUI; chat and embeddings always ride the same backend. The GUI writes model
changes back to `.env`, so hand-edits and GUI-edits stay in sync (hand-edits
apply on the next launch).

A starter `grc_agent.toml` lives in the repo root; user overrides go to
`~/.config/grc_agent/config.toml`.

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
- **Inline model toolbar:** pick Ollama (local) or OpenRouter (cloud), select a
  chat model and an embedding model, and live-swap the backend without
  restarting. Selections persist across sessions.
- Local chat-session history (`File > Recent Sessions...`)

If the backend is unreachable on launch, the GUI opens in **degraded mode**
(chat disabled; status bar reports the failure) instead of exiting — recover
via the inline model toolbar.

### Choosing a model

A chat model works with GRC Agent only if it meets **three criteria**:

1. **Tool/function calling** — the agent surfaces 5 tools; a model without
   function-calling is unusable here.
2. **Served by your backend** — any model Ollama exposes (`ollama list`) works,
   or any tool-calling model on OpenRouter.
3. **Adequate context window** for the graphs you edit.

Worked example — using Qwen3 27B locally:

```bash
ollama pull qwen3:27b        # pull it yourself, then set in .env / GUI
```

Then set `OLLAMA_MODEL=qwen3:27b` (or type it in the GUI's model field). For a
large context window on Ollama, bake `num_ctx` into a custom Modelfile —
Ollama's `/v1` endpoint ignores per-request `num_ctx`, so the window lives in
the model, not the request.

Embedding models are chosen the same way (`OLLAMA_EMBEDDING_MODEL` /
`OPENROUTER_EMBEDDING_MODEL`, editable in the GUI). Switching embedding models
is safe: the per-backend vector index rebuilds automatically on first use.

---

## Exploratory experiments

The autonomous agent-flow experiment harness lives at
`tests/agent_flow/run_agent_flow.py`. It runs the scenario suite against a live
model and writes Markdown transcripts + a metrics summary to the gitignored
`tests/output/agent_flow/` (regenerated each run). Engine correctness is anchored
deterministically by `tests/test_agent_flow_engine_core.py` (`grc_native`
marker). Run the live model smoke with `GRC_AGENT_LIVE_MODEL=1` and the live
embedding tests with `GRC_AGENT_LIVE_EMBED=1`.
---

## Example prompts

- `Summarize this graph.`
- `Show parameters for analog_sig_source_x_0.`
- `Find a low-pass filter block.`
- `Change samp_rate to 48000 and validate.`
- `Change the signal source frequency from 440 to 1000.`
