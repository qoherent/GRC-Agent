# GRC Agent Blueprint

This is the current high-level design contract. It is intentionally concise; old phase reports were removed because they were stale evidence logs, not durable product docs.

## Status

GRC Agent is a local assistant for GNU Radio Companion `.grc` graphs. It is built around explicit tool calls, typed validation, `grcc`, rollback, and copied-graph safety.

Current classification:

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`, `R4A_INSERT_BLOCK_ON_CONNECTION`, `R4B_REMOVE_BLOCK`, `R4C_ADD_VARIABLE`, `R5_SAVE_LOAD`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`
- Runtime: not production-ready

The default model-facing surface is the six-wrapper MVP profile only. Low-level graph tools remain internal.

## Safety Contract

- Never edit raw `.grc` YAML directly.
- Never mutate originals under installed example paths; copy graphs first.
- Preview must never mutate.
- Failed schema validation, preflight, or `grcc` validation must not commit.
- Save requires explicit user intent.
- Docs/RAG are explanation-only and never mutation authority.
- Ambiguous graph targets clarify instead of first-match mutation.
- `grcc` remains final graph-validity authority.

## Model-Facing Wrappers

### `inspect_graph`

Purpose: read-only graph inspection.

Args:

- `view`: required; one of `overview`, `details`
- `targets`: required string array; ignored for `overview`, one to five graph-local targets for `details`
- `params`: schema-visible string array; ignored for `overview`; for `details`, pass `["all"]` for useful visible parameters or exact parameter names to filter. Missing or empty `params` defaults to bounded useful parameters.
- `debug`: internal eval/dev telemetry only, not model-facing

Expected output:

- stable top-level fields: `ok`, `view`, `state_revision`, `complete`, `summary`, `targets`, `target_matches`, `params_filter`, `editable_handles`, `ambiguity`, `truncation`, `validation_status`, `errors`
- `overview`: compact whole-graph summary, counts, blocks/connections preview, validation status
- `details`: resolved graph-local targets, selected/current params, compact connection context, guarded editable handles with `target_ref` and state revision
- ambiguous or missing details targets return candidates/errors and no guessed mutation
- details calls with missing or empty `params` default to `["all"]`; overview is read-only and normalizes stray `targets`/`params`

Internal subtools/data:

- `summarize_graph`
- editable parameter candidate index
- session graph metadata and connection summaries

Design lessons from the redesign:

- The model-facing schema is an intent contract, not an internal query API.
- Required fields are still validated at runtime because local models can omit them; safe read-only defaults are filled before validation where omission is harmless.
- Overview is forgiving because it is read-only; details is strict because it can expose mutation-ready handles.
- Runtime owns budgets, truncation, target matching, and handle generation.
- Tool output must include labels, parameter keys, current values, target refs, state revisions, ambiguity, and truncation so the model is not forced to infer graph facts.
- Validation is not a model-facing inspect mode; validation runs at load, after mutation, and before save, then appears as status metadata.

### `search_blocks`

Purpose: find installed GNU Radio catalog blocks.

Args:

- `query`: required
- `k`: optional result bound
- `enrich`: optional block-description enrichment
- `debug`: optional telemetry

Expected output:

- compact ranked block candidates with block IDs, names, summaries, provenance, and warnings

Internal subtools/data:

- lexical/vector block retrieval
- catalog metadata
- optional `describe_block` enrichment

### `ask_grc_docs`

Purpose: explanation-only grounded docs answers.

Args:

- `question`: required
- `k`: optional source count
- `focus`: optional topic hint
- `debug`: optional telemetry

Expected output:

- concise answer
- source list
- `insufficient_evidence` when local docs do not support an answer
- no mutation payloads

Internal subtools/data:

- local GNU Radio wiki/tutorial corpus under `docs/wiki_gnuradio_org/`
- deterministic grounded-answer builder
- optional helper synthesis only when explicitly enabled for research

### `change_graph`

Purpose: the only model-facing graph-content mutation wrapper.

