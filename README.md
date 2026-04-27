# GRC Agent

Local GNU Radio `.grc` assistant focused on safe, validated, local-first edits.

## Status

- one `.grc` file per session
- package-level catalog description now exposes `describe_block(block_id)` for structured GNU block truth, including **GUI port colors** (e.g., blue for complex, orange for float)
- a bounded retrieval package now exposes `search_grc(...)` for GNU catalog and active-session search
- package-level session inspection now exposes `load_grc(...)`, `summarize_graph(session)`, and `get_grc_context(session, ...)`
- package-level preflight validation now exposes `preflight_transaction(session, operations)` for pure staged checks before mutation; **type-mismatch errors now include actionable repair hints**
- package-level transaction editing now exposes `propose_edit(session, transaction)` and `apply_edit(session, transaction)` for atomic validated edits; **duplicate block IDs are handled via optional block_type disambiguation**
- `FlowgraphSession` owns parsed state, persistence, validation, and all graph-mutation primitives
- `GrcAgent` intentionally exposes a smaller model-facing runtime than the full session surface
- the model-facing runtime now keeps the tools smart and the loop dumb: `agent.py` owns routing rules, follow-up hints, and transaction normalization while `llama_server.py` stays thin
- a thin llama.cpp adapter is wired for single-turn and multi-turn CLI conversations
- `chat` owns **concurrency-safe local llama.cpp startup** using file locking to prevent process races
- the live llama.cpp eval suite now covers phases 1-6, including multi-turn continuity, failure-recovery flows, and compound workflows; see `docs/LLAMA_EVAL.md` for the latest evidence
- latest full live sweep: `uv run python -m tests.llama_eval.run_all` -> **191/198** (Phase 1 `40/40`, Phase 5 `8/8`, Phase 6 `27/28`)
- multi-turn conversations use proactive history compaction (100k char default, configurable via `[agent]`) and session auto-refresh to control prompt growth
- raw model prose is still not trusted outside the runtime's deterministic finalization rules for supported flows

## Repo Map

- [src/grc_agent](src/grc_agent): retrieval, session, validation, transaction, runtime, and CLI package code
- [grc_agent.toml](grc_agent.toml): workspace override config for llama.cpp defaults when running from the repo
- [tests](tests): focused `unittest` regression coverage
- [tests/data/random_bit_generator.grc](tests/data/random_bit_generator.grc): canonical fixture flowgraph
- [tests/llama_eval](tests/llama_eval): six-phase live model eval suite plus `run_all.py` convenience runner
- [docs/BLUEPRINT.md](docs/BLUEPRINT.md): architecture, settled decisions, evidence, milestones, and backlog

## Planning Rule

- do not assume GNU Radio behavior from YAML shape or memory alone
- read the relevant GNU docs first, then verify with real `.grc` and `grcc` runs
- widen the supported contract only after the evidence is written down in [docs/BLUEPRINT.md](docs/BLUEPRINT.md)

## Production-V1 App Shape

The narrow production target is an installable local CLI app.

- installable console entrypoint: `grc-agent`
- built-in runtime defaults when no config file exists
- optional config override via `--config`, `GRC_AGENT_CONFIG`, repo `grc_agent.toml`, or user config at `~/.config/grc_agent/config.toml`
- built-in `doctor` and `health` commands for environment and runtime readiness
- deterministic direct-tool workflows stay valid even when no model backend is configured

Install and check the app:

```bash
uv sync
uv run grc-agent doctor
```

## Retrieval

Phase 1 keeps retrieval package-level and bounded. Phase 6 now routes it through the model-facing runtime without moving the search logic into `agent.py`.

