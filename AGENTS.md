# AGENTS.md

## Mission

GRC Agent is a local assistant for GNU Radio Companion `.grc` graphs. It should inspect the active graph, explain what it sees, make only validated tool-based edits, verify outcomes, and ask for clarification when the graph evidence is insufficient.

Autonomy must come from typed state, explicit wrappers, deterministic validation, `grcc`, rollback, and measured behavior. It must not come from raw YAML patches, hidden retries, prompt tricks, tutorial recipes, broad synonym dictionaries, or fixture-specific shortcuts.

## Current Runtime Truth

- Runtime is not production-ready.
- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`.
- Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`, `R4A_INSERT_BLOCK_ON_CONNECTION`, `R4B_REMOVE_BLOCK`, `R4C_ADD_VARIABLE`, `R5_SAVE_LOAD`.
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`.
- Default chat surface is exactly six MVP wrappers: `inspect_graph`, `search_blocks`, `ask_grc_docs`, `change_graph`, `save_graph_explicit`, `load_graph_explicit`.
- Low-level graph, catalog, validation, save, and transaction tools are internal primitives, not model-facing chat tools.
- CUDA llama.cpp on `CUDA0` is the preferred NVIDIA path. Health must prove llama reachability and actual context before model-backed runtime is treated as ready.

## Safety Contract

- Never edit raw `.grc` YAML directly.
- Mutate copied graphs, not installed examples or user originals.
- `change_graph` is the only model-facing graph-content mutation wrapper.
- Save/load are explicit lifecycle wrappers, not implicit side effects.
- Preview must never mutate.
- Failed schema validation, preflight, or `grcc` validation must not commit.
- Failed edits must roll back atomically.
- Save requires explicit user intent and validation of the current graph revision.
- Loading validates before replacing the active session.
- Docs/RAG are explanation-only and never mutation authority.
- Ambiguous targets clarify using real graph candidates; no first-match mutation.
- `grcc` remains final graph-validity authority.

## Tool Design Lessons

The `inspect_graph` redesign is the template for future wrapper work.

- Tool schemas should expose user intent, not internal query APIs.
- Keep model-facing arguments few, required, and easy to choose.
- Runtime code owns budgets, truncation, target resolution, handle generation, and safe defaults.
- A read-only overview can be forgiving and ignore stray narrowing fields.
- Targeted details must be strict about targets: no target, no details dump.
- Use semantic selectors such as `params=["all"]` or exact parameter names instead of low-level budget flags; if the model omits `params`, runtime safely defaults details to `["all"]` within bounded output.
- Tool output must include graph-local facts the model can actually use: labels, parameter names, current values, target refs, state revisions, connections, ambiguity, and truncation.
- Required fields must still be validated at runtime. Local models can omit them.
- Raw requested tool calls and raw tool outputs must be inspected when diagnosing behavior; pass/fail summaries are not enough.
- Validation and lifecycle policy belong in runtime code, not as another model-facing inspect mode.

## Wrapper Contracts

### `inspect_graph`

Read-only graph inspection.

- Args: `view`, `targets`, `params`.
- `view`: `overview` or `details`.
- `overview`: whole-graph summary; runtime ignores `targets` and `params`.
- `details`: requires one to five graph-local targets; `params` may be `["all"]`, exact parameter names, omitted, or empty. Missing/empty means bounded useful parameters.
- Returns state revision, validation status, compact facts, ambiguity/truncation metadata, and editable handles only for resolved targets.
- Does not validate on demand as a separate model-facing mode; validation is surfaced from lifecycle/mutation state.

### `search_blocks`

Read-only block catalog discovery.

- Use for broad block discovery, candidate lookup, and catalog metadata.
- It does not authorize mutation. Mutation still requires graph-local inspection, target refs or exact identifiers, and `change_graph`.

### `ask_grc_docs`

Read-only grounded docs answering.

- Use for conceptual GNU Radio help.
- Returns sources and `insufficient_evidence` when local evidence is weak.
- Never returns mutation authority, hidden recipes, or graph edit payloads.

### `change_graph`

The only model-facing graph-content mutation wrapper.

- Requires `dry_run`, `user_goal`, and `operation_kind`.
- Supported operation kinds include `set_param`, `set_state`, `add_variable`, `disconnect`, `rewire`, `insert_block`, `remove_block`, `clarify`, and `unsupported`.
- `set_param` supports graph-local metadata-driven natural resolution only when exactly one candidate matches and a new value is explicit.
- `expected_old_value`, when supplied, must match the current graph value before mutation.
- No docs/RAG authority, hidden retries, raw YAML, or broad phrase dictionaries.

### `save_graph_explicit`

Explicit save lifecycle wrapper.

- Save only when the user asks.
- Validate current revision before saving.
- CLI `/save` bypasses the model and calls the save path directly.

### `load_graph_explicit`

Explicit load lifecycle wrapper.

- Validate loaded graph before activating it.
- If load/validation fails, preserve the current active session.

## Agent Loop

1. Maintain one active `FlowgraphSession`.
2. Build compact messages from policy, session state, recent history, and bounded tool outputs.
3. Send six wrapper schemas to llama.cpp.
4. Validate every requested tool call before execution.
5. Execute tools serially.
6. Route mutations through transaction normalization, preflight, candidate apply, `grcc`, and commit/rollback.
7. Store raw requested calls, executed calls, tool results, graph deltas, validation state, and save/load events in traceable history.
8. Stop after bounded tool rounds or final assistant text.

Fallback free-text parsing is disabled for MVP runtime. Invalid tool calls fail closed or produce clarification.

## Context Handling

- Keep context compact through wrapper output design, not low `max_tokens`.
- Preserve raw tool history in traces even when model-facing history is compacted.
- Surface truncation and ambiguity explicitly.
- Include state revisions on graph-local handles.
- Reject stale target refs or stale expected old values.
- Desired llama context target is 120000 tokens; `doctor`/`health` must verify actual context before claiming large-context behavior.

## Engineering Rules

- Prefer the smallest correct change.
- Measure repeated generic failures before changing schemas, prompts, tool order, or runtime behavior.
- Do not add broad natural-language regexes or phrase dictionaries.
- Do not add new model-facing tools unless repeated evidence proves the existing surface is insufficient.
- Keep `llama_server.py` transport/loop oriented; graph policy belongs behind `GrcAgent`, wrappers, sessions, validation, and transactions.
- Use package imports under `src/grc_agent/`.
- Keep `pyproject.toml` authoritative.
- Use `uv run` for commands.
- Use stdlib `unittest` for deterministic tests.

## Testing

- Run targeted tests while iterating.
- Run `uv run ruff check src/ tests/` after code/test edits.
- Run `uv run python -m unittest <target>` for focused regression proof.
- Run full `uv run python -m unittest` after runtime, schema, prompt, validation, transaction, or wrapper behavior changes.
- Run live llama evals only when model-facing behavior changes and targeted deterministic tests pass.
- Do not delete or weaken safety tests.
- Reclassify or delete a test only when it is redundant, stale, or demonstrably checking the wrong contract.
- Prefer semantic assertions: tool call shape, graph diff, validation result, rollback/no-mutation behavior, save/load outcome, and user-facing answer quality.

## Docs/RAG

- Keep `docs/wiki_gnuradio_org/` explanation-scoped.
- Catalog metadata and active graph inspection are authorities for block IDs, ports, parameters, defaults, and graph-local facts.
- Tutorials are not runtime recipes.
- Docs-answer quality is evaluated separately from mutation safety.
- Helper synthesis remains optional research unless explicitly enabled.

## Durable Docs

- `docs/BLUEPRINT.md` is the current source of truth for architecture, wrappers, safety, context, evals, and runtime status.
- `docs/QUICKSTART.md` is procedural setup/use guidance.
- `docs/ISSUE_INTAKE.md` is the support/debug report template.
- `docs/DEMO_VIDEO.md` documents the reproducible demo workflow.
- Update docs when wrapper contracts, safety boundaries, eval gates, runtime requirements, or capability labels change.

## Next Wrapper Focus

The next wrapper to redesign should be `change_graph`.

Reason: `inspect_graph` now produces cleaner graph-local facts and handles. The remaining high-value work is making the mutation contract easier for the model while keeping one mutation boundary. Refactor internally by operation kind, not by exposing many new model tools. Tighten operation-specific validation, stale-state checks, old-value guards, clarification payloads, and error messages returned to the model for one safe retry or user clarification.
