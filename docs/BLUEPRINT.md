# GRC Agent System Blueprint

Updated: 2026-05-07

This is the single source-of-truth design document for GRC Agent. It merges the former blueprint, system-design, and package-guide material into one operational contract: product scope, package shape, harness flow, model loop, tools, safety boundaries, retrieval, eval gates, and release criteria.

## 1. Product Scope

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs. It should read, inspect, explain, preview edits, apply verified edits, validate with GNU Radio tooling, and save only when the user explicitly asks.

Scoped **R0 read-only** and **R1 set_param** evidence is viable on tested fixtures. The overall project is **not release-candidate** and not production-ready. Production readiness depends on committed clean state, validated set_state, passing retrieval eval gates, and a full clean re-run of all deterministic gates.

Supported local scope:

- Load copied `.grc` files and summarize graph state.
- Inspect blocks, variables, connections, and local graph neighborhoods.
- Search installed GNU Radio block metadata.
- Answer GNU Radio conceptual questions from local docs with citations.
- Preview supported graph edits without mutation.
- Apply verified parameter, state, connection, rewire, insertion, and block operations through deterministic tools.
- Validate graph candidates with `grcc` before committing live mutations.
- Save explicitly, after validation, through atomic file replacement.
- Keep local checkpoints and graph deltas for CLI/debug infrastructure.

Non-goals:

- Unsupervised production autonomy.
- Broad graph planning from tutorials or memory.
- Direct raw YAML/text mutation of `.grc` files.
- Hidden repairs that rewrite user intent.
- Fixture-specific shortcuts.
- Vector/doc retrieval as mutation authority.
- Unbounded retries or unbounded candidate search.
- Model-facing access to low-level mutation tools in default chat.
- Model-facing undo/redo/restore.

## 2. System Invariants

These rules are stronger than prompt text or model behavior:

- One `GrcAgent` owns runtime state and tool execution.
- The model may request actions only through exposed tools.
- Runtime validates tool name, schema, route, operation type, and graph state before execution.
- All graph mutations go through verified Python tooling, never raw YAML edits.
- `change_graph` is the only default model-facing mutation wrapper.
- Internal mutation handlers must preserve preflight, candidate clone, `grcc`, rollback, and checkpoint semantics.
- Preview operations must not mutate the live graph.
- Failed edits must not commit candidate state.
- `grcc` remains final graph-validity authority.
- Save requires explicit user intent and a successfully validated latest dirty state.
- Checkpoint restore is CLI-only and writes to an explicit copy path.
- Clarification options must come from real executable candidates and include free text/custom fallback.
- Route mismatches fail closed; they are not silently remapped.
- Source retrieval is read-only evidence and never mutation authority.
- Assistant-text fallback parsing is frozen compatibility infrastructure, not a routing layer.

## 3. Package Architecture

| Layer | Main files | Responsibility |
| --- | --- | --- |
| CLI | `src/grc_agent/cli.py` | `doctor`, `health`, chat, fake runtime, direct tools, vector/manual/dogfood/history commands |
| Runtime | `src/grc_agent/agent.py`, `src/grc_agent/runtime/` | Tool registries, wrapper dispatch, route validation, prompt state, turn policy, clarification, history checkpoints |
| Adapter | `src/grc_agent/llama_server.py`, `src/grc_agent/llama_launcher.py` | llama.cpp HTTP client, server reuse/startup, OpenAI-compatible tool calls, bounded loop |
| Session | `src/grc_agent/flowgraph_session.py`, `src/grc_agent/session/` | Loaded graph state, parsing, compact snapshots, validation, atomic save |
| Transactions | `src/grc_agent/transaction/` | Preflight, candidate clone, apply/propose, commit or rollback |
| Validation | `src/grc_agent/validation/` | Deterministic graph and operation checks before `grcc` |
| Catalog | `src/grc_agent/catalog/` | Installed GNU Radio block metadata, params, ports, defaults, descriptions |
| Retrieval | `src/grc_agent/retrieval/` | Read-only Qdrant/FastEmbed vector retrieval and catalog indexing |
| Manual | `src/grc_agent/manual/` | Cleaned GNU Radio wiki/tutorial search with citations |
| History | `src/grc_agent/history.py` | Local checkpoint JSONL, graph deltas, retention, CLI restore-to-copy |
| Advisor | `src/grc_agent/runtime/turnplan_advisor.py` | Shadow-only local LLM intent advisor |
| Evals | `tests/llama_eval/`, `tests/retrieval_eval/`, `tests/dogfood/` | Deterministic, retrieval, live, release, and dogfood evidence |

