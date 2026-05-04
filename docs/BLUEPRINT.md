# Project Blueprint

Updated: 2026-04-28

## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs. It creates, inspects, edits, validates, and saves graphs through a bounded tool contract. The model decides what graph work to attempt; verified tools decide whether mutations are allowed; `grcc` remains final validation authority.

## Safety Contract

- The model must never edit raw `.grc` YAML directly.
- All graph mutations go through `apply_edit`, `remove_connection`, `rewire_connection`, `insert_block_on_connection`, or `auto_insert_block`.
- Preview operations must never mutate the live graph.
- Failed edits must roll back atomically.
- `save_graph` is allowed only after the latest dirty state has validated successfully.
- Saving writes through a same-directory temp file and `os.replace`.
- Local history checkpoints are runtime/CLI infrastructure only. Restore is
  CLI-only and writes to an explicit new copy path; it is not a model-facing
  undo/restore tool.
- MVP model-facing simplification is available through wrapper tools:
  `inspect_graph`, `search_blocks`, `search_help`, `change_graph`. Legacy
  low-level tools remain internal safety handlers.
- No hidden repairs, prompt-regex transaction rewriting, fixture-specific logic, block recipes, block blacklists, unbounded retries, or unbounded candidate search.
- Clarification options must come from real validated graph/tool candidates and always include a custom/free-text option.

## Architecture

| Layer | Files | Responsibility |
|---|---|---|
| CLI | `cli.py` | `doctor`, `health`, `fake`, `chat`, direct `tool` execution, manual/vector/dogfood/history commands |
| Runtime | `agent.py`, `runtime/` | Tool registry, schemas, prompt state, local checkpoint journaling, argument normalization, clarification handling |
| Adapter | `llama_server.py`, `llama_launcher.py` | llama.cpp HTTP transport, startup/reuse, bounded turn loop |
| Session | `flowgraph_session.py`, `models.py` | Loaded graph state, parsing, validation, atomic save, compact session snapshots |
| Catalog | `catalog/` | GNU block metadata, parameter defaults, port definitions, block descriptions |
| Retrieval | `retrieval/` | Bounded lexical catalog/session search and read-only vector retrieval |
| Manual | `manual/` | Read-only GNU Radio tutorial/reference cleaning and cited lexical search |
| Validation | `validation/` | Pure staged preflight checks and default filling |
| Transaction | `transaction/` | Atomic propose/apply on copied sessions before live commit |
| History | `history.py` | Local checkpoint JSONL, graph deltas, retention, CLI-only restore to copy path |
| Advisor | `runtime/turnplan_advisor.py` | Advisor-first semantic mode classification; currently shadow-only until promotion gates pass |

Adapter boundary: `llama_server.py` should stay transport and bounded-loop oriented. Existing assistant-text fallback parsing is legacy weak-model compatibility and must not expand; moving it behind `GrcAgent` requires separate approval plus Tier 2 live eval.

## Model Tools

Default model-facing chat uses MVP wrappers only:

1. `inspect_graph`
2. `search_blocks`
3. `search_help`
4. `change_graph`

Low-level handlers remain internal/compatibility-only and are still verified
through the same runtime safety contract. Normal model-backed chat must not
expose low-level mutation tools directly unless explicit compatibility mode is
enabled for debugging/research.

`change_graph` is the single model-facing mutation wrapper. It internally
routes to verified handlers (`apply_edit`, `propose_edit`, `remove_connection`,
`rewire_connection`, insertion helpers) and still enforces schema validation,
route gates, preflight, `grcc`, rollback, and checkpoint journaling.

`save_graph` is not model-facing in MVP mode.

Advisor-first contract: semantic intent classification belongs to the local
Advisor, not to deterministic phrase dictionaries. The Advisor calls the same
llama.cpp server and must return exactly one JSON object:
`{"mode":"inspect|preview|change|clarify|unsupported"}`.
The mode enum is the semantic routing interface. Runtime code may validate the
schema, map the mode to a tool class, enforce allowed tools, validate
operation schemas, run preflight/`grcc`, roll back failed edits, and enforce
save state. Runtime code must not duplicate semantic intent logic with regexes,
phrase lists, hardcoded natural-language synonyms, prompt-regex transaction
rewrites, or hidden remapping.

