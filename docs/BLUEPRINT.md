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
| Variable edits | `update_params` on loaded variable blocks |
| Removal | detached/unreferenced blocks only unless the transaction repairs dependencies first |
| Add block | detached `variable` blocks only through generic `add_block` |
| Rewire | ordered transactions may disconnect/reconnect within one staged edit |
| Validation authority | `grcc` is final truth |

Derived rules from real `grcc` probes:

- removing `samp_rate` requires patching dependent parameters first
- detached stream blocks are not generally valid
- second-trace time-sink rewires require coordinated `nconnections` updates
- invalid intermediate states are acceptable only when the final staged result validates

## Runtime properties that matter

- Session search returns canonical `block_id` for `describe_block(...)`
- Empty session search results include a catalog-retry hint
- Session retrieval indexes are reused until session revision changes
- Session history keeps the latest active-session snapshot explicit for the model
- History compaction trims older payloads without dropping the current session state
- Launcher supports cold start and warm reuse through the real CLI path
- Tool-call schema validation rejects unknown tools, wrong types, enum mismatches, and extra fields before execution

## Current verified state

### Standard gates

- `uv run ruff check`
- `uv run python -m unittest`
- `uv run grc-agent fake tests/data/random_bit_generator.grc`

### Live llama.cpp evidence

| Area | Evidence |
|---|---|
| Tool routing | Phase 1: 40/40 |
| Multi-step chains | Phase 2: 30/30 |
| Natural prompts | Phase 3: 43/43 |
| Multi-turn continuity | Phase 4: 40/40 |
| Failure recovery | Phase 5: 8/8 |
| Compound workflows | Phase 6: 28/28 |
| CLI backend lifecycle | Cold start and warm reuse verified through `grc-agent chat` |

### Verified behaviors

- `summarize_graph` returns bounded graph summaries
- `get_grc_context` returns bounded neighborhoods for exact instance names
- `describe_block` returns normalized GNU metadata from the real catalog
- `apply_edit(update_params, samp_rate, 48000)` validates successfully
- `apply_edit(repair transaction, then remove samp_rate)` validates successfully
- live repair reminders/order guards prevent partial “fixes” from satisfying removal requests

## What is not proven

The current evidence supports the **single-session local CLI contract**.
It does **not** prove:

- concurrent-session behavior under load
- large-graph latency or stability
- behavior across other model/back-end combinations
- general-purpose structural editing beyond the explicitly verified slice

## Verification commands

```bash
uv run ruff check
uv run python -m unittest
uv run python -m tests.llama_eval.run_all
uv run grc-agent doctor
uv run grc-agent chat tests/data/random_bit_generator.grc --message "Summarize the graph."
uv run grc-agent chat tests/data/random_bit_generator.grc --message "Change samp_rate to 48000 and validate the graph."
```
