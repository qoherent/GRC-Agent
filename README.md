# Qoherent GRC Agent

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

An AI agent for GNU Radio Companion, built into a native desktop app. It
works directly on your live `.grc` flowgraph: reads it structurally, edits
it through validated tool calls, answers grounded in GNU Radio documentation,
and helps debug runs — all in a chat sidebar next to the canvas you already
use.

![GRC Agent User Interface](docs/screenshot.png)

---

## What it does

**Flowgraph editing.** Inspect blocks, connections, and parameters; add,
remove, rewire, and re-parameterize blocks; every change is validated,
rolled back on failure, and drawn on the canvas immediately. GRC's native
undo/redo (Ctrl+Z/Y) keeps working. What the agent changed is what you see —
no reload, no export/import.

**Reading your project.** The agent reads any file in the flowgraph's folder
(Python, C/C++, txt, markdown, MATLAB, JSON, YAML, XML, …). `.grc` files are
never dumped as raw XML — they pass through the same structural inspection
used on the active graph, so the agent understands them, not just reads them.

**Writing your project.** The agent creates and edits source/config files
(the usual suspects plus CMake — ready for out-of-tree module work), with
atomic saves and conflict detection. Flowgraph files themselves are
read-only to it on purpose: graphs are only ever edited through the
validated graph tools.

**Grounded knowledge.** Block IDs, port names, parameter keys, and concepts
are answered from a searchable GNU Radio catalog and docs wiki — the agent
checks the docs instead of guessing from memory, and falls back to web
search for what's not covered.

**Runtime debugging.** When a flowgraph run fails, the agent is notified
with the return code, reads the full console log itself, and diagnoses the
error. It can also render the exact Python code GNU Radio would generate
from the current graph — read-only, nothing written to disk.

**Saving reusable blocks.** A working Embedded Python Block can be exported
into GRC's block library (`~/.grc_gnuradio`) as a standalone, reusable
catalog block.

**Context management.** Long conversations are handled automatically:
bulky old tool results are cleared, aging turns are summarized (your
messages always preserved), and the target size is derived from the model's
real context window, probed from the backend. A manual compact button is
also there when you want it.

**Safety.** Every tool result is scanned for indirect prompt injection — a
malicious instruction planted in a project file or web page is withheld
before it reaches the model. The agent's file access is sandboxed to your
project folder, and secrets (`.env`, `.envrc`, `.git/`) are off-limits.

**Chat sessions.** Persisted with full history, resumable, and searchable.
Switch sessions or start new ones from the sidebar.

## Supported LLM providers

Pick any of these in Settings; model and API key apply immediately — no
restart. The model list can be loaded live from the provider, so you never
guess an id.

- Ollama — local or LAN
- Ollama Cloud
- OpenRouter
- OpenAI
- Any OpenAI-compatible endpoint (llama.cpp, vLLM, LM Studio, …)
- Anthropic (Claude)
- Google (Gemini)
- Groq
- Mistral
- Cohere
- xAI (Grok)
- ChatGPT Plus/Pro — sign in with your ChatGPT account, no API key

## Knowledge base search

Out of the box, search runs on **Lexical** (SQLite FTS5/BM25 keyword
search) — zero extra downloads, nothing running in the background.

Optionally, install **Local Vector Search** with one click from Settings:
a pinned llama.cpp + EmbeddingGemma runtime (~345 MB) is downloaded into
`~/.local/share/grc-agent` and served over a private local socket. Nothing
is installed system-wide. If it's ever unreachable, search falls back to
lexical automatically with a notice.

## Installation

### Prerequisites
- **[GNU Radio 3.10](https://wiki.gnuradio.org/index.php?title=InstallingGR)**
  with Python bindings (CI covers Ubuntu 24.04 and 26.04; other 3.10.x is
  likely fine).
- **Python 3.12–3.14** and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**.
- GTK bindings from your distro, not PyPI:
  ```bash
  sudo apt install gnuradio python3-gi python3-gi-cairo
  ```

### Setup
```bash
git clone https://github.com/qoherent/grc-agent.git
cd grc-agent
uv venv --system-site-packages --python /usr/bin/python3
uv sync --extra dev --locked --python .venv/bin/python
```

`--system-site-packages` bridges the venv to your system GNU Radio. Use
`/usr/bin/python3` explicitly — the GNU Radio bindings are compiled against
your system interpreter's ABI, so a uv-managed or pyenv/conda Python will
fail to `import gnuradio` even with the bridge.

Local Ollama note: the default context window is too small for multi-turn
tool-calling. Set `OLLAMA_CONTEXT_LENGTH=120000` and restart the daemon
(Linux: `sudo systemctl edit ollama`, add it under `[Service]`).

### Run
```bash
uv run grc-agent
```
A native GTK3 window opens: GRC's canvas on the left, the chat sidebar on
the right. Open `.grc` files from GRC's File menu — the agent follows the
active tab automatically.

- Zoom: Ctrl+/- and Ctrl+0. Block library: the slim arrow on the sidebar's
  left edge. Run/Stop and validation: GRC's own toolbar.

## Example prompts

- `Summarize this graph.`
- `Show parameters for analog_sig_source_x_0.`
- `Find a low-pass filter block.`
- `Change samp_rate to 48000 and validate.`
- `Change the signal source frequency from 440 to 1000.`
- `Read helper.py in this flowgraph's folder and explain what it does.`
- `The run failed — read the log and tell me what's wrong.`

## Tests

```bash
uv run pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py  # fast, no LLM
uv run pytest tests/test_button_integration.py  # tool integration, needs Ollama Cloud key
uv run pytest tests/test_integration.py         # live model scenarios, ~15-20 min
uv run ruff check                               # lint
```
Sidebar/canvas tests need a display: `xvfb-run -a uv run pytest …`.

## More docs

- [`AGENTS.md`](AGENTS.md) — architecture, engineering rules, and live-verified design decisions.
- [`docs/technical_overview.md`](docs/technical_overview.md) — deeper architecture writeup with diagrams and benchmarks.
- [`LICENSE`](LICENSE) / [`NOTICE.md`](NOTICE.md) — AGPL-3.0; the bundled GNU Radio docs corpus is CC BY-SA 3.0.