## 4. Runtime Harness Flow

Default chat flow:

```text
User prompt + copied .grc path
  -> CLI loads config and session
  -> GrcAgent records compact active-session context
  -> Turn policy selects tool profile and allowed tools
  -> llama.cpp bounded chat/tool loop
  -> native tool-call parsing
  -> schema validation
  -> route validation
  -> wrapper tool execution
  -> deterministic internal dispatch
  -> preflight on candidate graph
  -> grcc validation when mutation is committed
  -> atomic commit or rollback
  -> compact tool output
  -> assistant response
  -> explicit save only when requested
```

Mutation flow:

```text
change_graph(dry_run=false, exact args)
  -> route/schema validation
  -> internal handler selection
  -> propose/preflight
  -> clone live session
  -> apply operations to clone
  -> candidate.validate() via grcc
  -> if valid: commit candidate to live session and journal checkpoint
  -> if invalid: return failure and keep live session unchanged
```

Preview flow:

```text
change_graph(dry_run=true, exact args)
  -> route/schema validation
  -> propose/preflight only
  -> return normalized operations, warnings, errors, and clarification if needed
  -> live graph revision stays unchanged
```

Save flow:

```text
explicit user save request
  -> latest dirty state must have validated successfully
  -> save writes same-directory temp file
  -> fsync temp file
  -> os.replace into target path
  -> dirty flag clears only after successful write
```

## 5. Model Tool Surface

Default model-facing chat surface is exactly four MVP wrappers:

1. `inspect_graph`
2. `search_blocks`
3. `ask_grc_docs`
4. `change_graph`

Low-level tools remain internal or compatibility-only. They must not leak into normal model-backed chat unless `legacy_model_tool_surface=true` is explicitly configured for debugging or research.

Current implementation caveat: the default CLI path narrows schemas to the four wrappers, but the codebase still builds legacy schemas for internal/compatibility use. The MVP prompt (`src/grc_agent/runtime/prompt.py`) correctly references only the four wrappers. Legacy tool instructions exist only in the legacy prompt branch (`legacy=true`), which is not the default model-facing path.

## 6. Wrapper Contracts

### `inspect_graph`

Purpose: read-only active graph inspection.

Allowed behavior:

- Summarize graph state.
- List blocks, variables, and connections within configured bounds.
- Validate graph through read-only validation operation where supported.
- Return local context around exact loaded blocks or variables.

Forbidden behavior:

- Mutating graph state.
- Saving files.
- Returning mutation recipes as instructions to the model.

### `search_blocks`

Purpose: block discovery over installed GNU Radio catalog metadata.

Default output:

- `block_id`
- `name`
- `summary`

Internal behavior:

- Exact ID/name/alias fast path.
- Lexical catalog search.
- Semantic catalog search when useful.
- Bounded cache for repeated conceptual queries.
- Lexical fallback when vector index is missing.
- Debug-only ranking/provenance telemetry.

No mutation-shaped payloads should be returned in normal output.

### `ask_grc_docs`

Purpose: explanation-only GNU Radio docs answers with sources.

Default behavior:

- Search local cleaned manual/tutorial corpus.
- Use semantic manual/tutorial retrieval only when useful.
- Return concise grounded answer, source snippets, `insufficient_evidence`, and fallback telemetry.
- Use catalog-assisted block definitions only when explicitly allowed by the docs-answer path.

Forbidden behavior:

- Authorizing graph mutation.
- Returning graph recipes from tutorials as edit plans.
- Treating docs as block/default authority when catalog metadata and `grcc` disagree.

Current quality caveat: docs-answer eval exits 0 but has relevance and groundedness gaps. Treat this as safe read-only support, not production-grade docs QA.

