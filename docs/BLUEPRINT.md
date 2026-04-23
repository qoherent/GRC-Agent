# Project Blueprint

## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs.
It inspects, explains, edits, validates, and saves graphs through a bounded tool
contract; the model never edits raw YAML directly.

## Architecture

| Layer | Owner | Purpose |
|---|---|---|
| Raw `.grc` | YAML on disk | Persistence format |
| Session | `flowgraph_session.py`, `models.py` | Loaded graph, mutation, validation, save |
| Catalog | `catalog/` | GNU block metadata and `describe_block(...)` |
| Retrieval | `retrieval/` | Catalog/session search |
| Validation | `validation/` | Pure staged preflight checks |
| Transaction | `transaction/` | Atomic propose/apply on copied session |
| Runtime | `agent.py` | Tool registry, history, prompt, active session |
| Adapter | `llama_server.py`, `llama_launcher.py` | llama.cpp loop, startup, readiness |
| CLI | `cli.py` | `doctor`, `health`, `fake`, `chat`, `tool` |

## Model-facing contract

Nine tools, in fixed order:

1. `load_grc(file_path)`
2. `summarize_graph(max_blocks=None)`
3. `search_grc(query, scope="catalog|session", k=5)`
4. `get_grc_context(node_id, hops=1, max_nodes=20)`
5. `describe_block(block_id)`
6. `apply_edit(transaction)`
7. `propose_edit(transaction)`
8. `validate_graph()`
9. `save_graph(path=None)`

Important rules:

- `apply_edit` stays before `propose_edit`
- raw YAML is never model-editable
- `save_graph` is gated by successful validation of the current dirty state
- runtime search uses explicit session/catalog context
- direct package retrieval binding is context-local, not process-global

## GNU-facing boundary

Supported and verified today:

| Area | Supported contract |
|---|---|
| Parameter edits | `update_params` on loaded blocks, including symbolic GNU/Python expressions and optional `block_type` disambiguation |
| Block state toggles | `update_states` on loaded blocks, supporting `enabled`/`disabled` and optional `block_type` disambiguation |
| Removal | `remove_block` on detached blocks, with optional `block_type` disambiguation |
| Add block | detached `variable` blocks only through generic `add_block` |
| Rewire | ordered transactions may disconnect/reconnect within one staged edit |
| Validation authority | `grcc` is final truth |

Derived rules from real `grcc` probes:

- removing `samp_rate` requires patching dependent parameters first
- detached stream blocks are not generally valid
- second-trace time-sink rewires require coordinated `nconnections` updates
- `state: disabled` is accepted by `grcc`; duplicate-name shadowing is real GNU behavior
- invalid intermediate states are acceptable only when the final staged result validates
- tutorial-driven DSP relationships such as packet formatter compatibility or constellation/unpack lockstep are model recipes and `grcc` concerns, not Python preflight rules

## Runtime properties that matter

- Session search returns canonical `block_id` for `describe_block(...)`
- Empty session search results include a catalog-retry hint
- Session retrieval indexes are reused until session revision changes
- Session history keeps the latest active-session snapshot explicit for the model
- History compaction trims older payloads (100k char default, configurable) without dropping the current session state
- Launcher supports cold start and warm reuse with **concurrency-safe file locking**
- Tool-call schema validation rejects unknown tools, wrong types, enum mismatches, and extra fields before execution
- Incompatible dtype preflight failures now include a Type Converters repair hint
- `describe_block` enriches ports with **canonical GUI colors** (blue, orange, etc.)
- Touched-block preflight now revalidates incident connections after staged parameter/state edits instead of trusting only the edited field shape
- Structural compatibility now includes `vlen` and metadata-backed block `asserts`
- Duplicate enabled parsed identifiers are rejected during staged validation
- Default `search_grc` and `describe_block` payloads are intentionally sparse
- `get_grc_context` accepts exact instance names and can resolve an unambiguous loaded symbol-style id before graph lookup

## Current verified state

### Standard gates

- `uv run ruff check`
- `uv run python -m unittest`
- `uv run grc-agent fake tests/data/random_bit_generator.grc`

### Live llama.cpp evidence

| Area | Evidence |
|---|---|
| Tool routing | Phase 1: 39/40 on the last uninterrupted full sweep; `save_direct` then re-passed 3/3 after the final timeout-retry harness fix |
| Multi-step chains | Phase 2: 30/30 |
| Natural prompts | Phase 3: 51/51 |
| Multi-turn continuity | Phase 4: 41/41 |
| Failure recovery | Phase 5: 8/8 |
| Compound workflows | Phase 6: 28/28 |
| Full sweep | Last uninterrupted `run_all`: 197/198 before the final Phase 1 timeout-retry fix; no post-fix uninterrupted full sweep was completed in this session |
| CLI backend lifecycle | Cold start and warm reuse verified through `grc-agent chat` |

### Verified behaviors

- `summarize_graph` returns bounded graph summaries
- `get_grc_context` returns bounded neighborhoods for exact instance names
- `describe_block` returns normalized GNU metadata including port colors
- `apply_edit(update_params, samp_rate, 48000)` validates successfully
- `apply_edit(update_params, qtgui_time_sink_x_0.srate, samp_rate/2)` preserves the symbolic expression and validates successfully
- `apply_edit(add_block(variable debug_gain=0), then update_states(debug_gain, disabled))` validates successfully
- `apply_edit(repair transaction, then remove samp_rate)` validates successfully
- malformed quoted transaction payloads from the model are normalized before execution and now grade correctly in eval capture
- preview-only failure replies are finalized from the failed `propose_edit` result instead of trusting unstable trailing assistant text
- **Duplicate block IDs** are correctly handled via `block_type` disambiguation in `update_params`, `update_states`, and `remove_block`
- incompatible dtype preflight failures point the model toward Type Converters for common repair paths
- live repair reminders/order guards prevent partial “fixes” from satisfying removal requests
- touched parameter edits now invalidate existing incident connections when they break dtype, `vlen`, or metadata `asserts`
- sparse tool payloads reduce default search/describe verbosity without changing the public tool set
- full-sweep harness restarts the llama backend before each phase to reduce long-run drift
- Phase 1 now retries once after a llama connection timeout by force-restarting the backend, specifically to stabilize late-phase `save_graph` routing

## What is not proven

The current evidence supports the **single-session local CLI contract**.
It does **not** prove:

- concurrent-session behavior under extreme load
- large-graph latency or stability (>100 blocks)
- behavior across other model/back-end combinations
- general-purpose structural editing beyond the explicitly verified slice
- DSP-chain correctness beyond structural metadata; that still depends on model reasoning plus `validate_graph` / `grcc`

## Verification commands

```bash
uv run ruff check
uv run python -m unittest
uv run python -m tests.llama_eval.run_all
uv run grc-agent doctor
uv run grc-agent chat tests/data/random_bit_generator.grc --message "Summarize the graph."
uv run grc-agent chat tests/data/random_bit_generator.grc --message "Change samp_rate to 48000 and validate the graph."
```
