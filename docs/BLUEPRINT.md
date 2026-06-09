# GRC Agent Blueprint

This is the current high-level design contract. It is intentionally concise and limited to durable runtime facts.

## Status

GRC Agent is a local assistant for GNU Radio Companion `.grc` graphs. It is built around explicit tool calls, typed validation, `grcc`, rollback, and copied-graph safety.

Current classification:

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`, `R4A_INSERT_BLOCK_ON_CONNECTION`, `R4B_REMOVE_BLOCK`, `R4C_ADD_VARIABLE`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`
- Runtime: not production-ready

The default model-facing surface is the three-wrapper MVP profile only. Low-level graph tools remain internal.
ToolAgents is the model/provider/tool-call harness; GRC Agent still owns wrapper validation, routing, graph transactions, `grcc`, rollback, autosave, manual `/save`, history, and raw trace evidence.
Chat transport lives in ToolAgents; this repo keeps only non-chat health/probe HTTP code.

## Safety Contract

- Never edit raw `.grc` YAML directly.
- Never mutate originals under installed example paths; copy graphs first.
- Preview must never mutate.
- Failed schema validation or preflight must not commit. Final native/`grcc`
  validation failure must not commit unless the user intent supports an
  invalid intermediate graph and the model explicitly uses `force=true`.
- The model cannot save or load directly.
- Successful committed mutations validate, then autosave to the active copied graph path when that path is safe and writable.
- Committed mutations refuse when the active copied graph file changed on disk since the session last loaded or saved it.
- Manual `/save` requires explicit user intent.
- Docs/RAG are explanation-only and never mutation authority.
- Ambiguous graph targets clarify instead of first-match mutation.
- `grcc` remains final graph-validity authority, but it proves compilability rather than semantic/user-intent correctness.

## Model-Facing Wrappers

### `inspect_graph`

Purpose: read-only graph inspection.

Args:

- `view`: optional; one of `overview`, `details`
- `targets`: optional string array; ignored for `overview`, one to five graph-local targets for `details`
- `params`: optional string array; ignored for `overview`; for `details`, pass `["all"]`, exact parameter names, or omit it for bounded useful parameters
- `debug`: internal eval/dev telemetry only, stripped from the model-facing schema

Runtime defaults:

- omitted `view` plus no targets becomes `overview`
- omitted `view` plus targets becomes `details`
- `overview` normalizes away stray `targets` and `params`
- `details` with missing or empty `params` returns target identity, connections, and parameter availability without dumping large parameter lists
- exact `block.parameter` refs are split into the owning block target plus parameter filter

Expected output:

- stable top-level fields: `ok`, `view`, `state_revision`, `complete`, `summary` for overview, `targets` for details, plus `target_matches`, `params_filter`, `ambiguity`, `truncation`, `validation_status`, and `errors` only when they carry non-default evidence
- `overview`: minimal topology index with counts, block name/type/label/role rows, connection IDs, state revision, truncation, and validation status
- `details`: resolved graph-local targets, compact connection context, one block-level guarded `target_ref`, selected/current params only when requested or safely bounded, and explicit `params_omitted`/availability or truncation metadata
- ambiguous or missing details targets return candidates/errors and no guessed mutation
- details calls with missing or empty `params` must not become `["all"]`; `params=["all"]` is bounded and may truncate

Internal subtools/data:

- `summarize_graph`
- editable parameter candidate index
- session graph metadata and connection summaries

Design lessons from the redesign:

- The model-facing schema is an intent contract, not an internal query API.
- Required fields are still validated at runtime because local models can omit them; safe read-only defaults are filled before validation where omission is harmless.
- Overview is forgiving because it is read-only; details is strict because it can expose mutation-ready target refs.
- Runtime owns budgets, truncation, target matching, and target-ref generation.
- Overview output must stay small and omit parameter previews, dependency lists, editable handles, and duplicated grouping. Details output carries parameter keys, current values, target refs, state revisions, ambiguity, and truncation only when those facts are needed so the model is not forced to infer precise graph facts.
- Overview block roles come from catalog/GNU metadata and connection evidence. Do not hardcode block-name role lists.
- Validation is not a model-facing inspect mode; validation runs at load, after mutation, before autosave, and before manual save, then appears as status metadata.