- `initialize_retrieval(warm_catalog=False)`: verify graphify availability, discover the system GNU catalog root, and optionally warm the cached catalog index
- `search_grc(query, scope="catalog|session", k=5)`: package-level structured search contract for GNU catalog or active-session search
- catalog search uses the real system GNU metadata under `/usr/share/gnuradio/grc/blocks` (or `/usr/local/share/gnuradio/grc/blocks` when present)
- search is block-centric by default: parameter and port text boosts parent block matches instead of dominating top-level results
- session search uses the active parsed `.grc` graph that the app startup path binds before runtime flow, and may enrich block results from the catalog when that metadata is available
- unchanged sessions now reuse their previously built session retrieval index instead of rebuilding it on every query
- catalog index construction now reuses the shared phase 2 catalog snapshot for block metadata instead of re-reading every `.block.yml`
- graphify is used only as the graph-construction substrate; GNU metadata and the active `.grc` file remain the truth layers
- the CLI startup path now runs the bounded retrieval readiness check and fails clearly if the catalog root is missing or incomplete
- default results stay compact: score and source scope remain, while rich block details are collapsed into one short `summary` field

Example package usage:

```python
from grc_agent import initialize_retrieval, search_grc

initialize_retrieval()

catalog_hits = search_grc("analog_agc_xx", scope="catalog", k=5)
```

`session` scope is available after the app runtime has loaded an active `FlowgraphSession`, or after direct package callers bind one with `bind_retrieval_context(...)`.

## Catalog

Phase 2 keeps block description package-level and read-only.

- `describe_block(block_id)`: return normalized GNU block truth for one installed catalog block
- catalog description uses the same system GNU metadata roots as retrieval
- payloads stay structured: identity, category path, flags, loaded-from path, parameters, ports, asserts, documentation/doc_url, warnings, and a compact signature
- malformed catalog metadata fails as a structured `ok: false` payload rather than an uncaught parser exception
- hierarchical wrappers are marked through `warnings` instead of widening the public payload with extra derived fields

Example package usage:

```python
from grc_agent import describe_block

block = describe_block("analog_agc_xx")
```

## Session Inspection

Phase 3 now stays package-level and read-oriented. It does not widen the model-facing runtime yet.

- `load_grc(file_path)`: create and load one `FlowgraphSession`
- `summarize_graph(session, max_blocks=8)`: return a bounded structured summary with `graph_id`, counts, dirty state, and validation state
- `get_grc_context(session, node_id, hops=1, max_nodes=20)`: return a bounded neighborhood mini-graph around one session block instance
- provenance is explicit and includes `path`, `graph_id`, `file_format`, and `grc_version`
- unknown node ids fail with a stable `node_not_found` payload instead of falling back to a fuzzy dump

Example package usage:

```python
from grc_agent import get_grc_context, load_grc, summarize_graph

session = load_grc("tests/data/random_bit_generator.grc")
summary = summarize_graph(session)
context = get_grc_context(session, "blocks_throttle2_0", hops=1, max_nodes=20)
```

## Validation

Phase 4 stays package-level and preflight-only. It does not mutate the live graph or call `grcc` as its public contract.

- `preflight_transaction(session, operations)`: validate one ordered transaction or single operation against the active session and installed GNU catalog
- supported operations are `update_params`, `add_connection`, `remove_connection`, `remove_block`, and detached-`variable` `add_block`
- results stay structured: `ok`, `errors`, `warnings`, counts, and `normalized_operations`
- ordered staged validation is allowed: earlier ops can repair a later precondition without mutating the live session

Example package usage:

```python
from grc_agent import load_grc, preflight_transaction

session = load_grc("tests/data/random_bit_generator.grc")
payload = preflight_transaction(
    session,
    {"op_type": "update_params", "instance_name": "samp_rate", "params": {"value": "48000"}},
)
```

## Transactions

Phase 5 stays package-level and is the first path that mutates the live session. It consumes Phase 4 preflight validation, applies the ordered ops on a copied session, runs final `grcc` validation, and swaps the live session only after the candidate validates successfully.

- `propose_edit(session, transaction)`: run preflight and return the normalized/planned operation list with `commit_eligible=False`
- `apply_edit(session, transaction)`: apply the same narrow transaction surface atomically and return affected blocks/connections, revision markers, and final validation state
- supported operations remain narrow: `update_params`, `add_connection`, `remove_connection`, `remove_block`, and detached-`variable` `add_block`
- failed preflight or failed final GNU validation leaves the live session unchanged

Example package usage:

