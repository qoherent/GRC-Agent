# Project Blueprint

Updated: 2026-04-28

## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs. It creates, inspects, edits, validates, and saves graphs through a bounded tool contract. The model decides what graph work to attempt; verified tools decide whether mutations are allowed; `grcc` remains final validation authority.

## Safety Contract

- The model must never edit raw `.grc` YAML directly.
- All graph mutations go through `apply_edit`, `remove_connection`, `insert_block_on_connection`, or `auto_insert_block`.
- Preview operations must never mutate the live graph.
- Failed edits must roll back atomically.
- `save_graph` is allowed only after the latest dirty state has validated successfully.
- Saving writes through a same-directory temp file and `os.replace`.
- No hidden repairs, prompt-regex transaction rewriting, fixture-specific logic, block recipes, block blacklists, unbounded retries, or unbounded candidate search.
- Clarification options must come from real validated graph/tool candidates and always include a custom/free-text option.

## Architecture

| Layer | Files | Responsibility |
|---|---|---|
| CLI | `cli.py` | `doctor`, `health`, `fake`, `chat`, direct `tool` execution, manual search |
| Runtime | `agent.py`, `runtime/` | Tool registry, schemas, prompt, history, argument normalization, clarification handling |
| Adapter | `llama_server.py`, `llama_launcher.py` | llama.cpp HTTP transport, startup/reuse, bounded turn loop |
| Session | `flowgraph_session.py`, `models.py` | Loaded graph state, parsing, validation, atomic save, compact session snapshots |
| Catalog | `catalog/` | GNU block metadata, parameter defaults, port definitions, block descriptions |
| Retrieval | `retrieval/` | Bounded catalog and active-session search |
| Manual | `manual/` | Read-only GNU Radio tutorial/reference cleaning and cited lexical search |
| Validation | `validation/` | Pure staged preflight checks and default filling |
| Transaction | `transaction/` | Atomic propose/apply on copied sessions before live commit |

Adapter boundary: `llama_server.py` should stay transport and bounded-loop oriented. Existing assistant-text fallback parsing is legacy weak-model compatibility and must not expand; moving it behind `GrcAgent` requires separate approval plus Tier 2 live eval.

## Model Tools

Fifteen tools are exposed in fixed order:

1. `new_grc(graph_id="new_flowgraph")`
2. `load_grc(file_path)`
3. `summarize_graph(max_blocks=None)`
4. `search_grc(query, scope="catalog|session", k=5)`
5. `get_grc_context(node_id, hops=1, max_nodes=20)`
6. `describe_block(block_id)`
7. `search_manual(query, k=3)`
8. `suggest_compatible_insertions(connection_id, k=5)`
9. `insert_block_on_connection(connection_id, block_type, instance_name, params=None)`
10. `auto_insert_block(goal, preferred_block_type=None, target_hint=None, max_candidates=10)`
11. `remove_connection(connection_id)`
12. `apply_edit(transaction)`
13. `propose_edit(transaction)`
14. `validate_graph()`
15. `save_graph(path=None)`

Tool order matters. Keep `search_manual` after catalog block description and before mutation helpers, keep the exact-argument `remove_connection` wrapper before the nested `apply_edit` fallback, keep `apply_edit` before `propose_edit`, and keep insertion helpers before lower-level edit tools unless a separate eval-backed change proves otherwise.

## Supported Graph Work

- `new_grc` creates a minimal empty skeleton; construction uses `apply_edit`.
- `load_grc` binds one `.grc` file as the active session.
- `summarize_graph`, `search_grc`, `get_grc_context`, `describe_block`, and `search_manual` are read-only inspection/explanation paths.
- `update_params` supports unique loaded blocks and variables, including symbolic GNU/Python expressions.
- `update_states` supports `enabled` and `disabled` on unique loaded blocks.
- `add_block` supports arbitrary catalog blocks with catalog-default parameter filling.
- `add_connection` and `remove_connection` support stream and message-port endpoints.
- `remove_block` requires the target to be detached and uniquely named.
- `insert_block_on_connection` is a thin exact-arg wrapper around `apply_edit`.
- `remove_connection` is a thin exact-arg wrapper around the verified edit path; GNU-invalid disconnect end states are classified as nonrecoverable and are not retried by the model.
- `suggest_compatible_insertions` is read-only and returns catalog-backed candidate args.
- `auto_insert_block` performs bounded candidate search, commits one `grcc`-valid insertion, asks for clarification, or rejects safely.
- Duplicate instance names are safely rejected for edits until stable block identity exists.

