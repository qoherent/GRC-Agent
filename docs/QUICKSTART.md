# Quickstart

## Install

```bash
# Clone and enter the project
git clone <repo-url> && cd GRC_Agent

# Install dependencies (requires Python >= 3.12, uv)
uv sync
```

## Prerequisites

- Python >= 3.12
- GNU Radio 3.10+ with `grcc` on PATH
- llama.cpp server with a compatible GGUF model

## Check your environment

```bash
uv run grc-agent doctor
```

This reports Python version, grcc availability, GNU Radio version, config file,
catalog readiness, and llama.cpp server connectivity. Fix anything that shows FAIL.

## Open an existing graph

```bash
uv run grc-agent chat path/to/graph.grc
```

For a single query without the REPL:

```bash
uv run grc-agent chat path/to/graph.grc --message "Summarize this graph."
```

## Ask for a summary

```
>>> Summarize this flowgraph.
```

The agent calls `summarize_graph` and returns block counts, connection counts,
and a text description.

## Make a safe edit and validate

```
>>> Change the sample rate to 48000 and validate.
```

The agent calls `apply_edit` to change the parameter, then `validate_graph` to
confirm the graph still compiles with `grcc`.

## Save a copy

```
>>> Save a copy to /tmp/my_graph.grc
```

The agent calls `save_graph(path="/tmp/my_graph.grc")`. You must validate
before saving if the graph has been edited.

## Create a new graph

```bash
uv run grc-agent chat --new
```

```
>>> Create a minimal flowgraph with a signal source, throttle, and time sink.
     Validate it and save to /tmp/test.grc.
```

Note: new graphs have no file path. When saving, you must provide an explicit
path: `save_graph(path="/tmp/test.grc")`.

## Direct tool execution (no model)

```bash
# Summarize a graph without the model
uv run grc-agent tool summarize_graph --file path/to/graph.grc

# Validate a graph
uv run grc-agent tool validate_graph --file path/to/graph.grc
```

## What the agent will refuse

- **Raw YAML editing.** Direct `.grc` YAML editing is blocked. Use `apply_edit`
  or `propose_edit` instead.
- **Saving without validation.** A dirty graph must pass `validate_graph` before
  `save_graph` will write to disk.
- **Saving new graphs without a path.** New graphs (created with `--new` or
  `new_grc`) require an explicit save path.
- **Invalid edits.** Type-mismatched connections, disconnected stream blocks,
  and other `grcc`-invalid mutations are rejected before committing.

## Known limitations

1. The 2B model may choose incompatible blocks for insertion tasks (e.g., hardware-specific blocks like UHD RFNoC).
2. The 2B model may ask for clarification instead of acting on open-ended requests.
3. Duplicate instance names are safely rejected; disambiguation by block type is not yet supported.
4. `SAVE_PATH_REQUIRED` after `new_grc` may require you to specify the path manually.
5. Expert GNU/DSP knowledge depends on the backend model quality.

## Vision / mmproj policy

GRC Agent is **text-only**. Do not load multimodal projector files (`mmproj-BF16.gguf`) during normal use.

Reason:
- No image input is used
- `mmproj` consumes VRAM / RAM
- It does not improve `.grc` editing or tool calling
- It increases startup / resource risk

**Recommended launch (projector disabled):**

```bash
llama-server \
  -hf unsloth/gemma-4-E2B-it-GGUF \
  -hff gemma-4-E2B-it-UD-Q4_K_XL.gguf \
  --alias unsloth/gemma-4-E2B-it-GGUF \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 131072 \
  --jinja \
  --no-mmproj
```

**Local file form (no mmproj loaded):**

```bash
llama-server \
  -m /path/to/gemma-4-E2B-it-UD-Q4_K_XL.gguf \
  --alias unsloth/gemma-4-E2B-it-GGUF \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 131072 \
  --jinja
```

When using local `-m`, do not pass `--mmproj`. The model runs text-only automatically.

## Configuration

Create `grc_agent.toml` in your project root or `~/.config/grc_agent/`:

```toml
[llama]
server_url = "http://127.0.0.1:8080"
model = "your-model-alias"
temperature = 1.0
```
