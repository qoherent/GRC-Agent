# 01 — Idea and Features
Science-agent input. No refs or citations here by design.

## Idea (3 sentences)
RIA GRC-Agent is an AI agent for GNU Radio Companion built into the native desktop app itself. It works directly on the live `.grc` flowgraph — reading blocks, connections, and parameters structurally, editing through validated tool calls, answering grounded in GNU Radio documentation, and debugging runs. All of this happens in a chat sidebar next to the canvas the user already uses (`README.md:5-9`).

## Who it is for
RF and SDR engineers, SDR testbed teams, educators teaching signal processing, and offline or air-gapped shops that need local retrieval and local model backends.

## Features

### Flowgraph: inspect and edit structurally
- Semantic read of blocks, connections, parameters, and validation state via `inspect_graph` (`src/grc_agent/adapter/graph.py`, `src/grc_agent/agent.py`).
- One transactional edit via `change_graph` (7 fixed phases, automatic rollback, native `validate()` gate).
- Topology changes relayout into a clean layered (Sugiyama-style) arrangement so multi-step edits do not degrade the canvas (`src/grc_agent/adapter/layout.py`).
- Canvas redraws immediately; GRC native undo/redo keeps working.

### Approvals: Manual / Auto / YOLO
- Every `change_graph` requires approval by default (Manual mode): one-line reason plus structured summary, Approve / Deny / Always accept (`src/grc_agent/chat/approvals.py`, `src/grc_agent/ui/approval_card.py`).
- Mode toggle: Auto (flowgraph changes apply without asking; shell still asks) and YOLO (nothing asks), back to Manual any time.

### Run, stop, and debug natively
- Agent triggers GRC native Execute/Stop (`gui_actions().FLOW_GRAPH_EXEC` / `FLOW_GRAPH_KILL`), never shell-executed top-block scripts.
- Bounded runs via `stop_after_seconds`; run failure returns return-code plus full retained log for the diagnose-and-retry loop (`src/grc_agent/exec_monitor.py`).
- Read-only `generate_python` preview through GRC `Generator` (no write, no run).

### Measure, plot, canvas evidence
- Unified `grc_interaction` tool, modes `start | stop | log | measure | plot | canvas` (`src/grc_agent/agent.py:628`); planner has read-only `get_run_log`.
- File Sink workflow: fresh output path, append off → run → seal on exit → read with graph/run/file identity, dtype/vlen, hash, and staleness checks (`src/grc_agent/observations.py`, `src/grc_agent/measurements.py`).
- `measure` returns numbers to the model and shows its plot to the user; `plot` also sends the image to a vision-capable model. Canvas images show topology only, never signal proof.

### Save and reuse
- `save_graph`: agent-side Ctrl+S; untitled graphs land in the project directory under a derived name, titled ones rewrite in place (atomic tmp + fsync + `os.replace` under `flock`).
- `save_block`: exports an Embedded Python Block into the native hier-block library (`~/.grc_gnuradio`) so it becomes a real catalog block (`src/grc_agent/adapter/block_library.py`).

### Sandboxed workstation
- Filesystem tools rooted in the project directory with symlink containment; `.env`, `.git`, `.grc_agent` denied; `.grc` reads reroute to inspection, direct `.grc` writes rejected (`src/grc_agent/fs_tools.py`).
- Shell tools project-rooted with denylist, API-key scrubbing, approval on `run_command` / `start_command`; `stop_command` ungated (`src/grc_agent/shell_tools.py`).
- Every tool and web result scanned by injection defense (flag + log, not silently withheld).

### Knowledge: catalog + docs
- `query_knowledge` over block schemas and locally indexed GNU Radio docs: SQLite FTS5 lexical default (zero-download) plus optional one-click local vector (EmbeddingGemma) fused by Reciprocal Rank Fusion (k=60), with embedded C++ SWIG docstrings (`src/grc_agent/adapter/rag.py`).
- Standard C++ catalog blocks (VOLK SIMD) by default; Embedded Python Blocks only for custom logic, with vectorized NumPy/SciPy `work()` enforced by prompt.

### Providers, sessions, cost
- Hot-swappable LLM backends without restart (Ollama local/LAN/Cloud, OpenRouter, OpenAI-compatible, Anthropic, Gemini, Groq, Mistral, Cohere, xAI, among others — avoid exact counts; the list grows).
- Sessions persist; long contexts compact via tiered strategies; live context/cost readout in the sidebar.
- Failure reporting is uniform: `ModelRetry` (model can fix by retrying: bad param, validation failure) vs `ToolFailed` (terminal: unwired monitor, missing capability).
