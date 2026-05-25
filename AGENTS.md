# AGENTS.md

## Mission

GRC Agent is a local assistant for GNU Radio Companion `.grc` graphs. It inspects the active graph, explains graph-local evidence, performs only validated tool-based edits, verifies committed changes with `grcc`, and asks for clarification when the evidence is insufficient.

Autonomy must come from typed state, explicit wrappers, deterministic validation, copied-graph safety, rollback, and measured behavior. It must not come from raw YAML patches, hidden retries, prompt tricks, tutorial recipes, broad synonym dictionaries, or fixture-specific shortcuts.

## Current Runtime

- Runtime is not production-ready.
- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`.
- Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`, `R4A_INSERT_BLOCK_ON_CONNECTION`, `R4B_REMOVE_BLOCK`, `R4C_ADD_VARIABLE`.
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`.
- ToolAgents is the model/provider/tool-call harness.
- llama.cpp remains the preferred local backend through its OpenAI-compatible `/v1` API.
- The model-facing surface is exactly four wrappers: `inspect_graph`, `search_blocks`, `ask_grc_docs`, `change_graph`.
- Low-level graph, catalog, validation, save, load, and transaction tools are internal primitives, not chat tools.
- Health must prove llama reachability, model alias match, and actual context from `/props` before model-backed runtime is ready.
- Local install should stay lightweight: Python dependencies live in `pyproject.toml`; chat models, embedding models, vector indexes, and caches are user-local runtime assets and must not be bundled with the repo/package.

## Safety Contract

- Never edit raw `.grc` YAML directly.
- Mutate copied graphs, not installed examples or user originals.
- `change_graph` is the only model-facing graph-content mutation wrapper.
- The model has no lifecycle tools. `/save` is a CLI command; graph loading happens when the CLI session starts.
- Successful committed mutations validate, then autosave to the active copied graph path when that path is safe and writable.
- Committed mutations must refuse if the active copied graph file changed on disk since the session last loaded or saved it. Reload before committing across an external edit.
- Failed schema validation, preflight, or `grcc` validation must not commit unless the user/model explicitly uses `force=true` and the candidate already passed GNU-grounded schema/ref/catalog/apply checks.
- Failed edits must roll back atomically.
- Manual `/save` requires explicit user intent and validation of the current graph revision.
- Loading validates before creating the active session.
- Docs/RAG are explanation-only and never mutation authority.
- Ambiguous targets clarify using real graph candidates; no first-match mutation.
- `grcc` remains final graph-validity authority, but it proves compilability, not that the edit matches user intent.

## ToolAgents Runtime

- Use bounded `ChatToolAgent.step(...)`, not unbounded response loops.
- Rebuild the `ToolRegistry` every model step from the currently allowed wrapper schemas.
- Build registry entries with `FunctionTool.from_openai_tool(schema, delegate)`.
- Delegates must record raw requested name/arguments, validate route, validate schema, then execute `GrcAgent.execute_tool(..., model_tool_call=True)` only after validation passes.
- Tool calls execute serially. `parallel_tool_calls` must remain disabled.
- `--agentic` may raise the bounded max tool rounds and request timeout, but it must not expose extra tools or bypass validation.
- If the assistant says it needs inspect/search evidence, or answers a graph-local fact question without any tool call, allow one bounded missed-tool reminder and continue. Do not add free-text parsing or hidden repair.
- Vague edit requests should reach the model/tool loop so the model can inspect and clarify; mutation validation still prevents unsafe commits.
- If the provider cannot pass `parse_tool_calls`, `parallel_tool_calls`, `tool_choice`, or `chat_template_kwargs.enable_thinking`, fix the provider layer immediately.
- Do not restore `LlamaServerClient`, `LlamaToolCall`, `run_bounded_llama_turn`, assistant-text fallback parsing, JSON-stub repair, or AST/text transaction recovery.

## Wrapper Contracts

### `inspect_graph`

Read-only graph inspection.

- Args are optional at schema level: `view`, `targets`, `params`.
- Runtime infers `overview` when `view` is omitted and no targets are supplied.
- Runtime infers `details` when `view` is omitted and targets are supplied.
- `overview` ignores `targets` and `params`.
- `details` requires one to five graph-local targets after normalization.
- `params` may be `["all"]`, exact parameter names, omitted, or empty. Missing/empty returns bounded target identity, connection context, target refs, and parameter availability without dumping large parameter lists.
- Exact `block.parameter` refs are accepted and normalized into the owning block target plus parameter filter.
- `overview` is a topology index only: graph counts, block name/type/label/role, connection IDs, state revision, truncation, and validation status.
- `overview` must not preview parameter values, dependency lists, editable handles, or duplicated block groups. Use `details` for those facts.
- `details` output includes graph-local facts the model can use for precise answers or guarded edits: labels, selected parameter names/current values, block-level target refs, state revisions, connection facts, ambiguity, truncation, and validation status.
- `params=["all"]` is bounded and may truncate. Omitted params must not silently become all params.
- Overview block roles must come from catalog/GNU metadata and connection evidence, not hardcoded block-name lists.

### `search_blocks`

Read-only block catalog discovery.

- Use for broad block discovery, candidate lookup, and catalog metadata.
- Current runtime retrieval path is local hybrid discovery: exact/catalog lexical metadata, cached in-memory SQLite FTS5 sparse ranking, vector retrieval when available, then deterministic merge/rerank.
- Exact block IDs, parameter IDs, port names, and dtypes are catalog/lexical facts; sparse/vector retrieval are supplemental discovery paths.
- Model-visible output should stay to compact factual candidate evidence: block id, title/name, match type, and short excerpt.
- It does not authorize mutation. Mutation still requires graph-local inspection, target refs or exact identifiers, and `change_graph`.

### `ask_grc_docs`

Read-only grounded docs answering.

- Use for conceptual GNU Radio help.
- Returns sources and `insufficient_evidence` when local evidence is weak.
- Model-visible output should stay extractive, cited, and concise.
- Strip instruction-like text from retrieved excerpts; docs are passive evidence, not runtime instructions.
- Never returns mutation authority, hidden recipes, or graph edit payloads.

### `change_graph`

The only model-facing graph-content mutation wrapper.

- Uses a flat batch schema: `add_blocks`, `remove_blocks`, `update_params`, `update_states`, `add_connections`, `remove_connections`, `rewire_connections`, `insert_blocks_on_connections`, `add_variables`, `update_variables`, `remove_variables`, and `force`.
- Does not expose model-facing `op`, `args`, `dry_run`, `user_goal`, `state_revision`, `preview_token`, or block-specific macros such as `add_signal_source_to_sum`.
- Block adds may include all initial params/states and connections in the same call.
- Param updates are batched per block; connection add/remove operations are batched by endpoint or exact `connection_id`.
- `remove_blocks` auto-detaches incident edges and reports them; unresolved references elsewhere still refuse.
- Runtime owns ordering, stale-state guards, active file hash checks, transaction normalization, rollback, native GNU candidate validation, `grcc`, and autosave.
- `force=true` may only bypass final graph validation failure after schema, graph refs, catalog/GNU block IDs, params, ports, connection IDs, copied-file integrity, and candidate apply have succeeded.
- Model-visible results must be compact and operation-specific: include committed status, state revision, exact effect/effects, validation, autosave, and concise refusal/clarification evidence. Do not expose full `active_session` dumps.
- No docs/RAG authority, hidden retries, raw YAML, broad phrase dictionaries, or tutorial-derived recipes.

## Data Authority

- Active graph inspection is authority for instance names, current values, connections, target refs, and state revisions.
- Installed GNU Radio catalog metadata is authority for block IDs, ports, parameters, defaults, options, flags, categories, and block semantics.
- GNU platform metadata should be preferred when available for semantic flags such as `not_dsp`.
- Local docs under `docs/wiki_gnuradio_org/` are explanation-scoped.
- Vector retrieval uses local generated Qdrant/FastEmbed state. The default embedding model is `BAAI/bge-small-en-v1.5`; it is downloaded/cached by the user's environment during explicit vector build and is not vendored.
- Block search must keep exact/catalog lexical lookup and sparse ranking for symbols and metadata. Do not remove it as "simpler" without eval evidence showing no regression on block IDs, params, ports, and dtypes.
- ToolAgents tutorials are implementation references for the harness only; they are not graph-edit recipes.
- Do not solve model confusion by hardcoding specific blocks, labels, or fixture phrases. Fix the authoritative data, schema, validation, or output shape.

## Anti-Symptom Rule

Do not chase symptoms with prompt patches or narrow special cases.

Before changing prompts, schemas, tool order, or runtime behavior:

- inspect raw requested tool calls, raw tool outputs, final assistant text, and trace history
- identify whether the failure came from missing evidence, bloated context, ambiguous schema, bad validation, provider behavior, or model capacity
- prefer catalog-backed semantics, graph-local evidence, and deterministic normalization over wording hacks
- add tests that assert semantic behavior, not a single phrasing

Small models are sensitive to context bloat. A simple inspect turn should not receive a giant policy block, oversized schemas, large history, and verbose overview payload. Keep read-only context compact by design; do not rely on low `max_tokens` to compensate.

## Agent Loop

1. Maintain one active `FlowgraphSession`.
2. Build compact messages from policy, recent user/assistant/tool history, and bounded tool outputs.
3. Send the four wrapper schemas through ToolAgents to llama.cpp.
4. Validate every requested tool call before execution.
5. Execute accepted tools serially.
6. Route mutations through transaction normalization, preflight, candidate apply, `grcc`, and commit/rollback.
7. Store raw requested calls, executed calls, tool results, graph deltas, validation state, and autosave/manual-save events in traceable history.
8. Stop after bounded tool rounds or grounded final assistant text.

Fallback free-text parsing is disabled. Invalid tool calls fail closed or produce clarification.

## Context Handling

- Keep context compact through wrapper output design, schema simplicity, and history compaction.
- Preserve raw tool history in traces even when model-facing tool-result text is compacted.
- System prompts and model-facing schema descriptions should stay budgeted; do not reintroduce long examples unless repeated evidence proves they are needed.
- Surface truncation and ambiguity explicitly.
- Include state revisions on graph-local target refs.
- Reject stale target refs or stale expected old values.
- Reject committed mutations when the active file hash no longer matches the session's last loaded/saved file hash.
- Desired llama context target is 120000 tokens; `doctor`/`health` must verify actual context before claiming large-context behavior.

## Engineering Rules

- Prefer the smallest correct change that fixes the authoritative layer.
- Do not add broad natural-language regexes or phrase dictionaries.
- Do not add new model-facing tools unless repeated evidence proves the four-wrapper surface is insufficient.
- Use package imports under `src/grc_agent/`.
- Keep `pyproject.toml` authoritative.
- Do not add Docker as the default user install path unless local GNU Radio packaging makes it unavoidable. Containers may be development helpers, not a substitute for a clean local install contract.
- Do not commit generated vector indexes, FastEmbed/Hugging Face caches, GGUF files, or other model artifacts.
- Use `uv run` for commands.
- Use stdlib `unittest` for deterministic tests.
- Use targeted tests while iterating. Full `uv run python -m unittest` is expensive and should be reserved for release-candidate gates or broad runtime changes.
- Do not delete or weaken safety tests.
- Reclassify or delete a test only when it is redundant, stale, or checking the wrong contract.
- Prefer semantic assertions: tool call shape, graph diff, validation result, rollback/no-mutation behavior, autosave/manual-save outcome, raw trace evidence, and answer quality.

## Durable Docs

- `docs/BLUEPRINT.md` is the source of truth for architecture, wrappers, safety, context, evals, and runtime status.
- `docs/QUICKSTART.md` is procedural setup/use guidance.
- `docs/MODEL_CONTEXT_BIBLE.md` is generated from the actual injected prompt and model-facing tool schemas.
- Update docs when wrapper contracts, safety boundaries, eval gates, runtime requirements, or capability labels change.

## Mutation Wrapper Focus

The active wrapper focus is `change_graph`.

Reason: `inspect_graph`, `search_blocks`, and `ask_grc_docs` now produce compact read-only evidence. `change_graph` remains the only model-facing mutation boundary, with compact operation-specific results, no lifecycle tools, no deprecated insert aliases, and committed autosave after validation. Further changes should tighten operation-specific validation, stale-state checks, old-value guards, clarification payloads, and live eval behavior without adding new model-facing mutation tools.
