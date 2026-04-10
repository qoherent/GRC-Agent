# GRC Agent

Local GNU Radio `.grc` assistant focused on safe, validated, local-first edits.

## Status

- one `.grc` file per session
- `FlowgraphSession` owns parsed state, persistence, validation, and all graph-mutation primitives
- `GrcAgent` intentionally exposes a smaller model-facing runtime than the full session surface
- the structural-edit surface is frozen pending new experiments
- a thin llama.cpp adapter is wired for one bounded CLI turn
- the supported llama.cpp slice is live-verified for summarize, `set_variable + validate_graph`, and missing-variable recovery
- raw model prose is still not trusted outside the runtime's deterministic finalization rules for supported flows

## Repo Map

- [src/grc_agent](src/grc_agent): session layer, model-facing runtime wrapper, and CLI entrypoint
- [grc_agent.toml](grc_agent.toml): repo-backed llama.cpp defaults for server URL, model id, and bounded turn settings
- [docs/PACKAGE_GUIDE.md](docs/PACKAGE_GUIDE.md): concise script-by-script map of the Python package
- [tests](tests): focused `unittest` regression coverage
- [tests/data/random_bit_generator.grc](tests/data/random_bit_generator.grc): canonical fixture flowgraph
- [docs/BLUEPRINT.md](docs/BLUEPRINT.md): architecture, settled decisions, evidence, milestones, and backlog

## Planning Rule

- do not assume GNU Radio behavior from YAML shape or memory alone
- read the relevant GNU docs first, then verify with real `.grc` and `grcc` runs
- widen the supported contract only after the evidence is written down in [docs/BLUEPRINT.md](docs/BLUEPRINT.md)

## Model-Facing Runtime

The model-facing runtime is intentionally narrower than the session layer.

- `summarize_graph`: report the current graph shape
- `set_variable(instance_name, value)`: update only a `variable` block's `value` parameter through `FlowgraphSession`
- `validate_graph`: run `grcc` validation on the current in-memory graph
- `save_graph(path=None)`: persist the current graph, but only after the latest dirty state has passed validation

The broader `FlowgraphSession` mutation methods remain available for direct code paths and regression tests, but they are not part of the model tool contract.

## Optional llama.cpp Spike

The repo now includes a thin llama.cpp adapter that calls only documented server endpoints.

- default llama runtime values are loaded from [grc_agent.toml](grc_agent.toml)
- endpoints used: `/health`, `/v1/models`, `/v1/chat/completions`
- llama.cpp built-in `/tools` is intentionally not used
- the runtime tool surface stays fixed to the same four tools
- the runtime is bounded by tool rounds, with `--max-steps` defaulting to `2`
- the default model id is the server alias, currently `unsloth/gemma-4-E2B-it-GGUF`
- the default `max_tokens = 12000` is an operational ceiling, not the correctness guard
- summarize final answers are resolved from the `summarize_graph` tool payload
- supported mutation final answers are resolved from tool results after `set_variable` and `validate_graph`
- raw tool-call-like text is not surfaced as the final answer when no tools actually ran

Start the local server with the configured model:

```bash
llama-server -hf unsloth/gemma-4-E2B-it-GGUF:Q4_K_M \
	--alias unsloth/gemma-4-E2B-it-GGUF \
	--host 127.0.0.1 \
	--port 8080 \
	--jinja
```

`--jinja` is explicit for reproducibility, but current `llama-server` enables it by default.

Then run the bounded CLI path:

```bash
uv run python -m grc_agent.cli tests/data/random_bit_generator.grc \
	--message "Change samp_rate to 48000 and validate the graph."
```

Use `--model`, `--llama-server-url`, or `--max-steps` only when you want a one-off override of the repo config.
The CLI now verifies that `/v1/models` returns exactly one entry and that the returned `id` matches the configured alias before the first chat request.
The final non-tool assistant answer is allowed after the configured tool-round budget is exhausted.
For the current supported slice, correctness comes from the bounded runtime contract, not from trusting the model's free-form final prose.

## Verification

Use the package entrypoint directly:

```bash
uv run python scripts/check_env.py
uv run ruff check
uv run python -m unittest
uv run python -m grc_agent.cli --fake tests/data/random_bit_generator.grc
```

What to expect:

- `check_env.py` passes Python, `grcc`, and GNU Radio version checks
- `ruff check` is clean
- `python -m unittest` passes the current regression suite
- the `--fake` CLI path routes a deterministic tool sequence through `GrcAgent` and `FlowgraphSession`
- the adapter tests exercise a scripted llama.cpp-compatible server while still validating the fixture graph with real `grcc`
- live llama.cpp checks are env-gated:
  ```bash
  GRC_AGENT_LIVE_LLAMA_URL=http://127.0.0.1:8080 \
  GRC_AGENT_LIVE_LLAMA_MODEL=unsloth/gemma-4-E2B-it-GGUF \
  uv run python -m unittest tests.test_llama_server_live
  ```
- the non-gating reliability matrix is:
  ```bash
  GRC_AGENT_LIVE_LLAMA_URL=http://127.0.0.1:8080 \
  GRC_AGENT_LIVE_LLAMA_MODEL=unsloth/gemma-4-E2B-it-GGUF \
  uv run python scripts/llama_reliability_matrix.py
  ```
- the supported live cases are summarize, `set_variable + validate_graph`, and missing-variable recovery
- GitHub Actions repeats the fast lint gate and a GNU-backed validation job on Ubuntu

## Safety Rules

- the model never edits raw `.grc` YAML directly
- all meaningful mutations flow through `FlowgraphSession`
- the runtime save path is blocked until the current dirty state has passed validation
- structural APIs only widen when a new experiment pass justifies them
- final graph validity is established explicitly through `validate()`

See [docs/BLUEPRINT.md](docs/BLUEPRINT.md) for the settled structural boundary, runtime decision, condensed experiment evidence, milestones, and backlog.