Advisor is currently shadow-only and does not control default runtime routing.

## Supported Graph Work

- `new_grc` creates a minimal empty skeleton; construction uses `apply_edit`.
- `load_grc` binds one `.grc` file as the active session.
- `summarize_graph`, `search_grc`, `semantic_search_grc`, `get_grc_context`, `describe_block`, and `search_manual` are read-only inspection/explanation paths.
- `update_params` supports unique loaded blocks and variables, including symbolic GNU/Python expressions. Duplicate names may clarify only when `instance_name + block_type` is an executable discriminator.
- `update_states` supports `enabled` and `disabled` on unique loaded blocks. Duplicate names may clarify only when `instance_name + block_type` is an executable discriminator.
- `add_block` supports arbitrary catalog blocks with catalog-default parameter filling.
- `add_connection` and `remove_connection` support stream and message-port endpoints.
- `remove_block` requires the target to be detached and uniquely identified by the verified operation.
- `insert_block_on_connection` is a thin exact-arg wrapper around `apply_edit`.
- `remove_connection` is a thin wrapper around the verified edit path. It accepts an exact `connection_id`, or endpoint hints that the runtime resolves to one existing `connection_id` before mutation. Endpoint fields are not a second mutation path; zero matches fail unchanged and multiple matches clarify.
- `rewire_connection` is a thin wrapper around one ordered verified transaction:
  remove one resolved old connection, then add one resolved new connection. Old
  endpoint hints resolve to one `connection_id` or clarify with
  `state_revision`; partial new-endpoint hints are allowed only when they
  resolve to one executable candidate or a bounded clarification choice. The
  resolver rejects invalid message-port numeric hints before they can become
  executable clarification options.
- `suggest_compatible_insertions` is read-only and returns catalog-backed candidate args.
- `auto_insert_block` performs bounded candidate search, commits one `grcc`-valid insertion, asks for clarification, or rejects safely.
- Loaded blocks carry a deterministic `block_uid` for inspection and eval
  identity. Free-form user text cannot mutate by UID. The only supported UID
  mutation path is a verified `target_ref` object (`block_uid`,
  `expected_instance_name`, `expected_block_type`, `base_state_revision`) for
  `update_params` / `update_states` through `apply_edit` or `propose_edit`.
- `FlowgraphSession.resolve_block_reference(...)` returns read-only identity
  candidates with the current `state_revision`; callers must reject stale
  selections after any graph change. Same-name same-type duplicate param/state
  edits may execute only after clarification produces a verified `target_ref`.
- `FlowgraphSession.find_connection_candidates(...)` returns read-only endpoint
  candidates with the current `state_revision`; multiple matches must clarify,
  not pick the first connection.

## Runtime Properties

- Active-session context is explicit in CLI output, runtime history, and model-visible messages.
- Active-session context includes bounded counts, variable/block previews, and connection IDs so small models can route exact connection work without a full graph dump.
- History compaction keeps the latest active-session snapshot while trimming older large tool payloads.
- Tool-call schemas reject unknown tools, missing fields, wrong types, enum mismatches, and extra fields before execution.
- Each normal model turn is classified into a finite typed `TurnPlan` before the first completion. The plan records intent, required actions, allowed tools, expected transaction op types, clarification state, and evidence text.
- For classified turns, llama.cpp receives only the schemas allowed by the plan. For example, explicit disable/enable requests expose `apply_edit`/`propose_edit` plus any requested follow-up tools, and expect `update_states`.
- `uncertain_mutation` covers vague mutation wording such as swap/repair/rewire/fix/change topology when no finite safe intent is known. It clarifies before any model call and exposes no tools.
- Natural insertion wording routes to `auto_insert_block` only when the user supplies placement context such as a connection, source/destination, or signal path. Missing placement context clarifies before any model call.
- Exact disconnect wording routes to `remove_connection` without an `apply_edit`
  fallback. Endpoint hints are normalized to `connection_id` first; vague
  disconnect wording clarifies or stays in `uncertain_mutation`.