### `search_blocks`

Purpose: find installed GNU Radio catalog blocks.

Args:

- `query`: required
- `k`: optional result bound
- `debug`: optional telemetry

Expected output:

- compact ranked block candidates with `block_id`, title/name, match type, and a short factual excerpt
- exact block-ID queries may include compact catalog details: params, defaults, options, option labels, inputs, and outputs, so generic transactions can be planned from installed metadata
- no raw vector scores in model-visible output
- no mutation payloads or edit authority

Internal subtools/data:

- exact/catalog lexical lookup over installed GNU metadata
- cached in-memory SQLite FTS5 sparse ranking over compact catalog metadata/prose
- vector block retrieval when the local generated index is available
- deterministic merge/rerank
- catalog metadata
- optional `describe_block` enrichment

### `ask_grc_docs`

Purpose: explanation-only grounded docs answers.

Args:

- `question`: required
- `k`: optional source count
- `debug`: optional telemetry

Expected output:

- concise answer
- source list
- `allowed_use=explanation_only`
- `mutation_authority=false`
- confidence derived from source quality and insufficient-evidence state
- `insufficient_evidence` when local docs do not support an answer
- instruction-like retrieved text is stripped before it reaches model-visible answers/sources
- no mutation payloads
- no docs-derived graph edit recipes or hidden plans

Internal subtools/data:

- local GNU Radio wiki/tutorial corpus under `docs/wiki_gnuradio_org/`
- deterministic grounded-answer builder
- optional helper synthesis only when explicitly enabled for research

### `change_graph`

Purpose: the only model-facing graph-content mutation wrapper.

The model-facing schema is a flat batch interface. The model does not provide
`op`, `args`, `dry_run`, `user_goal`, `state_revision`, or `preview_token`.
Runtime injects turn intent, stale-state guards, active-file integrity checks,
transaction ordering, rollback, validation, and autosave internally.

Supported flat fields:

- `add_blocks`: add GNU blocks by exact `block_id` and `instance_name`, with optional initial `params` and `states`
- `remove_blocks`: remove existing blocks by `instance_name` or guarded `target_ref`; runtime auto-detaches incident connections and reports them
- `update_params`: update multiple params on one existing block per item, with optional `expected_params`
- `update_states`: update supported state fields on one existing block per item
- `add_connections`: add exact source/destination endpoint pairs
- `remove_connections`: remove exact `connection_id` strings from `inspect_graph`
- `add_variables`, `update_variables`, `remove_variables`: first-class variable edits, normalized internally to variable-block transactions
- `force`: optional validation override, default `false`

At least one edit list must be non-empty. Runtime normalizes the flat fields into
internal transaction operations in fixed order: remove connections, remove
blocks, add blocks/variables, update params/variables, update states, insert
blocks on connections, then add connections. The model is not responsible for
low-level transaction ordering.

`force=true` may only bypass final native/`grcc` validation failure after schema,
graph refs, catalog/GNU block IDs, params, ports, connection IDs, copied-file
integrity, and candidate apply have all succeeded. It never bypasses unknown GNU
facts, ambiguity, stale refs, path safety, or save/autosave errors. Forced
success returns `committed=true`, `validation_ok=false`, a validation warning,
and an autosave result for the invalid intermediate working copy.

There are no model-facing block-specific macros. Workflows such as rewire,
insert-in-path, add-source-and-connect, or source-to-sum are expressed by the
flat generic buckets and validated through graph/catalog evidence. Internal
helpers may still lower generic batches into existing transaction primitives.

Expected output:

- `ok`, `committed`, current state revision
- compact `effect`/`effects` describing the exact graph fact changed
- compact graph delta when applicable
- validation result and autosave result for committed mutations
- stale file-integrity refusal when the active file hash no longer matches the last loaded/saved hash
- clarification payload for ambiguous or underspecified requests
- rollback/refusal details for failed validation
- no model-visible `active_session` dump; state revision, validation, and autosave fields carry the needed state

Internal subtools/data:

- `apply_edit` for committed mutation
- transaction normalizer
- validation rules and preflight checks
- `grcc`
- graph history/checkpoints

Current mutation-wrapper status:

- Keep one model-facing mutation wrapper for now.
- Keep model-facing edits generic through flat edit buckets; do not restore block-specific macros.
- Return compact model-visible results with exact effect/effects, committed status, state revision, validation, autosave, and concise refusal evidence.
- Require graph-local authority for edits: exact identifiers, guarded target refs, or uniquely resolved metadata candidates from the active graph.
- Preserve existing safety: no docs/RAG authority, no raw YAML, no hidden retries, no first-match mutation, and rollback on failed validation.
- Remaining follow-up work should focus on live eval quality for mutation phrasing and operation-specific edge cases, not expanding the model-facing tool surface.
- Deprecated model-facing compatibility fields such as `op`, `args`, `dry_run`, `user_goal`, `preview_token`, `operation_kind`, `candidate_id`, `insert_block`, and `auto_insert` must not be restored without eval evidence.

## Internal Tool Boundary

Internal tools include graph creation/loading, summaries, catalog retrieval, block descriptions, vector retrieval, insert suggestions, connection removal/rewire, edit proposal/application, validation, and raw save. They are implementation primitives, not default model-facing chat tools.

The model sees only the three wrappers above in MVP chat mode.

## Data Authority

Active graph inspection is authority for instance names, current values, connections, target refs, and state revisions.
Installed GNU Radio catalog metadata is authority for block IDs, ports, parameters, defaults, option labels, categories, and block-level semantics.
GNU platform metadata is preferred when available for semantic flags such as `not_dsp`.
Block search uses exact/catalog lexical lookup and cached in-memory SQLite FTS5 sparse ranking for identifiers and metadata, with vector retrieval as semantic discovery when the local index exists. Exact block IDs, parameter IDs, port names, and dtypes must not depend on dense embeddings alone.

Docs and ToolAgents tutorials are not runtime graph-edit recipes. When model behavior is confused, fix the authoritative data path, wrapper contract, validation, or context budget instead of adding prompt folklore or fixture-specific special cases.

## Runtime Harness

Model-backed chat uses ToolAgents with llama.cpp through its OpenAI-compatible `/v1` API. The runtime uses bounded `ChatToolAgent.step(...)` calls rather than an unbounded response loop so the repo still controls the max tool-round ceiling.

Each model step rebuilds a `ToolRegistry` from the currently allowed wrapper schemas. Delegates record the raw requested tool call, validate route and schema through `GrcAgent`, and execute `GrcAgent.execute_tool(..., model_tool_call=True)` only after validation passes. Invalid or disallowed calls return structured tool results for model repair or user-facing refusal without mutation.

The CLI default uses the configured bounded round limit. `uv run grc-agent chat
--agentic` raises the bounded limit and request timeout for exploratory local
turns without exposing extra tools or weakening validation. `--max-tool-rounds
N` is an explicit per-session override.

If the assistant returns final text that says it needs inspection/search, or
answers a graph-local fact question without any tool evidence, the runtime adds
one reminder and continues the same bounded turn. This is a missed-tool nudge,
not a free-text fallback parser, hidden repair path, or permission bypass.
For mutation requests, reminders may require `change_graph` only when enough
tool evidence exists and the model is not asking a clarification. If the model
asks a graph-evidence-backed clarification, the turn may end with no mutation.
This prevents reminder pressure from turning ambiguity into first-match edits.

Vague graph-edit requests are allowed into the model/tool loop so the model can
inspect and clarify. Mutation safety remains enforced by wrapper schemas, route
validation, graph-local resolution, `change_graph`, preflight, `grcc`, rollback,
and autosave checks.