### `change_graph`

Purpose: only default model-facing graph mutation/preview surface.

Current required fields:

- `dry_run`: true for preview, false for committed mutation.
- `user_goal`: compact natural-language evidence for the requested change.

Optional exact fields include instance names, connection IDs, endpoints, new endpoints, selected block IDs, parameter keys/values, state, variable names/values, and verified `target_ref` objects.

Required behavior:

- Reject unsupported workflows such as raw YAML, undo, redo, save, or Python export.
- Ask for clarification when exact executable details are missing.
- Dispatch internally to verified handlers only.
- Preserve preflight, `grcc`, rollback, checkpoint, and save-state semantics.

Implemented: `operation_kind` enum added to `change_graph` schema. `user_goal` is supporting evidence; routing is based on `operation_kind`.

## 7. Internal Tool And Handler Inventory

Internal/compatibility tools currently include:

- `new_grc`
- `load_grc`
- `summarize_graph`
- `search_grc`
- `get_grc_context`
- `describe_block`
- `search_manual`
- `semantic_search_grc`
- `suggest_compatible_insertions`
- `insert_block_on_connection`
- `auto_insert_block`
- `remove_connection`
- `rewire_connection`
- `apply_edit`
- `propose_edit`
- `validate_graph`
- `save_graph`

These are acceptable internal boundaries. They should not be treated as default model-facing tools.

## 8. Supported Graph Work

### Creation And Loading

- `new_grc` creates a minimal empty graph skeleton.
- `load_grc` loads an existing `.grc` file into the active session.
- CLI must reject direct mutation of canonical fixtures or original user files when a copied-graph safety rule applies.

### Inspection

- Graph summaries include bounded block previews, connection information, variable counts, dirty state, validation state, and provenance.
- Context lookup requires exact loaded instance names or verified resolved references.
- Duplicate ambiguous targets must clarify rather than pick the first match.

### Parameter And State Edits

- `update_params` supports unique loaded blocks and variables.
- Values may be literals or GNU/Python expressions.
- `update_states` supports enabled/disabled state changes.
- Same-name duplicate handling requires verified discriminators or `target_ref`.

### Block Identity And `target_ref`

Loaded blocks carry deterministic `block_uid` for inspection and eval identity. Free-form text cannot mutate by UID.

The only supported UID mutation path is a structured `target_ref` containing:

- `block_uid`
- `expected_instance_name`
- `expected_block_type`
- `base_state_revision`

The runtime must reject stale `target_ref` values after graph changes.

### Connections And Rewires

- `add_connection` and `remove_connection` support stream and message ports.
- Exact disconnects should resolve to one `connection_id`.
- `rewire_connection` is one ordered transaction: remove old resolved connection, add new resolved connection.
- Partial endpoint hints may clarify only with executable candidate options.
- Invalid message-port numeric hints must fail before becoming executable clarification choices.

### Insertions

- `suggest_compatible_insertions` is read-only.
- `insert_block_on_connection` requires exact selected candidate args.
- `auto_insert_block` may perform bounded candidate search and commit only the first `grcc`-valid candidate, clarify, or reject.
- No tutorial-derived hidden block recipes are allowed.

### Removal

- `remove_block` requires the target to be detached or the transaction to remove attached wires first.
- Dependency repairs must be explicit operations in the same verified transaction when required for validity.
- Failed removal must leave the live graph unchanged.

## 9. Agent Loop And Recovery

The loop is bounded and must remain simpler than a broad planner.

Allowed loop behavior:

- Use native OpenAI-compatible tool calls from llama.cpp.
- Disable parallel tool calls unless intentionally tested.
- Execute only schema-valid and route-valid calls.
- Stop on failed mutation unless a typed recovery policy allows exactly one retry.
- Use continuation nudges only for explicit requested actions that remain incomplete.
- Return compact assistant text after terminal success or failure.

Recovery rules:

- One corrected retry is allowed only for selected recoverable missing-argument failures.
- The retry must stay within the current route policy and allowed tool set.
- Runtime recovery must not switch preview into apply.
- Runtime recovery must not synthesize graph recipes from docs.
- Failed `grcc` validation is not automatically repaired unless a narrow tested recovery exists and the user requested recovery.

