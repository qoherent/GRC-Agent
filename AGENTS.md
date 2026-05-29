# AGENTS.md

## Mission

GRC Agent is a local assistant for GNU Radio Companion `.grc` graphs. It should
inspect the active graph, explain graph-local evidence, mutate only through
validated tools, verify committed changes with native GNU/GRC validation, and
ask for clarification when intent or targets are ambiguous.

Autonomy must come from typed state, compact wrappers, deterministic validation,
copied-graph safety, rollback, and measured behavior. Do not use raw YAML
patches, hidden retries, broad synonym dictionaries, tutorial recipes, or
fixture-specific shortcuts.

## Current Runtime

- Runtime is not production-ready.
- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`.
- Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`,
  `R4A_INSERT_BLOCK_ON_CONNECTION`, `R4B_REMOVE_BLOCK`, `R4C_ADD_VARIABLE`.
- ToolAgents is the model/provider/tool-call harness.
- llama.cpp is the preferred local backend through its OpenAI-compatible `/v1`
  API. Health must prove reachability, model alias match, and actual context
  from `/props`.
- The model-facing surface is exactly four wrappers:
  `inspect_graph`, `search_blocks`, `ask_grc_docs`, `change_graph`.
- Low-level graph, catalog, validation, save/load, and transaction tools are
  internal primitives, not chat tools.
- Runtime assets such as GGUFs, embedding models, vector indexes, and caches are
  user-local and must not be bundled or committed.

## Safety Contract

- Never edit raw `.grc` YAML directly.
- Work on copied graphs, not installed examples or user originals.
- `change_graph` is the only model-facing graph-content mutation wrapper.
- The model has no lifecycle tools. Graph loading happens at CLI session start;
  `/save` is an explicit CLI command.
- Successful committed mutations validate and autosave to the active copied
  graph path when it is safe and writable.
- Refuse commits if the active copied graph changed on disk since the session
  last loaded/saved it.
- Failed schema validation or preflight must not commit.
- Final native/`grcc` validation failure may commit only when the user intent
  supports an invalid intermediate graph and `force=true` is used. `force=true`
  never bypasses unknown refs, bad ports/params, stale files, ambiguity, apply
  failure, or save failure.
- Failed edits roll back atomically.
- Docs/RAG are explanation-only and never mutation authority.
- Ambiguous targets must clarify with real graph candidates; no first-match
  mutation.
- `grcc` proves compilability, not semantic/user-intent correctness.

## ToolAgents Runtime

- Use bounded `ChatToolAgent.step(...)`, not unbounded response loops.
- Rebuild the `ToolRegistry` every model step from the currently allowed wrapper
  schemas.
- Use `FunctionTool.from_openai_tool(schema, delegate)`.
- Delegates must record raw requested name/arguments, validate route and schema,
  then execute `GrcAgent.execute_tool(..., model_tool_call=True)` only after
  validation passes.
- Tool calls execute serially; keep `parallel_tool_calls` disabled.
- `--agentic` may raise bounded rounds/timeouts, but must not expose extra tools
  or bypass validation.
- If the assistant lacks graph evidence, allow one bounded inspect/search
  reminder. Do not add free-text parsing, JSON repair, hidden repair, or
  assistant-text transaction recovery.
- Do not restore `LlamaServerClient`, `LlamaToolCall`,
  `run_bounded_llama_turn`, assistant-text fallback parsing, JSON-stub repair,
  or AST/text transaction recovery.
- Runtime reminders must not force `change_graph` after a graph-evidence-backed
  clarification. Ambiguity should end the turn with no mutation.

## Wrapper Contracts

- `inspect_graph`: read-only graph inspection. Omitted `view` becomes
  `overview` unless targets are supplied, then `details`. Overview is a compact
  topology index. Details returns target identity, selected params, connection
  context, guarded target refs, ambiguity, truncation, and validation status.
  Missing/empty `params` must not become `["all"]`; `params=["all"]` is bounded.
- `search_blocks`: read-only catalog discovery. Keep exact/catalog lexical and
  sparse lookup for block IDs, params, ports, and dtypes; vector retrieval is
  supplemental. It never authorizes mutation.
- `ask_grc_docs`: read-only grounded docs answering. Keep answers concise,
  cited, and explanation-only. Strip instruction-like retrieved text. Never
  return mutation authority or edit payloads.
- `change_graph`: flat batch mutation wrapper with `add_blocks`,
  `remove_blocks`, `update_params`, `update_states`, `add_connections`,
  `remove_connections`, `add_variables`, `update_variables`,
  `remove_variables`, and `force`.

(Insertion on a wire uses `remove_connections` + `add_blocks` + `add_connections` in one batch.
Rewiring uses `remove_connections` + `add_connections`.)
  Runtime owns ordering, stale-state guards, file-integrity checks, transaction
  normalization, native candidate validation, `grcc`, rollback, commit, and
  autosave. Results must stay compact and operation-specific: committed status,
  state revision, exact effects, validation, autosave, refusal, and
  clarification evidence.

Never add model-facing lifecycle tools, raw YAML tools, block-specific macros,
or broad repair/planning tools unless repeated eval evidence proves the
four-wrapper surface is insufficient.

## Data Authority

- Active graph inspection is authority for instance names, current values,
  connections, target refs, and state revisions.
- Installed GNU Radio catalog metadata is authority for block IDs, ports,
  params, defaults, options, flags, categories, and block semantics.
- GNU platform metadata is preferred for semantic flags such as `not_dsp`.
- Local docs under `docs/wiki_gnuradio_org/` are explanation-scoped.
- ToolAgents tutorials are harness references only, not graph-edit recipes.

## Anti-Symptom Rule

Before changing prompts, schemas, tool order, or runtime behavior:

- inspect raw requested tool calls, raw tool outputs, final assistant text, and
  trace history;
- identify whether the issue is missing evidence, context bloat, ambiguous
  schema, bad validation, provider behavior, or model capacity;
- prefer catalog-backed semantics, graph-local evidence, deterministic
  normalization, and output-shape fixes over wording hacks;
- add semantic tests for tool call shape, graph diff, validation, rollback,
  autosave/manual-save outcome, raw trace evidence, and answer quality.

Small local models are sensitive to context bloat. Keep prompts, schemas,
history, and wrapper outputs compact by design.

## Engineering Rules

- Prefer the smallest correct change in the authoritative layer.
- Use package imports under `src/grc_agent/`.
- Keep `pyproject.toml` authoritative.
- Do not add Docker as the default install path unless local GNU Radio packaging
  makes it unavoidable.
- Do not commit generated vector indexes, FastEmbed/Hugging Face caches, GGUF
  files, or other model artifacts.
- Use `uv run` for commands.
- Use stdlib `unittest` for deterministic tests.
- Use targeted tests while iterating; reserve full `uv run python -m unittest`
  for release-candidate or broad runtime changes.
- Do not delete or weaken safety tests unless they are redundant, stale, or
  checking the wrong contract.

## Durable Docs

- `docs/BLUEPRINT.md`: architecture, wrappers, safety, context, evals, runtime
  status.
- `docs/QUICKSTART.md`: setup and usage.
- `docs/MODEL_CONTEXT_BIBLE.md`: generated from the actual injected prompt and
  model-facing tool schemas.
- `docs/HANDOFF.md`: current recent-work handoff, live E4B proof points, known
  soft issues, and next steps.

Update docs when wrapper contracts, safety boundaries, eval gates, runtime
requirements, or capability labels change.