## Runtime Properties

- Active-session context is explicit in CLI output, runtime history, and model-visible messages.
- Active-session context includes bounded counts, variable/block previews, and connection IDs so small models can route exact connection work without a full graph dump.
- History compaction keeps the latest active-session snapshot while trimming older large tool payloads.
- Tool-call schemas reject unknown tools, missing fields, wrong types, enum mismatches, and extra fields before execution.
- Schema-rejected tool calls are recorded as failed turn actions, so the bounded turn guard does not nudge the model to continue after invalid arguments.
- `apply_edit` validates internally before committing; successful apply satisfies the dirty-state validation gate for save, but explicit user validation still requires `validate_graph`.
- `grcc` is used for final validation and remains the authority over GNU behavior.
- llama.cpp local startup uses file locking, model-alias verification, deterministic `temperature=0.0`, bounded generation defaults, and `--no-mmproj` when supported.
- `doctor` is passive by default and does not start llama.cpp unless `--start-llama` is supplied.
- Live eval reports collect best-effort llama.cpp `/props` metadata so backend tool-template/parser capability is visible without failing older servers.
- Live eval reports include repeat-run stability metadata. `--n-runs` controls repeated attempts and `--stability-threshold` controls the reported per-case release-stability threshold without changing majority pass/fail gating.
- `tests.llama_eval.release_dashboard` aggregates persisted `--results-path` stores across live tiers and fails CI-style when required phases, minimum run counts, infra health, or per-case stability are not met.
- Failed-tool recovery is classified by a typed policy shared with live evals. The policy can mark missing mutation arguments, dirty-save refusal, and clarification payloads as bounded recoverable cases; GNU-invalid end states and unsupported requests stay nonrecoverable. It snapshots graph state before/after recovery attempts, limits recovery mutation retries, and does not synthesize graph recipes or bypass tools.
- Raw YAML direct-edit, undo/redo, and Python export/code-generation requests are refused as unsupported.

## Current Status

Local alpha is ready for daily manual use.

- Ruff gate passed: `uv run ruff check src/ tests/`.
- Deterministic unittest gate passed: `uv run python -m unittest`.
- Tier 1 live eval reporting distinguishes routing pass, argument pass, tool success pass, semantic pass, safety pass, end-state pass, and recovery pass. The first semantic checks cover simple parameter edit, preview no-mutation, explicit save reload/validate, raw YAML refusal no-mutation, and edit-validate-save.
- Tier 1 live quick eval with semantic reporting passed: 15/15. Tool success passed 12/15 because three insertion cases safely returned clarification rather than a committed mutation.
- Tier 2 release eval now uses the shared declarative live scenario harness and reports routing, argument, tool success, semantic, safety, end-state, and recovery dimensions. Latest quick live run passed 36/36 with every dimension green.
- Tier 3 multi-turn live eval covers clarification replies, preview-then-apply, edit-then-validate, edit-then-save, bounded recovery classification, and vague connection refusal. Latest quick live run passed 7/7 with every dimension green.
- Tier 4 installed-example live eval covers read-only summary, validation, save-copy behavior, edit/validate, edit/validate/save-copy, and one non-variable block-parameter edit/validate case on installed GNU Radio examples. Latest quick live run passed 6/6 with every dimension green; repeated Tier 4 runs passed 18/18 with stability green.
- Tier 4 `--include-probes` currently includes a block-state edit/validate probe on `vocoder/grfreedv.grc`. The verified tool path is deterministic, but the 2B model passed only 2/3 live attempts by sometimes interpreting "disable" as `remove_block`; preflight rejected that connected-block removal safely. This probe is not promoted to the release gate.
- Persisted release dashboard over Tier 2, Tier 3, and Tier 4 with `--n-runs 3` passed 147/147 model attempts, 0 infra failures, 0 unstable cases, and `release_ready=true`.
- STOP_THE_LINE safety events: 0 in the accepted eval baseline.
- Default backend remains `unsloth/gemma-4-E2B-it-GGUF` through llama.cpp.