Current concern:

- Fixed: MVP mode defaults to `max_tool_rounds=8`. Higher limits reserved for explicit compatibility or research modes.

## 10. Fallback Parser Policy

Assistant-text fallback parsing exists for weak-model compatibility. It can parse pseudo tool calls or mutation-shaped JSON when native tool calls are absent.

Contract:

- It must not bypass route validation.
- It must not execute legacy mutation tools in MVP mode.
- It must not become a hidden planner or repair layer.
- It must not expand to more shapes without separate design review and live eval evidence.

Recommended direction:

- Disable fallback parsing by default in MVP mode unless a model-specific compatibility flag requires it.
- Move fallback policy behind `GrcAgent` or a `ToolSurface` profile so transport remains transport-only.

## 11. Advisor And Turn Policy

Long-term direction: advisor-first intent classification.

Current advisor shadow vocabulary:

```json
{"mode":"inspect|preview|change|clarify|unsupported"}
```

Advisor status:

- Shadow-only by default.
- Does not control default routing.
- Uses the same local llama.cpp server.
- Must output labels only, never transactions.

Current deterministic TurnPlan status:

- It provides finite policy and allowed-tool narrowing today.
- It still contains phrase/regex-based routing assumptions.
- That is acceptable only as current production-candidate scaffolding, not as the desired long-term semantic architecture.

Promotion criteria for advisor control:

- Deterministic tests show no safety regressions.
- Live evals prove better or equal routing accuracy than current TurnPlan.
- Advisor uncertainty routes to clarify/unsupported, not guessed mutations.
- Runtime still owns schema validation, route gates, operation validation, preflight, `grcc`, rollback, save state, and retry budgets.

## 12. Context And Health

Config defaults:

- Desired context target: `120000` tokens when backend/model support it.
- `max_tokens`: generation ceiling only, not compression strategy.
- History compact budget: configured under `[agent]`.
- Tool output limits: configured under `[agent.guardrails]`.

Context strategy:

- Prefer compact wrapper outputs over large raw graph dumps.
- Keep active session summaries bounded.
- Keep docs snippets bounded and cited.
- Track truncation in telemetry.
- Verify desired vs actual context before claiming large-context behavior.

Health contract:

- `doctor` checks local environment: Python, `grcc`, GNU Radio import/version, app config, retrieval readiness.
- `health` should report end-to-end readiness, not just package object construction.

Required health fields:

- `core_ready`
- `retrieval_ready`
- `model_ready`
- `context_verified`
- `llama_desired_context_tokens`
- `llama_actual_context_tokens`
- `tool_surface`
- `model_tool_count`
- `internal_tool_count`
- `status`: `ok`, `degraded`, or `not_ready`

Fixed:

- `doctor` and `health` now treat unknown actual context as a failure (`context_verified=false`).
- `grc-agent health` already fails when llama.cpp is unreachable.

Remaining concern:

- Health should distinguish `ok`/`degraded`/`not_ready` rather than binary pass/fail when context is below desired but still functional.

## 13. Retrieval And RAG

Current vector architecture:

- Qdrant local mode.
- FastEmbed default model: `BAAI/bge-small-en-v1.5`.
- Read-only vector index over catalog, manual, and tutorial records.
- Vector-only baseline; no default hybrid sparse search or reranker.
- Source types: catalog block, manual chunk, tutorial chunk.
- Result payloads strip mutation-shaped keys.

Current lexical docs architecture:

- Cleaned GNU Radio wiki/tutorial markdown in `docs/wiki_gnuradio_org/`.
- Bounded lexical chunk search with source URL, line range, oldid, edit date, and license metadata.
- Docs answers must return `insufficient_evidence` when support is weak.

Operating contract:

- Catalog metadata is authority for block IDs, ports, params, defaults, and signatures.
- `grcc` is authority for graph validity.
- Manuals/tutorials explain concepts only.
- Retrieval must never return mutation plans, transactions, allowlists, blacklists, or hidden default recipes.

