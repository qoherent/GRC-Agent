# Quickstart

## Install

```bash
uv sync
uv run grc-agent doctor
```

Prerequisites:

- Python >= 3.12
- GNU Radio 3.10+ with `grcc` on `PATH`
- llama.cpp configured for model-backed chat

## Developer Checks

```bash
uv run ruff check src/ tests/
uv run python -m unittest
```

## Open A Graph

```bash
uv run grc-agent chat path/to/graph.grc
```

Single prompt:

```bash
uv run grc-agent chat path/to/graph.grc --message "Summarize this graph."
```

## Common Prompts

Summarize:

```text
Summarize this flowgraph.
```

Edit and validate:

```text
Change samp_rate to 48000 and validate.
```

Preview without mutation:

```text
Preview changing samp_rate to 64000 before touching anything.
```

Save a copy:

```text
Save a copy to /tmp/my_graph.grc.
```

## Create A New Graph

```bash
uv run grc-agent chat --new
```

```text
Create a minimal flowgraph with a signal source, throttle, and null sink. Validate it and save to /tmp/test.grc.
```

New graphs have no file path. Saving a new graph requires an explicit destination path.

## Direct Tool Execution

```bash
uv run grc-agent tool summarize_graph --file path/to/graph.grc
uv run grc-agent tool validate_graph --file path/to/graph.grc
```

## Refusals

The agent refuses or safely rejects:

- Raw `.grc` YAML editing.
- Save requests on dirty graphs before validation.
- Save requests for new graphs without a path.
- Invalid edits that fail preflight or `grcc` validation.
- Undo/redo and Python export/code-generation requests.

## Local llama.cpp

Default repo config uses:

```toml
[llama]
server_url = "http://127.0.0.1:8080"
model = "unsloth/gemma-4-E2B-it-GGUF"
hf_model = "unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL"
```

Manual launch equivalent:

```bash
llama-server \
  -hf unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL \
  --alias unsloth/gemma-4-E2B-it-GGUF \
  --host 127.0.0.1 \
  --port 8080 \
  --jinja \
  --no-mmproj
```

The project is text-only; multimodal projector files are not useful for `.grc` editing.
