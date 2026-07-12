# Qoherent GRC Agent

A local companion for GNU Radio Companion (`.grc`) flowgraphs. It inspects,
edits, and documents your graphs through a chat agent, with vector search
over the full GNU Radio block catalog and docs wiki, and a browser-based GUI
alongside the chat.

See [`AGENTS.md`](AGENTS.md) for the architecture, engineering rules, and design decisions. This file covers install + run.

---

## Architecture at a glance

Everything lives in the installable `grc_agent` package (`src/grc_agent/`):

| File | Role |
|------|------|
| `adapter.py` | The **only** module that imports `gnuradio`. Flow-graph load/save, parameter/port filtering, block role classification, the `change_graph` mutation engine, catalog/docs vector RAG, `lite_web_search` (lite.duckduckgo.com scrape, the local fallback of pydantic-ai's `WebSearch` capability). |
| `agent.py` | Wires `adapter.py`'s functions into PydanticAI `Tool`s, defines the `WebSearch`/`WebFetch` capabilities, defines the system prompt, hosts the scenario harness used by integration tests. |
| `web.py` | Starlette app: proxies/rebrands `agent.to_web()`'s chat widget, serves the GNU Radio dashboard (`panel.html`) and its `/grc/*` JSON API, builds the chat `Agent`'s model from the saved provider/model preference. Also owns the broadwayd/`canvas_app.py` subprocess lifecycle. |
| `panel.html` / `panel.js` | The dashboard page and its logic — plain HTML/CSS/vanilla JS, no build step. |
| `canvas_app.py` | The live GTK canvas subprocess embedded in the dashboard via Broadway (GTK's HTML5 backend). |
| `ingest.py` | Builds the catalog/docs vector databases from scratch on first use. |
| `settings.py` | Persisted provider/model preference (`settings.json`, gitignored). |

Data flow: `.grc` file → `adapter.load_flow_graph()` → live
`gnuradio.grc.core.FlowGraph` → `inspect_graph()` → JSON tool result.

---

## Installation

### 1. Prerequisites
- **GNU Radio 3.10+** (with python bindings installed):
  ```bash
  sudo apt install gnuradio gnuradio-dev libgtk-3-bin  # Ubuntu/Debian
  ```
  `libgtk-3-bin` provides `broadwayd`, the GTK3 HTML5 backend the dashboard's
  live canvas embeds — a default `apt install` of `gnuradio` already pulls it
  in as a `Recommends`, but it's listed explicitly here since a
  `--no-install-recommends`/minimal setup would silently miss it.
- **Python >= 3.12** and **[uv](https://docs.astral.sh/uv/)**.

### 2. Clone & Setup
Clone the repository and sync the environment:
```bash
git clone https://github.com/qoherent/grc-agent.git
cd grc-agent
uv venv --system-site-packages --python 3.12
uv sync --extra dev --python .venv/bin/python
```
*(The `--system-site-packages` flag bridges your virtualenv directly to the system-installed GNU Radio).*

### 3. Setup LLM Backend
The agent supports Ollama (local) and OpenRouter (cloud). You can toggle them in the dashboard GUI at any time.

#### Option A: Ollama (Local & Free)
1. Install [Ollama](https://ollama.com/).
2. Pull the models you want to use:
   ```bash
   ollama pull qwen3.6:35b-a3b-q4_K_M   # Chat model
   ollama pull embeddinggemma:latest    # Embedding model
   ```

<details>
<summary>⚙️ Required: Increase Ollama Context Window (Click to expand)</summary>

Ollama's default context window is too small for multi-turn agent tool-calling. Increase it to `120000`:
- **Linux:** Run `sudo systemctl edit ollama`, add:
  ```ini
  [Service]
  Environment="OLLAMA_CONTEXT_LENGTH=120000"
  ```
  Then reload and restart:
  `sudo systemctl daemon-reload && sudo systemctl restart ollama`
- **macOS:** Run `launchctl setenv OLLAMA_CONTEXT_LENGTH 120000`, then quit and restart the Ollama app.
- **Windows:** Add `OLLAMA_CONTEXT_LENGTH` = `120000` to User Environment Variables, then restart Ollama.
</details>

#### Option B: OpenRouter (Cloud)
1. Get an API key at [OpenRouter](https://openrouter.ai/).
2. Copy `.env.example` to `.env` and add your key:
   ```bash
   cp .env.example .env
   ```

---

## Usage

### Launch the web GUI

```bash
uv run grc-agent-web
```

The script will automatically print the dashboard panel URL (**http://127.0.0.1:7932/grc/panel**) and open it in your default web browser. (Note: do not open the bare `/`, which is reserved for the chat widget's API and internal router). 

You can override host/port with the `GRC_AGENT_HOST`/`GRC_AGENT_PORT` env vars.

The app starts with no `.grc` file loaded — click **Browse** to pick one from
disk (the browser opens to your current working directory by default). Once
a conversation has a message in it, Browse locks until you start a new
conversation, so a single chat is never split across two flowgraphs.

**First run:** the catalog/docs vector databases don't ship with the
package — they're built automatically the first time `query_knowledge` runs,
which takes a few minutes (enumerating ~570 GNU Radio blocks and embedding
~100 docs pages). Subsequent runs read the cached `.db` files under
`src/grc_agent/vectors/` instantly. Switching embedding backends
(Ollama ↔ OpenRouter) builds a separate `.db` per backend automatically; if
you change `OLLAMA_EMBEDDING_MODEL`/`OPENROUTER_EMBEDDING_MODEL` for a backend
that already has a cached `.db`, delete that `.db` file manually to force a
rebuild.

**Model settings:** the dashboard's "Model" section lets you switch between
Ollama and OpenRouter at any time. The model name itself is click-to-edit —
click the current name to reveal an editable field, then confirm with the
checkmark (or cancel with the ✕) — and is otherwise plain, non-editable text
so it can't be changed by an accidental keystroke. A saved change is written
to a small config file immediately, but only takes effect after restarting
the app; a badge next to the model name stays visible whenever the saved
setting differs from what the running session actually loaded, so a pending
restart is never silently invisible.

**Undo/redo and validation:** the toolbar above the canvas has Undo/Redo
buttons and a Validate button. Undo/redo covers edits from either source —
the chat agent's `change_graph` tool calls and manual edits made directly in
the canvas (drag, properties dialog, context menu) — sharing one history, so
either side can undo the other's last change. Validate re-runs GNU Radio's
own native validation and refreshes the status pill on demand.

**Canvas behavior:** the canvas *is* directly editable — you can drag
blocks, double-click to open a block's properties, and use the right-click
context menu, same as native GNU Radio Companion. Every edit, from either
side, is written straight to the `.grc` file on disk (no unsaved/dirty
state to lose). Blocks the agent adds are positioned automatically, spaced
to avoid landing on top of existing ones — you never need to reposition
them yourself just to make them visible.

### Run the tests

```bash
uv run pytest tests/test_unit.py        # fast, no LLM
uv run pytest tests/test_web_app.py     # web endpoints, no LLM
uv run pytest tests/test_integration.py # live model, ~15-20 min
```

### Example prompts

- `Summarize this graph.`
- `Show parameters for analog_sig_source_x_0.`
- `Find a low-pass filter block.`
- `Change samp_rate to 48000 and validate.`
- `Change the signal source frequency from 440 to 1000.`