There is no assistant-text fallback parser, JSON repair path, or AST/text transaction recovery path.

## Agent Loop

1. Load or create one active `FlowgraphSession`.
2. Build compact model messages from system policy, recent user/assistant/tool history, and bounded tool results.
3. Ask llama.cpp through ToolAgents for one bounded `step(...)` with the three wrapper schemas.
4. Validate every requested tool call against wrapper schemas and current route constraints.
5. Execute accepted tool calls serially through GRC delegates.
6. For mutations, route through `change_graph`, transaction validation, preflight, `grcc`, and rollback/commit.
7. Append raw requested calls, executed calls, tool results, deltas, and validation state to history/trace.
8. Stop after bounded tool rounds or when the assistant returns grounded final text.

Fallback free-text parsing is disabled for the MVP runtime. If the model cannot produce a valid call, the turn fails closed or asks for clarification.

## Context Handling

Context is compacted by tool output design, not by hiding raw tool calls.

- `inspect_graph` has only `overview` and `details`, with explicit truncation and ambiguity metadata.
- `details` exposes guarded target refs only for resolved graph-local targets and omits large param lists unless specifically requested.
- `search_blocks` and `ask_grc_docs` return compact evidence objects; retrieval scores and verbose source text stay out of model-visible output.
- Tool results are compacted before future model turns, while raw call/result history remains traceable.
- The system prompt and three wrapper schemas are intentionally budgeted; long examples belong in tests/docs, not every model turn.
- Health checks verify desired vs actual llama context; current target is 120000 tokens when supported.
- `max_tokens` limits generation length only; it is not used as a compression strategy.

Small models are sensitive to context bloat. A simple read-only inspect turn should not carry a large policy block, oversized schemas, full history, and verbose overview payload. Keep schemas and wrapper output compact; reserve broad context for tasks that need it.

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
9. autosave to the active copied graph path when safe and writable

Autosave and manual `/save` share the same session save path. Save takes an advisory lock under `.grc_agent`, rejects symlink or hard-linked targets, rechecks the active file hash under the lock before replace, writes a content-addressed backup under `.grc_agent/backups/`, writes through a temp file plus `os.replace`, fsyncs the directory, then re-reads and verifies the persisted hash before clearing dirty state.

The model has no lifecycle tools. `/save` in the CLI bypasses the model and calls the internal save path directly after validation.

## CLI Chat UX

- `uv run grc-agent chat <copy.grc>` starts interactive chat on a copied graph.
- For programmatic single-shot execution, use `--stdin` to pass prompts and `--json` to force stdout to emit a single, parseable JSON payload.
- Bare `uv run grc-agent` enters chat only in an interactive TTY; non-interactive use prints help and exits command-safe.
- Chat startup checks the configured llama.cpp server and starts/reuses it when needed; readiness is still health-verified before model-backed use.
- `uv run grc-agent health` is passive and reports `not_ready` if llama.cpp is not already reachable. Use `uv run grc-agent doctor --start-llama` or chat startup when you want the launcher to start/reuse llama.cpp.
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

All live evals enforce 11 REPORT_DIMENSIONS: routing_pass, argument_pass, tool_success_pass, semantic_pass, safety_pass, runtime_safety_pass, model_contract_pass, end_state_pass, recovery_pass, budget_pass, lint_pass.

- `budget_pass` — upper-bound thresholds for tool_rounds, tool_calls, assistant_text length. Catches catastrophic thrashing without demanding perfect DSP math from a 4B model.
- `lint_pass` — graph-hygiene checks after mutation: orphan blocks, unused variables, disabled blocks with connections, duplicate block names. Known fixture-level dirtiness is whitelistable via `lint_expected_issues`.

Live quick gate (model-facing behavior changes):