- Exact or bounded rewire wording routes to `rewire_connection` when the old
  connection is exact/resolvable and the new endpoint is exact or resolvable
  from bounded endpoint hints. Ambiguous old edges or new endpoints clarify with
  executable candidate options and `state_revision`; vague topology rewires
  stay in `uncertain_mutation`.
- Deterministic exact tool calls, such as exact add-variable and exact
  connection-id rewires, are built behind `GrcAgent` after `TurnPlan`
  classification. The llama.cpp adapter only transports the resulting
  structured tool call and still runs route validation, schema validation,
  wrapper execution, preflight, `grcc`, and rollback.
- Route mismatches fail closed before execution. If a disable/enable turn attempts `remove_block`, no graph mutation runs; the result is `route_mismatch`, not a hidden rewrite to `update_states`.
- Schema-rejected tool calls are recorded as failed turn actions, so the bounded turn guard does not nudge the model to continue after invalid arguments.
- `apply_edit` validates internally before committing; successful apply satisfies the dirty-state validation gate for save, but explicit user validation still requires `validate_graph`.
- `grcc` is used for final validation and remains the authority over GNU behavior.
- llama.cpp local startup uses file locking, model-alias verification, deterministic `temperature=0.0`, bounded generation defaults, and `--no-mmproj` when supported.
- `doctor` is passive by default and does not start llama.cpp unless `--start-llama` is supplied.
- Live eval reports collect best-effort llama.cpp `/props` metadata so backend tool-template/parser capability is visible without failing older servers.
- Live eval reports include repeat-run stability metadata and persisted release metadata: git commit, prompt hash/version, tool-schema hash, TurnPlan policy hash/version, model alias, backend metadata, chat-template hash, results schema version, and fixture identifiers. `--n-runs` controls repeated attempts and `--stability-threshold` controls the reported per-case release-stability threshold without changing majority pass/fail gating.
- `tests.llama_eval.release_dashboard` aggregates persisted `--results-path` stores across live tiers and fails CI-style when required phases, minimum run counts, infra health, or per-case stability are not met.
- Failed-tool recovery is classified by a typed policy shared with live evals. The policy can mark missing mutation arguments, dirty-save refusal, and clarification payloads as bounded recoverable cases; GNU-invalid end states and unsupported requests stay nonrecoverable. It snapshots graph state before/after recovery attempts, limits recovery mutation retries, and does not synthesize graph recipes or bypass tools.
- Raw YAML direct-edit, undo/redo, and Python export/code-generation requests are refused as unsupported.
- `search_grc` remains deterministic lexical graph search. It now has a finite alias layer for known misses such as audio smoother, automatic gain control, spectrum, rate limiter, scope, and trace.
- `semantic_search_grc` is read-only vector search over a local Qdrant index built with FastEmbed `BAAI/bge-small-en-v1.5`. It is candidate discovery only and is not exposed for `uncertain_mutation`.
- Vector retrieval v1 is frozen as local Qdrant + FastEmbed + `BAAI/bge-small-en-v1.5`, vector-only, read-only, no hybrid, no reranker, and no runtime multi-model selector. The current governed baseline artifact is `reports/retrieval/vector_eval_governed_metadata.json`.
- Catalog semantic metadata additions must describe stable block capability, not patch one eval query. Changes require repeated clustered evidence and a retrieval-regression rerun before acceptance.
- `grc-agent vector miss` records sanitized real-user/eval/manual-review retrieval misses to JSONL evidence only. It redacts filesystem paths and `.grc` filenames from stored query, notes, expected IDs, and actual top IDs while preserving normal catalog IDs. `grc-agent vector misses` clusters and deduplicates those records conservatively; shared expected IDs alone do not merge unrelated wording. `grc-agent vector proposals` generates a human-review candidate report only. These commands must not update metadata, rebuild indexes, change rankings, call model tools, or authorize mutation.
- `grc-agent vector gc` is explicit cleanup for old local Qdrant collections from this index family. It is dry-run by default, requires `--apply` to delete, preserves the active alias target plus one previous retired collection, and deletes older staging/retired collections only with `--apply`.
- `grc-agent dogfood record` and `grc-agent dogfood report` provide structured real-use evidence intake. They are evidence-only: no model call, no tool execution, no graph mutation, no retrieval rebuild, and no automatic fix promotion. Intake redacts filesystem paths and `.grc` filenames from stored user text, records task/failure/severity categories, and clusters repeated observations conservatively so fixes are based on repeated generic gaps rather than one-off prompts.
- Assistant-text fallback parsing is frozen legacy weak-model compatibility. Do not expand it into a hidden router, hidden repair path, or mutation policy layer without a separate design review and Tier 2/3/5 evidence.
- TurnPlan Advisor is shadow-only until a separate promotion milestone proves
  0 preview/apply mistakes, 0 save mistakes, 0 unsupported/raw-YAML executable
  mistakes, better fair-corpus accuracy than deterministic TurnPlan, and
  acceptable latency. Promotion must not reintroduce hardcoded semantic phrase
  dictionaries in runtime code.