Required args:

- `dry_run`
- `user_goal`
- `operation_kind`

Common args:

- `target_ref`
- `instance_name`
- `state_revision`
- `debug`

Supported `operation_kind` values:

- `set_param`
- `set_state`
- `add_variable`
- `disconnect`
- `rewire`
- `insert_block`
- `remove_block`
- `clarify`
- `unsupported`
- `auto_insert` is experimental/non-gating

`set_param` args:

- `instance_name` or guarded `target_ref`
- `param_key`
- `param_value`
- optional `expected_old_value`

Natural `set_param` resolution is graph-local and metadata-driven only. It may resolve through loaded graph instance names, block types, catalog labels, parameter keys/labels, current values, and explicit old-value guards. It does not use phrase dictionaries, docs/RAG, tutorials, hidden retries, or raw YAML. If zero or multiple candidates match, it clarifies. If `expected_old_value` does not match the active graph, it refuses without mutation.

Other operation args:

- `set_state`: `instance_name` or `target_ref`, `state`
- `add_variable`: `variable_name`, `variable_value`
- `disconnect`: exact `connection_id` or uniquely resolved endpoints
- `rewire`: `connection_id`, `state_revision`, `new_src_block`, `new_src_port`, `new_dst_block`, `new_dst_port`
- `insert_block`: `connection_id`, `block_id`/`candidate_id`/`insert_block`, `instance_name`, optional `insert_params`
- `remove_block`: `instance_name` or `target_ref`, optional explicit detach controls

Expected output:

- `ok`, `dry_run`, `operation_summary`
- graph delta and active session state when applicable
- validation result for committed mutations
- clarification payload for ambiguous or underspecified requests
- rollback/refusal details for failed validation

Internal subtools/data:

- `propose_edit` for preview
- `apply_edit` for committed mutation
- transaction normalizer
- validation rules and preflight checks
- `grcc`
- graph history/checkpoints

Recommended next refactor focus:

- Keep one model-facing mutation wrapper for now.
- Make the internal implementation operation-kind oriented: `set_param`, `set_state`, `add_variable`, `disconnect`, `rewire`, `insert_block`, `remove_block`, `clarify`, `unsupported`.
- Keep schema narrowing so the model sees only relevant mutation fields when possible.
- Return clearer validation errors to the model, such as missing required arg, stale state revision, ambiguous target, old-value mismatch, and failed `grcc`.
- Require graph-local authority for edits: exact identifiers, guarded target refs, or uniquely resolved metadata candidates from the active graph.
- Preserve existing safety: no docs/RAG authority, no raw YAML, no hidden retries, no first-match mutation, and rollback on failed validation.

### `save_graph_explicit`

Purpose: explicit lifecycle save only.

Args:

- `path`: optional destination; omit only for saving the current loaded path
- `overwrite`: optional
- `debug`: optional telemetry

Expected output:

- save status
- path
- validation result
- active session dirty state

Internal subtools/data:

- `save_graph`
- path-safety policy
- atomic write path
- validation before save

### `load_graph_explicit`

Purpose: explicit lifecycle load/open only.

Args:

- `path`: required
- `debug`: optional telemetry

Expected output:

- load status
- active session summary
- path-safety result

Internal subtools/data:

- `load_grc`
- path-safety policy

## Internal Tool Boundary

Internal tools include graph creation/loading, summaries, catalog retrieval, block descriptions, manual search, insert suggestions, connection removal/rewire, edit proposal/application, validation, and raw save. They are implementation primitives, not default model-facing chat tools.

The model sees only the six wrappers above in MVP chat mode.

## Agent Loop

1. Load or create one active `FlowgraphSession`.
2. Build compact model messages from system policy, session snapshot, recent history, and bounded tool results.
3. Ask llama.cpp for a response with the six wrapper schemas.
4. Validate every requested tool call against wrapper schemas.
5. Execute tool calls serially.
6. For mutations, route through `change_graph`, transaction validation, preflight, `grcc`, and rollback/commit.
7. Append raw requested calls, executed calls, tool results, deltas, and validation state to history/trace.
8. Stop after bounded tool rounds or when the assistant returns final text.