- `uv run python -m tests.llama_eval.run_r0_release --n-runs 1 --results-path /tmp/r0.json`
- `uv run python -m tests.llama_eval.run_r1_release --n-runs 1 --results-path /tmp/r1.json`
- `uv run python -m tests.llama_eval.run_r2_release --n-runs 1 --results-path /tmp/r2.json`

Parameterized DSP Fuzzing Gauntlet:

- `uv run python -m tests.llama_eval.run_dsp_gauntlet --seed 42 --count 30 --quick`
- Generates scenarios from 8 generators (notch, ble, ofdm, qam, mac, inline_swap, cascade, typo) with isolated `random.Random(seed)`.
- Each scenario produces a `LiveScenario` with `fuzzed_variables` mapped to prompt and semantic checks (prompt-to-evaluation symmetry).
- Fixture variables are patched via `ruamel.yaml` round-trip-safe YAML mutation (`fuzz_fixture`).
- Reports pass/fail per scenario plus aggregate budget and lint metrics.
- `--seed` for deterministic reproducibility; `--output-dir` for post-mortem graph preservation.

The full `unittest` discovery is slow because it includes integration-style graph loading, `grcc` validation, eval harness logic, and CLI loops. Use targeted tests during iteration and reserve full runs for release candidates.

## Docs And Retrieval

`docs/wiki_gnuradio_org/` is a local explanation corpus. It can support user education and docs QA, but it cannot authorize mutations, infer active graph semantics, or provide hidden graph recipes.

Catalog metadata and active graph inspection own block semantics, ports, parameters, current values, and edit targets. Retrieval is explanation-scoped and must not become mutation authority.

Docs-answer quality is evaluated separately from mutation safety. Groundedness and relevance matter; misleading answers and mutation leakage must remain zero.

The default retrieval stack is local and lightweight:

- Python dependencies come from `pyproject.toml`.
- The active vector index is generated locally with `uv run grc-agent vector build`.
- Embedding models are downloaded/cached by the user environment at runtime; they are not bundled, vendored, or committed.
- The default embedding model is `thenlper/gte-base` through FastEmbed. The cached quantized ONNX package is about 400 MB.
- `search_blocks` combines exact/catalog lexical metadata lookup, cached in-memory SQLite FTS5 sparse ranking, and vector retrieval when the generated index is available. Lexical lookup covers exact block IDs, parameter IDs, port names, dtypes, labels, and categories; FTS5 covers sparse prose matches; vector retrieval covers semantic discovery.
- Stale index schemas fail closed and tell the user to rebuild.

## Runtime Readiness

End-to-end runtime readiness requires:

- package installed
- ToolAgents runtime dependency installed
- GNU Radio Python import works
- `grcc` works
- retrieval catalog ready
- vector index ready when retrieval is required
- embedding model available in the user's local cache or downloadable during explicit vector build
- llama.cpp reachable
- actual llama context verified
- llama.cpp server-side built-in tools not detected in `/props`
- four MVP model-facing wrappers only

CUDA-enabled llama.cpp on `CUDA0` is the default NVIDIA runtime path. The local launcher passes `--device CUDA0 --gpu-layers 999 --flash-attn auto` explicitly; if `llama-server --list-devices` does not show `CUDA0`, model-backed chat is not runtime-ready.
When `[llama].model_path` is configured, the launcher passes `-m` to
`llama-server` pointing directly at the local `.gguf`. The Qwen 3.5 GGUF
file is loaded this way; jinja templating is enabled by default in current
llama.cpp builds so the embedded ChatML template correctly renders tool-call messages.

## Documentation Set

Durable docs kept under `docs/`:

- `BLUEPRINT.md`: architecture and safety contract
- `MODEL_CONTEXT_BIBLE.md`: generated exact model-facing prompt and wrapper schemas
- `CHANGELOG.md` (under `docs/`) — release history and the deferred harder-wins roadmap
- `wiki_gnuradio_org/`: local GNU Radio tutorial/reference corpus

---

## Hardening History & Handoff Summary

The GRC Agent has undergone rigorous behavior hardening to ensure absolute safety and deterministic validation.