- Vector/hybrid retrieval must remain read-only. It may return candidate blocks/docs/chunks with provenance and scores, but it must not return transactions, params payloads, insert arguments, save instructions, hidden recipes, tutorial-derived defaults, or any mutation authorization.

## Current Status

Local alpha is stable for bounded inspect/search/help/preview/change workflows
on copied `.grc` graphs.

- Default model-facing runtime surface is MVP wrappers only:
  `inspect_graph`, `search_blocks`, `search_help`, `change_graph`.
- Legacy low-level tools remain internal/compatibility-only.
- Advisor is shadow-only and does not control default runtime routing.
- Vector retrieval is frozen/read-only and does not authorize mutation.
- Scope statement: beta-ready for bounded workflows on copied graphs, not
  production autonomy.
- Current tracked evidence in this checkout:
  - `reports/BETA_READY_STATUS.md`
  - `reports/BETA_PACKAGING_HARDENING_STATUS.md`
  - `reports/MAINTENANCE_STATUS_2026-05-03.md`
  - `reports/MVP_WRAPPER_EFFICIENCY_REPORT.md`
  - `reports/dogfood/MVP_WRAPPER_CONTROLLED_DOGFOOD_2026-05-03.md`
  - `reports/retrieval/vector_eval_governed_metadata.json`

## Known Limits

- The default 2B model is reliable for summarize, inspect, search, describe, validate, save, preview, raw-YAML refusal, and simple parameter edits.
- Natural-language insertion is bounded and safe, but may clarify or reject instead of mutating.
- Complex multi-step graph creation is still model-limited.
- Exact natural-language disconnection requests route through
  `remove_connection` when a connection ID can be provided or resolved from
  endpoint hints; GNU-invalid end states roll back and are classified as
  nonrecoverable.
- Exact rewires are atomic remove-plus-add transactions. Ambiguous old-edge or
  new-endpoint hints clarify and stale choices are rejected after graph
  changes. A failed preflight or GNU-invalid rewire leaves the live graph
  unchanged; partial disconnects are not committed.
- Endpoint candidate resolution is clarification-backed, not automatic
  guessing. Ambiguous endpoint matches ask the user, and stale selections are
  rejected after graph changes.
- Copying structured fields from one tool output into another is not consistently reliable with the 2B model.
- Runtime correction now handles schema-level malformed mutation calls with one typed retry through the model, restricted to recovery-policy allowed tools. The promoted selector block-parameter live case exercises this path and still validates through `apply_edit` plus `grcc`.
- Valid installed examples with mixed stream/message port identifiers now load through `GrcAgent`; connection ordering normalizes port sort keys instead of comparing unlike Python types.
- Natural-language state edits are verified by the tool layer and guarded by typed routing. The default 2B model may still confuse "disable block" with removal, but that mismatch is now blocked before mutation instead of reaching graph preflight.
- Vague user goals may now clarify or preview instead of mutating. This is intentional: safe false-blocks are preferred over guessing topology changes with a 2B model.
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

Use dogfooding evidence before broadening runtime behavior:

```bash
uv run grc-agent dogfood record "..." --source real_user --task-type param_edit --failure-category routing_failure --reproducible
uv run grc-agent dogfood report --json
```

Dogfood records may come from `real_user`, `eval`, `manual_review`,
`installed_example`, or `user_graph` observations. A cluster is only a generic
patch candidate after repeated evidence, cross-source evidence, or a
STOP_THE_LINE safety event. The intake path must never become an automatic
metadata, prompt, tool-order, or runtime-change pipeline.

