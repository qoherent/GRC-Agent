# GRC Agent CLI — Quickstart

The CLI is the terminal-first way to use GRC Agent. It runs an interactive chat
session against a copied `.grc` flowgraph, powered by a local llama.cpp model.
The model inspects, searches, and mutates the graph through a four-wrapper
contract: `inspect_graph`, `search_blocks`, `ask_grc_docs`, `change_graph`.

For the desktop sidekick panel, see `docs/GUI_QUICKSTART.md`.

## Prerequisites

- Python >= 3.12
- GNU Radio 3.10.x with `grcc` on `PATH`
- `llama-server` (CUDA-enabled for NVIDIA machines; `grc-agent doctor --start-llama` will start it for you)

## Install

```bash
uv sync --locked
uv run grc-agent doctor
```

If GNU Radio is installed by the OS package manager and the uv venv cannot
import it, recreate the venv with system site-packages:

```bash
rm -rf .venv
uv venv --system-site-packages --python /usr/bin/python3
uv sync --locked --python .venv/bin/python
uv run grc-agent doctor
```

## Open a graph

```bash
mkdir -p playground/grc_agent_interactive
cp /usr/share/gnuradio/examples/audio/dial_tone.grc \
  playground/grc_agent_interactive/dial_tone_interactive.grc

uv run grc-agent chat playground/grc_agent_interactive/dial_tone_interactive.grc
```

The CLI copies installed example graphs into your workspace first; it never
mutates originals. After the first `chat` command, the copied graph is your
active session and all mutations autosave to it.

## Try a few prompts

```text
Summarize this graph.
What are the variables and connections?
Change the signal source frequency from 440 to 10k.
Add a low-pass filter at 5k and connect it to the audio sink.
Find a quadrature demod block and show its parameters.
```

For exploratory tool-heavy turns, raise the bounded tool-round budget:

```bash
uv run grc-agent chat --agentic playground/grc_agent_interactive/dial_tone_interactive.grc
```

For a single-shot programmatic turn with JSON output:

```bash
echo "Change samp_rate to 48000 and validate the graph." \
  | uv run grc-agent chat playground/grc_agent_interactive/dial_tone_interactive.grc --stdin --json
```

## Chat commands

- `/save [path] [--overwrite]` — manual save to a path
- `/history` — show the recent turns (off by default; not raw model trace)
- `/quit` — exit the session

## Safety

Mutations are routed through `change_graph` and only commit after schema,
graph-reference, catalog, preflight, apply, and `grcc` validation pass.
Autosave writes the active copied graph when safe. Failed validation rolls
back atomically. The model cannot edit raw YAML, save or load directly, or
silently retry with hidden repair paths.

If the model cannot uniquely identify a target, it asks a clarification
question instead of guessing.

## When to use the CLI

- You live in the terminal.
- You want scripted / piped / JSON one-shot runs.
- You want a clear session per file with explicit `/save`.
- You want to stay in the chat/mutation plane while GRC stays untouched.

For a graphical view of variables, blocks, and connections alongside
mutation, see `docs/GUI_QUICKSTART.md`.

## Useful CLI commands

```bash
uv run grc-agent chat <copy.grc>                # interactive
uv run grc-agent chat --new                      # create a new graph
uv run grc-agent chat --agentic <copy.grc>       # higher tool-round budget
uv run grc-agent doctor                          # environment check
uv run grc-agent doctor --start-llama            # start llama.cpp + check
uv run grc-agent health                          # health probe
uv run grc-agent tool summarize_graph --file X   # one tool, no model
uv run grc-agent tool validate_graph --file X
uv run grc-agent history list                    # local checkpoints
uv run grc-agent history show <id>
uv run grc-agent history diff <id1> <id2>
uv run grc-agent history restore <id> --to /tmp/r.grc
uv run grc-agent debug-bundle --output /tmp/bundle.json
```
