# GRC Agent

Local companion for GNU Radio Companion (`.grc`) flowgraphs. GRC Agent helps you inspect, document, and edit your graphs using local LLMs.

Runs in your terminal (CLI) or as a sidekick window alongside GRC (GUI).

---

## Installation

### Prerequisites
- **Python >= 3.12**
- **GNU Radio 3.10.x** (with `grcc` on `PATH`)
- **llama.cpp** (with `llama-server` on `PATH`)

### Setup
```bash
git clone https://github.com/qoherent/grc-agent.git
cd grc-agent
uv venv --system-site-packages --python /usr/bin/python3
uv sync --locked --python .venv/bin/python
uv run grc-agent doctor
uv run grc-agent init
uv run grc-agent vector build
```

---

## Usage

### CLI — Terminal Chat
Start an interactive assistant session on a copied GRC graph:
```bash
uv run grc-agent chat path/to/your_copy.grc
```

Run a single-shot instruction:
```bash
uv run grc-agent chat path/to/your_copy.grc --message "Change samp_rate to 48000"
```

Interactive commands:
- `/save [path]` — Save current graph state
- `/history` — Show edit history
- `/quit` — Exit

### GUI — Desktop Panel
Launch the sidekick panel alongside GRC:
```bash
uv run grc-agent-gui path/to/your_copy.grc
```
Features include:
- Chat interface for graph modifications
- Live flowgraph inspector (variables, blocks, connections)
- Compile, run, and stop controls for testing on the fly
- Model selector dialog: discover local `.gguf` files in the Hugging Face cache, view system specs (GPU/VRAM/RAM/CPU), and live model swap that persists across sessions
- Local chat-session history: `File > Recent Sessions...` to browse and reopen past conversations, persisted in a local SQLite store

---

## Command Reference

```bash
uv run grc-agent doctor                         # Verify dependencies
uv run grc-agent health                         # Check LLM connection status
uv run grc-agent paths                          # Show data/config/preferences paths
uv run grc-agent vector search "audio sink"     # Semantic search in catalog
uv run grc-agent history list                   # List graph checkpoints
uv run grc-agent model list                     # List .gguf files in the local HF cache
uv run grc-agent model specs                    # Print local machine GPU/VRAM/RAM/CPU
uv run grc-agent sessions list                  # List local chat sessions
uv run grc-agent sessions show <id>            # Print one session's messages
uv run grc-agent sessions export <id> --out out.md  # Export a session
uv run grc-agent sessions gc --older-than-days 180  # Prune old sessions
uv run grc-agent debug-bundle                   # Generate support debug bundle
```

---

## Example Prompts
- `Summarize this graph.`
- `Show parameters for analog_sig_source_x_0.`
- `Find a low-pass filter block.`
- `Change samp_rate to 48000 and validate.`
- `Change the signal source frequency from 440 to 1000.`