## Active Gate Tiers

Fast default gate (normal development):

```bash
uv run ruff check src/ tests/
uv run ruff check
uv run python -m unittest
uv run python -m tests.retrieval_eval.vector_regression
```

Live quick gate (runtime/model-facing changes only):

```bash
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
uv run python -m tests.llama_eval.tier5_adversarial --quick
```

Release gate (release claims/default-routing changes):

- Tier 2/3/4/5 with `--n-runs 3`
- `tests.llama_eval.release_dashboard` over persisted results

Advisor/model bakeoffs are explicit research-only scripts and are not part of
default verification.

Persisted release dashboard example:

```bash
uv run python -m tests.llama_eval.release_dashboard \
  --results-path /tmp/grc-agent-live-runs.json \
  --min-runs-per-case 3
```

Example release-candidate evidence commands (write to your own result paths):

```bash
uv run python -m tests.llama_eval.tier2_release --n-runs 3 --results-path /tmp/tier2_n3.json
uv run python -m tests.llama_eval.tier3_multiturn --n-runs 3 --results-path /tmp/tier3_n3.json
uv run python -m tests.llama_eval.tier4_external_examples --n-runs 3 --results-path /tmp/tier4_n3.json
uv run python -m tests.llama_eval.tier5_adversarial --n-runs 3 --results-path /tmp/tier5_n3.json
uv run python -m tests.llama_eval.release_dashboard \
  --results-path /tmp/tier2_n3.json \
  --results-path /tmp/tier3_n3.json \
  --results-path /tmp/tier4_n3.json \
  --results-path /tmp/tier5_n3.json \
  --min-runs-per-case 3 > /tmp/release_dashboard.json
```

Vector retrieval evals compare lexical and vector retrieval on paraphrase
queries, exact block IDs, manual/tutorial conceptual queries, false-positive
traps, citation accuracy, latency, deterministic record rebuilds, and safety
checks proving retrieval cannot expose mutation tools or authorize graph
changes.
- `tests.retrieval_eval.vector_regression` is the frozen vector v1 no-LLM
  regression gate. It requires vector hits >=276, exact-ID misses 0,
  false-positive failures 0, source-type misses 0, safety 290/290, provenance
  290/290, and deterministic rebuild pass true. It reports lexical hits but
  does not hard-fail on the lexical count.
- Offline embedding/model bakeoffs are research-only. Runtime default remains
  `BAAI/bge-small-en-v1.5` until evidence justifies a change.

## Backlog

- Continue copied private/user graph intake via the controlled beta workflow
  in `reports/BETA_READY_STATUS.md`; patch only STOP_THE_LINE issues or repeated
  generic failures across unrelated graphs.
- Keep expanding Tier 4 only with installed-example cases that pass repeated live evidence; any future pre-turn setup must use public verified tools, persist setup calls in the report, and validate the graph before the measured turn.
- Dogfood held-out installed examples and user graphs with structured intake before adding more synthetic eval cases. Classify failures as routing, argument-copying, preflight false reject, unsafe mutation risk, `grcc` failure, save/reload mismatch, confusing clarification, retrieval miss, tool error, or other.
- Expand the typed `TurnPlan` coverage only from repeated generic evidence; do not recover wrong destructive operations by inventing hidden repairs.
- Experiment with llama.cpp `response_format=json_schema` only for an optional finite-label router if deterministic `TurnPlan` coverage proves insufficient. That router must output intent labels only, never transactions.
- Move assistant-text fallback parsing behind `GrcAgent` without behavior drift.
- Keep vector retrieval vector-only until eval evidence justifies hybrid sparse search or reranking.
- Expand manual retrieval quality and coverage for explanation-only answers without making it mutation-adjacent.
- Persist accepted release-dashboard artifacts in a stable location when cutting tagged releases.
- Keep `block_uid` mutation limited to verified `target_ref` for block-local
  param/state edits. Do not add UID targeting for connections, rewires,
  add-block, or free-form text.
- Add a clarification-backed endpoint disconnect flow that resolves exact
  source/destination endpoint candidates, rejects stale revisions, and clarifies
  on multiple matches before any mutation.
