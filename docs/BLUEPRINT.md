# Project Blueprint

Updated: 2026-04-27

## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs. It creates, inspects, edits, validates, and saves graphs through a bounded tool contract. The model decides what graph work to attempt; verified tools decide whether mutations are allowed; `grcc` remains final validation authority.

## Safety Contract

- The model must never edit raw `.grc` YAML directly.
- All graph mutations go through `apply_edit`, `insert_block_on_connection`, or `auto_insert_block`.
- Preview operations must never mutate the live graph.
- Failed edits must roll back atomically.
- `save_graph` is allowed only after the latest dirty state has validated successfully.
- Saving writes through a same-directory temp file and `os.replace`.
- No hidden repairs, prompt-regex transaction rewriting, fixture-specific logic, block recipes, block blacklists, unbounded retries, or unbounded candidate search.
- Clarification options must come from real validated graph/tool candidates and always include a custom/free-text option.

## Architecture

| Layer | Files | Responsibility |
|---|---|---|
| CLI | `cli.py` | `doctor`, `health`, `fake`, `chat`, direct `tool` execution |
| Runtime | `agent.py`, `runtime/` | Tool registry, schemas, prompt, history, argument normalization, clarification handling |
| Adapter | `llama_server.py`, `llama_launcher.py` | llama.cpp HTTP transport, startup/reuse, bounded turn loop |
| Session | `flowgraph_session.py`, `models.py` | Loaded graph state, parsing, validation, atomic save, compact session snapshots |
| Catalog | `catalog/` | GNU block metadata, parameter defaults, port definitions, block descriptions |
| Retrieval | `retrieval/` | Bounded catalog and active-session search |
| Validation | `validation/` | Pure staged preflight checks and default filling |
| Transaction | `transaction/` | Atomic propose/apply on copied sessions before live commit |

Adapter boundary: `llama_server.py` should stay transport and bounded-loop oriented. Existing assistant-text fallback parsing is legacy weak-model compatibility and must not expand; moving it behind `GrcAgent` requires separate approval plus Tier 2 live eval.

## Model Tools

Thirteen tools are exposed in fixed order:

1. `new_grc(graph_id="new_flowgraph")`
2. `load_grc(file_path)`
3. `summarize_graph(max_blocks=None)`
4. `search_grc(query, scope="catalog|session", k=5)`
5. `get_grc_context(node_id, hops=1, max_nodes=20)`
6. `describe_block(block_id)`
7. `suggest_compatible_insertions(connection_id, k=5)`
8. `insert_block_on_connection(connection_id, block_type, instance_name, params=None)`
9. `auto_insert_block(goal, preferred_block_type=None, target_hint=None, max_candidates=10)`
10. `apply_edit(transaction)`
11. `propose_edit(transaction)`
12. `validate_graph()`
13. `save_graph(path=None)`

Tool order matters. Keep `apply_edit` before `propose_edit`, and keep insertion helpers before lower-level edit tools unless a separate eval-backed change proves otherwise.

## Supported Graph Work

- `new_grc` creates a minimal empty skeleton; construction uses `apply_edit`.
- `load_grc` binds one `.grc` file as the active session.
- `summarize_graph`, `search_grc`, `get_grc_context`, and `describe_block` are read-only inspection paths.
- `update_params` supports unique loaded blocks and variables, including symbolic GNU/Python expressions.
- `update_states` supports `enabled` and `disabled` on unique loaded blocks.
- `add_block` supports arbitrary catalog blocks with catalog-default parameter filling.
- `add_connection` and `remove_connection` support stream and message-port endpoints.
- `remove_block` requires the target to be detached and uniquely named.
- `insert_block_on_connection` is a thin exact-arg wrapper around `apply_edit`.
- `suggest_compatible_insertions` is read-only and returns catalog-backed candidate args.
- `auto_insert_block` performs bounded candidate search, commits one `grcc`-valid insertion, asks for clarification, or rejects safely.
- Duplicate instance names are safely rejected for edits until stable block identity exists.

## Runtime Properties

- Active-session context is explicit in CLI output, runtime history, and model-visible messages.
- History compaction keeps the latest active-session snapshot while trimming older large tool payloads.
- Tool-call schemas reject unknown tools, missing fields, wrong types, enum mismatches, and extra fields before execution.
- `apply_edit` validates internally before committing; successful apply satisfies the dirty-state validation gate for save, but explicit user validation still requires `validate_graph`.
- `grcc` is used for final validation and remains the authority over GNU behavior.
- llama.cpp local startup uses file locking, model-alias verification, and `--no-mmproj` when supported.
- Raw YAML direct-edit, undo/redo, and Python export/code-generation requests are refused as unsupported.

## Current Status

Local alpha is ready for daily manual use.

- Ruff gate passed: `uv run ruff check src/ tests/`.
- Deterministic unittest gate passed: `uv run python -m unittest` with 669 tests, 9 skipped.
- Tier 1 live quick eval passed: 15/15.
- Tier 2 release eval passed: 35/36, with the single miss triaged as model routing, not safety.
- STOP_THE_LINE safety events: 0 in the accepted eval baseline.
- Default backend remains `unsloth/gemma-4-E2B-it-GGUF` through llama.cpp.

## Known Limits

- The default 2B model is reliable for summarize, inspect, search, describe, validate, save, preview, raw-YAML refusal, and simple parameter edits.
- Natural-language insertion is bounded and safe, but may clarify or reject instead of mutating.
- Complex multi-step graph creation is still model-limited.
- Copying structured fields from one tool output into another is not consistently reliable with the 2B model.
- Expert GNU/DSP answers depend on backend quality unless future tutorial retrieval is added.
- The current live evals measure bounded tool routing and safety; they do not prove Claude Code/Cursor-style long-horizon autonomy.

## Tutorial Corpus Policy

`docs/wiki_gnuradio_org/` is kept as a local GNU Radio tutorial/reference corpus. It may inform future documentation, retrieval, and explanation evals.

- Do not turn tutorials into runtime block recipes.
- Do not add tutorial-derived hidden repairs.
- Do not use tutorial pages as block blacklists or allowlists.
- Keep catalog metadata and `grcc` as the truth for tool arguments and validity.
- Future tutorial/RAG work should start with explanation-only retrieval and provenance before mutation evals.

## Patch Criteria

Patch runtime behavior only when one of these occurs:

1. Unsafe mutation.
2. Invalid graph committed or saved.
3. Preview mutates the live graph.
4. Raw YAML edit bypasses the guard.
5. Wrong file overwritten.
6. Valid installed GNU example fails to load.
7. The same failure repeats across three or more unrelated real-use graphs.

Do not patch isolated small-model weirdness.

## Standard Gates

```bash
uv run ruff check src/ tests/
uv run python -m unittest
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
```

Use Tier 1 after runtime, prompt, schema, or live-eval changes. Use Tier 2 before release or after adapter behavior changes.

## Backlog

- Add multi-turn evals for clarification resolution, failed-edit correction, validate-after-edit, and save-after-validation.
- Add semantic end-state checks to live evals, not just expected tool-name checks.
- Move assistant-text fallback parsing behind `GrcAgent` without behavior drift.
- Investigate tutorial/RAG support for explanations with provenance.
- Consider stable `block_uid` support for duplicate-name disambiguation.
