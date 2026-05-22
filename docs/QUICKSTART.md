# Quickstart

Use copied `.grc` files. Do not edit installed examples or originals directly.

## Install

```bash
uv sync --locked
uv run grc-agent doctor
uv run grc-agent health
```

Required outside this package:

- Python 3.12+
- GNU Radio 3.10.x with `grcc` on `PATH`
- CUDA-enabled `llama-server` from llama.cpp for model-backed chat on NVIDIA machines

If GNU Radio is installed by the OS and a normal uv venv cannot import it, use a system-site venv:

```bash
rm -rf .venv
uv venv --system-site-packages --python /usr/bin/python3
uv sync --locked --python .venv/bin/python
uv run grc-agent doctor
```

Build the local vector index when retrieval/runtime readiness needs it:

```bash
uv run grc-agent vector build
uv run grc-agent vector stats --json
```

## Start llama.cpp

CUDA path for the local NVIDIA runtime:

```bash
llama-server --list-devices

llama-server \
  -hf unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL \
  --alias unsloth/gemma-4-E2B-it-GGUF \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 120000 \
  --device CUDA0 \
  --gpu-layers 999 \
  --jinja \
  --no-mmproj
```

If `llama-server --list-devices` does not show `CUDA0`, the installed llama.cpp binary is not CUDA-enabled.

Verify:

```bash
uv run grc-agent health
```

Health must report reachable llama and verified context for end-to-end runtime readiness.

## Open A Copied Graph

```bash
mkdir -p playground/grc_agent_interactive
cp /usr/share/gnuradio/examples/audio/dial_tone.grc \
  playground/grc_agent_interactive/dial_tone_interactive.grc

uv run grc-agent chat playground/grc_agent_interactive/dial_tone_interactive.grc
```

Interactive commands:

- `/save [path] [--overwrite]`: deterministic save, bypasses the model
- `/history`: print debug conversation/tool history
- `/quit` or `/exit`: stop

Normal chat hides full history and prints concise operation summaries.

## Useful Prompts

Read-only:

```text
Inspect this graph and summarize blocks, variables, and connections.
```

Targeted parameter inspection:

```text
List parameters for the signal source blocks.
```

Safe natural parameter edit with old-value guard:

```text
Change the signal source frequency from 440 to 10k.
```

Exact variable edit:

```text
Set variable samp_rate to 48000.
```

Preview:

```text
Preview changing samp_rate to 48000. Do not apply it.
```

Save manually:

```text
/save playground/grc_agent_interactive/dial_tone_interactive_changed.grc
```

## Direct Tool Calls

`grc-agent tool` is a deterministic debug entrypoint for internal primitives, not
the model-facing wrapper surface. For normal use, start chat and let the model use
the six wrappers.

```bash
uv run grc-agent tool summarize_graph \
  --file playground/grc_agent_interactive/dial_tone_interactive.grc \
  --args '{}'

uv run grc-agent tool get_grc_context \
  --file playground/grc_agent_interactive/dial_tone_interactive.grc \
  --args '{"node_id":"analog_sig_source_x_1"}'
```

## Safety Expectations

The agent will:

- inspect the active graph
- expose mutation-ready handles during targeted inspection
- preview without mutation
- mutate only through `change_graph`
- validate committed graph edits
- save only when explicitly asked
- clarify ambiguous graph targets

The agent will not:

- edit raw YAML
- save implicitly
- mutate originals under installed example paths
- use docs/RAG as mutation authority
- pick the first ambiguous target
- claim production-ready

## Debug Bundle

For issue reports:

```bash
uv run grc-agent debug-bundle --output /tmp/grc_agent_debug_bundle.json
```

The bundle redacts secrets and includes health, doctor, release manifest, tool surface, vector state, and environment details.