Current eval evidence:

- Vector regression: 290 cases, 276 vector top-k hits, 290 provenance passes, 290 safety passes.
- Docs-answer eval: 35 rows, 0 mutation leakage, 0 misleading answer count, but only 24 relevance passes and 19 groundedness passes.

RAG recommendation:

- Keep Qdrant + FastEmbed now.
- Do not add rerankers, hybrid sparse search, or llama.cpp embeddings until measured misses justify them.
- Add better source coverage for `grcc`, validation, GRC compile/generation behavior, and comparison questions.
- Add explicit quality thresholds before production docs QA claims.

## 14. llama.cpp Runtime

The adapter uses llama.cpp's OpenAI-compatible server API:

- `/v1/chat/completions`
- tools/function definitions
- `tool_choice="auto"`
- `parallel_tool_calls=false`
- `parse_tool_calls=true`
- temperature `0`

This is a reasonable local runtime path.

Requirements:

- Launch/reuse must verify server readiness.
- Model alias must match configured alias.
- `/props` or equivalent must verify actual context.
- Health must distinguish unreachable model from OK runtime.
- Tool schemas and prompt must match the active tool profile.

Do not claim 120k context behavior unless actual server properties prove it.

## 15. Testing And Eval Gates

Default deterministic development gates:

```bash
uv run ruff check src/ tests/
uv run ruff check
uv run python -m unittest
uv run python -m tests.retrieval_eval.vector_regression
uv run python -m tests.retrieval_eval.grc_docs_answer_eval
uv run grc-agent doctor
uv run grc-agent health
```

Gate roles:

- Lint gates catch style and import errors.
- Unit tests validate deterministic runtime, transaction, safety, wrapper, config, and harness behavior.
- Vector regression validates retrieval safety/provenance and governed quality.
- Docs-answer eval tracks grounded answer quality but currently tolerates known gaps.
- Doctor validates local dependencies and GNU Radio environment.
- Health must validate end-to-end runtime readiness after its semantics are fixed.

Live eval tiers:

- Tier 1: quick routing/behavior smoke.
- Tier 2: release-level prompt/schema/tool-order evidence.
- Tier 3: multiturn behavior.
- Tier 4: external installed examples.
- Tier 5: adversarial intent/safety.

Dogfood evidence:

- Use copied graphs only.
- Classify failures by routing, argument extraction, preflight false reject, unsafe mutation risk, `grcc` failure, save/reload mismatch, clarification quality, retrieval miss, tool error, or other.
- Dogfood reports support release decisions but do not replace deterministic tests.

Current test-harness caveat:

- Full `unittest` took about 28 minutes in the audit run. Split fast inner-loop gates from full release gates.
- Retrieval evals share local index state and should run sequentially unless isolated temp indexes are used.
- Legacy-to-MVP eval canonicalization has been removed. Blocked legacy tool attempts remain visible in scoring and correctly fail `model_contract_pass` while passing `runtime_safety_pass` only when no mutation occurred.

## 16. Release Criteria

Before claiming production-ready:

- `health` must not return overall OK when configured llama runtime is unreachable or actual context is unknown. **Fixed.**
- MVP prompt must mention only MVP wrappers, not legacy low-level tools. **Verified.**
- `ToolSurface` or equivalent must align schemas, prompt, fallback policy, health counts, and eval profile. **Verified for runtime; native MVP eval case catalogs created for R0/R1.**
- Default MVP tool-round ceiling must be reduced and tested. **Verified (8 rounds).**
- Assistant-text fallback parser must be disabled/frozen for MVP mode. **Verified (disabled in MVP).**
- Live evals must run against the default MVP wrapper profile. **Verified.**
- Release dashboard must inspect raw tool-call history, not just metadata. **Fixed.**
- Release manifest must include commit, dirty state, model alias, actual context, prompt hash, schema hash, policy hash, eval versions, and fixture identifiers. **Fixed.**
- Committed mutation evals must include save/reload/`grcc` semantic checks. **Not validated; save/load out-of-scope.**
- Docs-answer quality thresholds must be explicit. **Not validated; Qdrant unavailable.**
- No STOP_THE_LINE safety findings may be open. **Three fixed: eval canonicalization, dashboard metadata-only validation, doctor unknown-context pass.**