Fallback free-text parsing is disabled for the MVP runtime. If the model cannot produce a valid call, the turn fails closed or asks for clarification.

## Context Handling

Context is compacted by tool output design, not by hiding raw tool calls.

- `inspect_graph` has only `overview` and `details`, with explicit truncation and ambiguity metadata.
- `details` exposes mutation-ready handles only for resolved, requested targets.
- Tool results are compacted for future model turns, while raw call/result history remains traceable.
- Health checks verify desired vs actual llama context; current target is 120000 tokens when supported.
- `max_tokens` limits generation length only; it is not used as a compression strategy.

## Mutation Safety

All graph-content mutations pass through `change_graph`.

Commit path:

1. schema validation
2. operation dispatch
3. graph-local target validation
4. transaction normalization
5. preflight checks
6. apply on a candidate copy
7. `grcc` validation
8. atomic commit or rollback

Preview path uses proposal/candidate logic and must leave the live graph unchanged.

Save/load are lifecycle operations, not graph-content mutations. `/save` in the CLI bypasses the model and calls `save_graph_explicit` directly.

## CLI Chat UX

- `uv run grc-agent chat <copy.grc>` starts interactive chat on a copied graph.
- Bare `uv run grc-agent` enters chat only in an interactive TTY; non-interactive use prints help and exits command-safe.
- Startup/reuse of llama.cpp is explicit and health-verified.
- Normal chat prints assistant text plus concise operation summaries.
- Full history is hidden by default; use `/history`.
- Use `/save [path] [--overwrite]` for deterministic manual save.

## Eval Harness

Deterministic tests and live/model evals are separate.

Deterministic gates:

- `uv run ruff check src/ tests/`
- `uv run python -m unittest <targeted modules>`
- `uv run python -m tests.retrieval_eval.vector_regression`
- `uv run python -m tests.retrieval_eval.grc_docs_answer_eval`
- `uv run grc-agent doctor`
- `uv run grc-agent health`
- `uv run grc-agent release-manifest`

Live dashboards exercise llama.cpp routing and behavior by suite. They preserve raw requested/executed tool calls, separate task success from runtime safety, and fail closed on forbidden raw/internal tool history.

Production gameplay harnesses run copied graphs only, record full artifacts, apply deterministic local judging, and keep Ollama/dummy users out of mutation authority and judging.

The full `unittest` discovery currently contains about 950 tests and is slow because it includes integration-style graph loading, `grcc` validation, eval harness logic, CLI loops, and production gameplay tests. Use targeted tests during iteration and reserve full runs for release candidates.

## Docs/RAG

`docs/wiki_gnuradio_org/` is a local explanation corpus. It can support user education and docs QA, but it cannot authorize mutations or provide hidden graph recipes.

Docs-answer quality is evaluated separately from mutation safety. Groundedness and relevance matter; misleading answers and mutation leakage must remain zero.

## Runtime Readiness

End-to-end runtime readiness requires:

- package installed
- GNU Radio Python import works
- `grcc` works
- retrieval catalog ready
- vector index ready when retrieval is required
- llama.cpp reachable
- actual llama context verified
- six MVP model-facing wrappers only

CUDA-enabled llama.cpp on `CUDA0` is the default NVIDIA runtime path. The local launcher passes `--device CUDA0 --gpu-layers 999` explicitly; if `llama-server --list-devices` does not show `CUDA0`, model-backed chat is not runtime-ready.

## Documentation Set

Durable docs kept under `docs/`:

- `BLUEPRINT.md`: architecture and safety contract
- `QUICKSTART.md`: setup and use
- `ISSUE_INTAKE.md`: report template
- `DEMO_VIDEO.md`: demo workflow
- `capability_classification.json`: current capability labels
- `wiki_gnuradio_org/`: local GNU Radio tutorial/reference corpus