### Validation Hardening & Behavioral Rules
1. **Native Validation Refusal**: Any invalid graph edits (e.g. disabling the only sink in a flowgraph) are rejected by native GRC validation. Rollbacks are performed atomically, leaving the copied graph byte-identical, and quoting GRC errors in the assistant response.
2. **Forced Invalid Intermediate State**: If the user explicitly accepts an invalid intermediate state, `force=true` permits committing the GRC-invalid graph, but still enforces schema checks, preflight checks, reference checks, and file-write safety.
3. **Disable vs Remove Disambiguation**: The wrapper schema explicitly distinguishes between disabling a block (which uses `update_states` to set state to `disabled`) and removing a block (which uses `remove_blocks`).
4. **Variable Removal Repair**: The `remove_variables` argument takes a list of strings (instance-names), with robust validation warning the model against passing dictionary objects.
5. **Tool Schema Disambiguation**: `inspect_graph` and `search_blocks` descriptions contain strict boundaries to prevent the model from calling inspect tools to search block catalogs.

### Wireless Engineering Scenario Matrix
All 11 complex wireless-engineering graph mutation scenarios have been run and succeeded:
*   **LPF insertion on float stream** (dtype mismatch recovery)
*   **Volume variable + multiply const** (multi-step parameter linking)
*   **Swap signal source connections** (port-occupancy, atomic rewire ordering)
*   **Rational Resampler + sink rate update** (concurrent block + param mutations)
*   **Non-specific HPF insertion** (vague prompt to exact param mapping)
*   **GUI Range control + LPF linkage** (variable-linked parameter expressions)
*   **Heterodyne mixer / downconverter** (multi-block add + complex rewiring)
*   **Bandpass filter + spectrum visualizer** (dual-output routing, Qt GUI block)
*   **AM SC transmitter** (parallel signal branches)
*   **AM coherent demodulator/receiver** (shared carrier oscillator multi-fan-out)
*   **AWGN channel + AGC normalization** (5-block pipeline insert, disconnect + rewire)

### PySide6 GUI Release (Release 2.0.0)
The native PySide6 Desktop GUI operates as a lightweight sidekick panel running alongside the GRC editor. It features non-blocking LLM reasoning inside a `QThread`, state-preserving widget updates, split-stage compilation and run processes, and a deferred `closeEvent` sequence to prevent SDR hardware locks upon application exit.

#### Second-pass hardening (M7)
A 19-item audit of the sidekick GUI was completed after the M6
remediation. The full list of items, contracts, and test mappings is
documented in `AGENTS.md` (Engineering Rules / Agent Behavior sections).
Highlights: per-slot kill timers (`_compile_kill_timer`,
`_run_kill_timer`) instead of id-keyed dicts; `shutdown()` waits
capped at 200ms × 2; layered HTML sanitization (pair-wise tag strip +
self-closing tag strip + `on*` event attr strip + dangerous URI
scheme strip); throttled stream via persistent `QTimer` so cancelled
turns never reach `turn_finished`; explicit `Qt.UserRole` category
keys; `open_in_grc` no longer fails silently on missing
`gnuradio-companion`; 59/59 GUI tests green under `xvfb-run`.

#### Third-pass hardening (M8)
A systems-architecture audit addressed critical PySide6/Qt threading
violations, C++/Python GC gaps, and QProcess resource leaks. Full
items and contracts are documented in `AGENTS.md`
under Milestone 8. Highlights: thread-safe `cancel()` delegation via
`QMetaObject.invokeMethod(QueuedConnection)`; `_reap_active_processes`
disconnects and reaps old QProcess instances before new starts;
`QThread` parented to `MainWindow` and `deleteLater()`'d in cleanup to
prevent Python GC from outpacing the C++ event loop; `FailedToStart`
explicit cleanup; per-slot kill timer reuse to avoid transient-id
bugs.