## Known Limits

- The default 2B model is reliable for summarize, inspect, search, describe, validate, save, preview, raw-YAML refusal, and simple parameter edits.
- Natural-language insertion is bounded and safe, but may clarify or reject instead of mutating.
- Complex multi-step graph creation is still model-limited.
- Exact natural-language disconnection/rewire requests route through `remove_connection` when a connection ID can be derived; GNU-invalid end states roll back and are classified as nonrecoverable.
- Copying structured fields from one tool output into another is not consistently reliable with the 2B model.
- Runtime correction now handles schema-level malformed mutation calls with one typed retry through the model, restricted to recovery-policy allowed tools. The promoted selector block-parameter live case exercises this path and still validates through `apply_edit` plus `grcc`.
- Valid installed examples with mixed stream/message port identifiers now load through `GrcAgent`; connection ordering normalizes port sort keys instead of comparing unlike Python types.
- Natural-language state edits are verified by the tool layer, but the default 2B model may confuse "disable block" with removal. Keep state-edit live cases as probes until repeated runs are stable without adding semantic hidden repairs.
- Expert GNU/DSP answers can use cited `search_manual` excerpts, but answer quality still depends on backend synthesis and corpus coverage.
- The current live evals measure bounded tool routing, selected semantic/end-state outcomes, multi-turn follow-up behavior, and safety; they do not prove Claude Code/Cursor-style long-horizon autonomy.
- Tier 2 semantic checks are broader than Tier 1 but still canonical-fixture scoped. Tier 4 adds a small installed-example smoke/edit gate, but it is not evidence for arbitrary installed GNU examples or long-horizon design tasks.

## Tutorial Corpus Policy

`docs/wiki_gnuradio_org/` is kept as a local GNU Radio tutorial/reference corpus. It is available through `search_manual` for cited, explanation-only retrieval and may inform future explanation evals.

- Do not turn tutorials into runtime block recipes.
- Do not add tutorial-derived hidden repairs.
- Do not use tutorial pages as block blacklists or allowlists.
- Keep catalog metadata and `grcc` as the truth for tool arguments and validity.
- Manual results must keep provenance and must not expose mutation-shaped outputs such as transactions or insert-tool arguments.

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
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
```

Use Tier 1 after runtime, prompt, schema, or live-eval changes. Use Tier 2 before release or after adapter behavior changes. Use Tier 3 before claiming multi-turn clarification/recovery reliability. Use Tier 4 when installed GNU Radio examples are available. For release candidates, run Tier 2, Tier 3, and Tier 4 with `--n-runs 3` and inspect the stability report. Tier 4 `--include-probes` is for future known-gap investigation only and is not part of release readiness unless a probe is explicitly promoted after repeated stable runs.

Persisted release dashboard:

```bash
uv run python -m tests.llama_eval.release_dashboard \
  --results-path /tmp/grc-agent-live-runs.json \
  --min-runs-per-case 3
```

## Backlog

- Expand Tier 4 beyond smoke behavior to representative installed-example edits only after deterministic tool tests prove the edit selection is generic.
- Add the next installed-example edit only after deterministic verified-tool coverage and repeated live evidence show it is generic; keep new known-gap probes opt-in until they are stable.
- Improve low-intelligence routing for explicit enable/disable requests only with measured generic evidence; do not recover wrong destructive operations by inventing hidden repairs.
- Move assistant-text fallback parsing behind `GrcAgent` without behavior drift.
- Expand manual retrieval quality and coverage for explanation-only answers without making it mutation-adjacent.
- Persist accepted release-dashboard artifacts in a stable location when cutting tagged releases.
- Consider stable `block_uid` support for duplicate-name disambiguation.