```python
from grc_agent import apply_edit, load_grc

session = load_grc("tests/data/random_bit_generator.grc")
result = apply_edit(
    session,
    {"op_type": "update_params", "instance_name": "samp_rate", "params": {"value": "48000"}},
)
```

## Model-Facing Runtime

The model-facing runtime is intentionally narrower than the session layer.

- `load_grc(file_path)`: load or switch the active `.grc` session
- `summarize_graph(max_blocks=None)`: report the current graph shape
- `search_grc(query, scope="catalog|session", k=5)`: route bounded retrieval with explicit session/catalog context and without runtime-global session binding
- `get_grc_context(node_id, hops=1, max_nodes=20)`: return a bounded neighborhood around one loaded block
- `describe_block(block_id)`: return structured GNU catalog truth for one block id
- `propose_edit(transaction)`: run preflight validation for a supported ordered transaction
- `apply_edit(transaction)`: apply the same narrow transaction surface atomically and commit only after final GNU validation
- `validate_graph`: run `grcc` validation on the current in-memory graph
- `save_graph(path=None)`: persist the current graph, but only after the latest dirty state has passed validation; a successful `apply_edit(...)` already satisfies that gate

The broader `FlowgraphSession` mutation methods remain available for direct code paths and regression tests, but they are not part of the model tool contract. `set_variable` is no longer part of the public runtime surface; variable edits now flow through `propose_edit` / `apply_edit` like other supported transactions. The runtime search path passes explicit session/catalog context into retrieval and does not rely on a module-global active-session binding.
Every model tool call is validated against the declared runtime schema before execution: unknown tools, missing required fields, non-object payloads, type mismatches, enum mismatches, and unsupported extra fields fail with structured errors instead of reaching the session layer. Routed tool results now also carry an `active_session` snapshot so the current file, graph id, revision, dirty flag, and validation state stay explicit at the runtime boundary.

## Optional llama.cpp Spike

The repo now includes a thin llama.cpp adapter that calls only documented server endpoints.

- default llama runtime values come from built-in app defaults and may be overridden by [grc_agent.toml](grc_agent.toml), `GRC_AGENT_CONFIG`, or `--config`
- endpoints used: `/health`, `/v1/models`, `/v1/chat/completions`
- llama.cpp built-in `/tools` is intentionally not used
- the runtime tool surface stays fixed to the explicit routed phase 6 tool list
- returned tool calls are validated against the declared runtime schemas before execution
- active session context is explicit in CLI output, runtime history, and model-visible session messages
- `chat` now owns local llama.cpp startup for the normal CLI path when the configured `server_url` is a plain local `http://127.0.0.1` or `http://localhost` base URL
- the runtime is unbounded with a safety ceiling of 50 tool rounds
- the default model id is the server alias, currently `unsloth/gemma-4-E2B-it-GGUF`
- the default Hugging Face model source is `unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL`
- the default `max_tokens = 100000` is an operational ceiling, not the correctness guard
- summarize final answers are resolved from the `summarize_graph` tool payload
- other supported final answers fall back to the latest structured tool `message` only when the model leaves the final text empty or tool-call-shaped
- raw tool-call-like text is not surfaced as the final answer when no tools actually ran

Run a single-turn CLI path:

```bash
uv run grc-agent chat tests/data/random_bit_generator.grc \
	--message "Change samp_rate to 48000 and validate the graph."
```

Run an interactive multi-turn REPL (no `--message` flag):

```bash
uv run grc-agent chat tests/data/random_bit_generator.grc
```

Type `/quit` or `/exit` to leave the REPL. History compaction runs between turns to control prompt growth across multi-turn sessions.

The eval suite is strong regression evidence for the supported harness contract, but it is not exhaustive proof for concurrent sessions, large-graph scaling, or model/backend diversity.

If the configured local llama.cpp server is down, the CLI starts it automatically, waits for `/health`, requires `/v1/models` to return exactly one model, and requires that model `id` to match the configured alias before the first chat request. Repeated `chat` runs reuse the healthy local backend instead of relaunching it.

For manual backend debugging only, the equivalent repo-configured launch command is:

```bash
llama-server -hf unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL \
	--alias unsloth/gemma-4-E2B-it-GGUF \
	--host 127.0.0.1 \
	--port 8080 \
	--jinja
```

