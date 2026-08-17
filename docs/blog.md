# GRC Agent: An AI That Builds and Fixes GNU Radio Flowgraphs for You

**Qoherent's GRC Agent** is an autonomous AI assistant that lives *inside* GNU Radio Companion. It reads your `.grc` flowgraph, edits it through validated tool calls, and grounds every answer in a RAG-searchable GNU Radio block catalog and docs wiki — all in one native desktop window, sharing the same live `FlowGraph` object as your canvas. No web server. No subprocess. No copy-pasting code between tools.

![Screenshot of GRC Agent with chat sidebar and canvas](docs/screenshot.png)

---

## What It Does

Ask in plain English, watch the canvas redraw. The agent inspects the graph, plans a mutation, and applies it in a single transactional edit — then GRC's own validator checks the result and the canvas scrolls to what changed. Because the agent and canvas share one thread and one `FlowGraph`, every edit is instant and consistent.

### Six Tools, One Loop

| Tool | What It Does |
|------|-------------|
| `inspect_graph` | Reads blocks, params, connections, ports — pruned to a clean semantic JSON so the model reasons over signal topology, not canvas coordinates |
| `query_knowledge` | Vector RAG over the GNU Radio catalog + docs wiki, with automatic lexical fallback when embeddings are unreachable |
| `generate_python` | Previews the exact Python GRC would generate — in-memory, zero disk I/O |
| `change_graph` | Adds, removes, and rewires blocks in a 7-phase transactional mutation with rollback and native validation |
| `get_run_log` | Reads the last execution's stdout/stderr on demand |
| `save_block` | Exports a working Embedded Python Block into GNU Radio's own reusable block library, for any future flowgraph — not an out-of-tree module |

---

## Why It Feels Like Magic

### 🧠 A Smart Execution Harness
The hard part isn't the LLM — it's the harness. When the agent adds a block, GRC Agent runs GNU Radio's own type resolution, `"auto"` dtype propagation, and a 7-phase transactional mutation with full rollback. Every add relays out the whole canvas — variables and config blocks pack into a tidy header row up top, everything else flows left-to-right below by signal order — because a model shouldn't have to think in pixel coordinates. If validation fails, changes roll back and the agent sees the exact compiler feedback to self-correct — up to 3 attempts, then it stops.

### 🔗 Block Tagging & Canvas Highlighting
Every block the agent mentions becomes a clickable tag in chat. Click it — the canvas scrolls to and highlights that block. No hunting through a crowded flowgraph.

### 📋 Log Reading & Auto-Fix on Error
Run your flowgraph from GRC's toolbar. If it fails, the agent is **automatically notified** with the return code, reads the full log via `get_run_log`, diagnoses the failure, and proposes a fix — then it edits the graph and re-validates. The whole loop runs with zero manual copy-paste.

### 🔄 Multiple Providers, Live-Swappable
Choose **Ollama** (local or cloud), any **OpenAI-compatible** endpoint (OpenRouter, llama.cpp, vLLM, …), or **ChatGPT Plus/Pro (Codex)** with OAuth sign-in from the Settings dialog. Switch mid-session; changes apply immediately with no restart, and chat history is preserved.

---

## Getting Started

```bash
# Prerequisites: GNU Radio 3.10, Python 3.12-3.14, uv
# sudo apt install gnuradio python3-gi python3-gi-cairo
git clone https://github.com/qoherent/grc-agent.git
cd grc-agent
uv venv --system-site-packages --python /usr/bin/python3
uv sync --extra dev --locked --python .venv/bin/python
uv run grc-agent
```

Add an Ollama model or a cloud API key from Settings, open a `.grc`, and start chatting.

---

GRC Agent is **AGPL-3.0** open source. [GitHub →](https://github.com/qoherent/grc-agent)