Current classification (2026-05-08):

- **R0_READ_ONLY** (inspect_graph, search_blocks, ask_grc_docs): **Viable.** 14/14 cases stable at 3/3. model_contract_pass=1.00, runtime_safety_pass=1.00, semantic_pass=1.00.
- **R1_SET_PARAM_ONLY** (change_graph set_param): **Viable on tested fixtures.** 2/2 cases stable at 3/3. model_contract_pass=1.00, runtime_safety_pass=1.00, semantic_pass=1.00.
- **R1_SET_STATE** (change_graph set_state): **Unvalidated.** Runtime correctly rejects state changes that break graph validity (e.g., disabling throttle in default fixture). A valid set_state fixture/target must be added separately.
- **BETA_COMPLEX_MUTATION** (add_variable, multi-step chains, external edits, vague queries): **Informational only.** Not release-gating.
- **Out-of-scope** (rewire, disconnect, insert, remove, save, load, clarification-heavy flows): Not assessed.
- **Overall**: **Not release-candidate** because:
  - set_state is unvalidated.
  - Retrieval eval gates blocked (Qdrant unavailable).
- **Not production-ready**.

## 17. Completed / Hardened Items

- Health semantics fixed: fails closed when actual context is unknown.
- Doctor fixed: context check requires actual >= desired, not unknown-pass.
- Release dashboard validates raw tool-call history (`raw_legacy_tool_entries`).
- Native MVP R0/R1 eval case catalogs created; legacy translation removed from release-gating paths.
- `release_profile` persisted in run-store metadata and dashboard scope filtering implemented.
- MVP tool-round ceiling = 8, fallback parser disabled in MVP mode.

## 18. Remaining Work (Not Release-Gating for R0/R1)

- **set_state validation:** Add a fixture where disabling a block does not break graph validity, then validate.
- **Retrieval eval gates:** Requires Qdrant available. Blocked in current env.
- **Docs-answer eval gate:** Requires Qdrant + vector index. Blocked in current env.
- **Clean commit:** No unstaged changes remain; intended changes are staged. Repository remains dirty until committed.
- **Save/load semantic checks:** Out-of-scope for current R0/R1 scopes.
- **Complex mutation evidence:** Beta only; not release-gating.
- Run MVP wrapper dogfood.
- Run Tier 1 and Tier 2 live evals against default MVP profile.
- Generate release dashboard and manifest.

## 19. STOP_THE_LINE Conditions

Stop the release and report clearly if any of these are found:

- Legacy mutation tools exposed in default MVP model chat.
- Preview mutates live graph state.
- Raw YAML/text mutation path reaches live graph.
- Failed preflight commits live graph changes.
- Failed `grcc` validation commits live graph changes.
- Rollback bypass or checkpoint corruption.
- Docs/vector retrieval directly drives mutation parameters or transactions.
- Fallback parser bypasses route validation.
- Save occurs without explicit user request.
- Health claims OK when configured model runtime is required but unreachable.

Do not patch STOP_THE_LINE issues silently inside release reports. Fix with explicit tests and review.

## 20. External References

- llama.cpp function calling: https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md
- llama.cpp server: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- GNU Radio Companion tutorial: https://wiki.gnuradio.org/index.php/GNURadioCompanion
- GNU Radio YAML GRC: https://wiki.gnuradio.org/index.php?title=YAML_GRC
- Qdrant FastEmbed semantic search: https://qdrant.tech/documentation/fastembed/fastembed-semantic-search/
- Qdrant FastEmbed optimization: https://qdrant.tech/documentation/fastembed/fastembed-optimize/
- Qdrant FastEmbed rerankers: https://qdrant.tech/documentation/fastembed/fastembed-rerankers/
- FastEmbed docs: https://qdrant.github.io/fastembed/
- MCP tools: https://modelcontextprotocol.io/docs/concepts/tools
- MCP resources: https://modelcontextprotocol.io/docs/concepts/resources