`--jinja` is explicit for reproducibility, but current `llama-server` enables it by default.

Or execute one routed tool directly without a model backend:

```bash
uv run grc-agent tool summarize_graph \
	--file tests/data/random_bit_generator.grc
```

Check the packaged app state:

```bash
uv run grc-agent doctor --json
```

Use `--model` or `--llama-server-url` only when you want a one-off override of the configured defaults.
The `chat` command prints the active session first, then whether it started or reused the backend, so the real runtime path and the current bound `.grc` file are visible in normal CLI output.
When a model tool call is rejected before execution, validation errors feed back to the model for retry.
The final non-tool assistant answer concludes the turn.
For the current supported slice, correctness comes from the bounded runtime contract, not from trusting the model's free-form final prose.

## Verification

Use the packaged CLI entrypoint directly:

```bash
uv run python scripts/check_env.py
uv run grc-agent doctor
uv run ruff check
uv run python -m unittest
uv run grc-agent fake tests/data/random_bit_generator.grc
```

What to expect:

- `check_env.py` passes Python, `grcc`, and GNU Radio version checks
- `grc-agent doctor` passes Python, `grcc`, GNU Radio, config, and retrieval readiness checks
- `ruff check` is clean
- `python -m unittest` passes the current regression suite
- the retrieval and catalog tests cover the real GNU catalog metadata and the canonical `.grc` fixture
- the session inspection tests cover `load_grc(...)`, bounded summary payloads, and bounded context slices on the canonical `.grc` fixture
- the validation tests cover real catalog-backed enum/port rules plus staged transaction checks on the canonical `.grc` fixture
- the transaction tests cover proposal behavior, atomic apply, rollback/unchanged-live-session guarantees, and final GNU validation gating on the canonical `.grc` fixture
- the runtime validation tests reject unknown tools, missing required args, wrong types, and unsupported extra fields before execution
- the runtime loop tests cover invalid tool-call rejection plus `load_grc` session-context rebinding during a chat turn
- `describe_block(...)` is exercised against real GNU blocks with asserts, documentation/doc_url, and hierarchical-wrapper coverage
- the phase-6 `fake` CLI path is a deterministic harness only; it is not evidence that the real model-backed path is ready
- the direct `tool` CLI path exercises read-only and edit flows without a model backend
- the adapter tests exercise a scripted llama.cpp-compatible server while still validating the fixture graph with real `grcc`
- the launcher tests exercise the real subprocess startup path, including cold-port startup, malformed/stale/mismatched state cleanup, alias mismatch failure, and end-to-end `chat` auto-start/reuse on the canonical fixture
- live llama.cpp checks are env-gated:
  ```bash
  GRC_AGENT_LIVE_LLAMA_URL=http://127.0.0.1:8080 \
  GRC_AGENT_LIVE_LLAMA_MODEL=unsloth/gemma-4-E2B-it-GGUF \
  uv run python -m unittest tests.test_llama_server_live
  ```
- the env-gated live llama module now covers CLI cold-start, CLI reuse, a real live edit flow, summarize, and structured edit failure
- the non-gating reliability matrix is:
  ```bash
  GRC_AGENT_LIVE_LLAMA_URL=http://127.0.0.1:8080 \
  GRC_AGENT_LIVE_LLAMA_MODEL=unsloth/gemma-4-E2B-it-GGUF \
  uv run python scripts/llama_reliability_matrix.py
  ```
- the supported live cases are summarize, routed `apply_edit` success, and routed edit failure staying structured
- GitHub Actions repeats the fast lint gate and a GNU-backed validation job on Ubuntu

## Safety Rules

- the model never edits raw `.grc` YAML directly
- all meaningful mutations flow through `FlowgraphSession`
- the runtime save path is blocked until the current dirty state has passed validation
- structural APIs only widen when a new experiment pass justifies them
- final graph validity is established explicitly through `validate()`

See [docs/BLUEPRINT.md](docs/BLUEPRINT.md) for the settled structural boundary, runtime decision, condensed experiment evidence, milestones, and backlog.
