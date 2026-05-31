# Quickstart

## Install

```bash
uv sync --locked
uv run grc-agent doctor
```

Requires Python 3.12+, GNU Radio 3.10.x, and `grcc` on `PATH`.

If GNU Radio is installed through the OS and the uv venv cannot import it:

```bash
rm -rf .venv
uv venv --system-site-packages --python /usr/bin/python3
uv sync --locked --python .venv/bin/python
uv run grc-agent doctor
```

## Run Chat

Work on copied `.grc` files only.

```bash
mkdir -p playground/grc_agent_interactive
cp /usr/share/gnuradio/examples/audio/dial_tone.grc \
  playground/grc_agent_interactive/dial_tone_interactive.grc

uv run grc-agent chat playground/grc_agent_interactive/dial_tone_interactive.grc
```

For exploratory tool-heavy turns:

```bash
uv run grc-agent chat --agentic playground/grc_agent_interactive/dial_tone_interactive.grc
```

For programmatic single-shot execution with JSON output and stdin prompts:

```bash
echo "Change the sample rate to 48000" | uv run grc-agent chat playground/grc_agent_interactive/dial_tone_interactive.grc --stdin --json
```

## llama.cpp

Chat starts or reuses the configured local `llama-server` automatically, then verifies health/model/context. For explicit readiness:

```bash
uv run grc-agent doctor --start-llama
uv run grc-agent health
```

For native NVIDIA CUDA, make sure `llama-server --list-devices` shows `CUDA0`.
Gemma 4 GGUF repos also publish multimodal projector files; use
`[llama].model_path` for the local text `.gguf` when you want text-only startup
without downloading `mmproj` assets.

## Retrieval Index

Optional, for vector-backed retrieval:

```bash
uv run grc-agent vector build
uv run grc-agent vector stats --json
```

## Chat Commands

- `/save [path] [--overwrite]`
- `/history`
- `/quit`

## Useful Prompts

```text
Inspect this graph and summarize blocks, variables, and connections.
List parameters for the signal source blocks.
Change the signal source frequency from 440 to 10k.
Add a sine source at 1000 Hz and connect it to the adder.
```

## Safety

The model sees only `inspect_graph`, `search_blocks`, `ask_grc_docs`, and
`change_graph`. It cannot directly save/load, edit raw YAML, or use
shell/filesystem tools. Mutations go through schema checks, graph-reference
checks, preflight, validation, rollback, and autosave to the active copied
graph. If the user explicitly wants an invalid intermediate graph, the model may
use `force=true`; failed schema/preflight/reference checks still never commit.

Ambiguous edit requests should end in clarification, not a guessed mutation.
For example, if a graph has two matching sinks or two candidate message debug
blocks, the correct result is to ask which exact instance/connection to edit and
leave the copied graph unchanged.