#### Adversarial audit (M9, closed baseline)
A read-only, static, adversarial audit of the entire GUI surface
(`src/grc_agent_gui/` and `tests/gui/`) was conducted against the
standard 4 audit vectors. **M9 is a closed, evidence-only audit with no
code remediation.** The full findings (21 items: 0 CRITICAL / 4
MODERATE / 7 MINOR / 10 TEST-GAP) have been retired from `docs/`.
M9 also re-verified all 23 M6/M7/M8 claims at the cited `file:line`
locations — no claim-accuracy drift was found. Headline findings:
`console_log` lacks `setMaximumBlockCount` (unbounded memory on
long-running flowgraphs); stdout/stderr pipe round-trip has no rate
cap; `compile_and_run` is re-entrant; mid-tool cancel is untested. M9
is a polish-pass backlog, not a stability pass.

The next audit (M10) was conducted with an **expert-level** reviewer
prompt (the original junior-grade prompt used for M9 was upgraded in
place and has been retired with the audit reports). The expert-level
prompt adds a mandatory 7-step methodology, 20 specific grep probes, 4
file-hotspot groups, a 12-category test-gap taxonomy, a strict output
schema with file:line + 3-step trigger + confidence rating, and
anti-hallucination mechanisms.

#### Session history sidebar & bug-fix pass (M11)

**Session history sidebar** (`SidebarWidget`, `src/grc_agent_gui/sidebar_widget.py`):

- A persistent `QWidget` in the leftmost splitter pane replaces the
  modal `File > Recent Sessions...` dialog.
- Width defaults to 18% of the window; user-draggable; constrained to
  a maximum of 20%; collapsible via the `◀` button, the
  `File > Session Sidebar` menu item, or `Ctrl+Shift+H`.
- `populate_sessions()` takes a list of `SessionRecord` objects and
  renders them as `QListWidgetItem` entries. Double-clicking emits
  `session_selected(int session_id)`.
- Signals: `session_selected`, `new_chat_requested`, `collapse_requested`.

**Splitter layout contract** (`main_window.py`, `showEvent`):

- `QSplitter.restoreState` is deferred from `__init__` to a
  one-shot `showEvent` override so that `self.width()` returns the
  true realized geometry (e.g. 1702 px) rather than the
  pre-show value (~92 px).
- `setStretchFactor(0, 0)` / `setStretchFactor(1, 1)` /
  `setStretchFactor(2, 0)` — only the chat pane absorbs spare
  space on resize; the sidebar and inspector hold their pixel widths.
- Size guards applied in `showEvent` after `restoreState`:
  - sidebar > 20% → clamp to 18%.
  - inspector < 50 px (old 2-widget state migrated) → restore to 32%.
  - chat pane fills the remainder (minimum 300 px).

**Session resumption contract** (`_open_past_session`):

- `active_session_id` is set to the loaded session's ID (not `None`),
  so subsequent `send_prompt` calls append to the **same** SQLite
  record instead of opening a new one.
- `agent.history` is rebuilt from the stored `user` and `assistant`
  messages so the model sees full prior context on continuation turns.
  Tool rows (`tool_started`, `tool_finished`, `mutation`, `error`) are
  excluded from the model history (they are display-only).
- `reset_chat_session()` is still called first to clear the llama.cpp
  KV-cache session ID before reconstructing history.

**`ChatWidget` role model** (`chat_widget.py`, `_render_chat`):

- `append_status`, `append_mutation`, `append_error` now write named
  role entries to `self._history` and trigger `_render_chat()` instead
  of injecting raw HTML directly. This ensures all message types are
  replayed consistently during session resumption.
- `_render_chat()` dispatches on `role` with distinct HTML templates:
  `user`, `assistant`, `tool_started` (⚡ block), `mutation` (✓ green
  bar), `error` (✗ red bar), `tool_finished` (empty/hidden), and a
  catch-all `assistant` template for any unrecognised role.
- HTML rendering is memoized per message dict (`_rendered` key);
  re-renders on unchanged messages are a cheap list join.

**Test count**: 116 GUI tests (all green).
