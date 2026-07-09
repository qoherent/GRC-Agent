# Changelog

## [Unreleased]

### Model reliability: prompt humanization + gemma4/laguna fixes

- **Scenario prompts now describe blocks by role/position** ("the noise
  source", "the adder") instead of quoting raw internal instance names — no
  real user talks about `analog_sig_source_x_0`. This surfaced several real
  bugs behind the humanization work, all fixed generally (no per-scenario
  hacks):
  - Catalog param values no longer leak their type-descriptor prefix
    (`raw=X` → `[raw]=X`, matching the enum shape already parsed correctly).
  - `param_filter.py`'s `keep_param()` now implements the `hide == 'none'`
    branch its own docstring always claimed — a param whose value equals its
    native default no longer silently vanishes from the overview.
  - `inspect_graph`'s `block_not_found` error now pairs each valid name with
    its `block_id`, so a role-based description can resolve without a
    wasted extra call.
  - New native-derived `port_count_controlling_params()` (mirrors
    `type_controlling_params`) — a missing-port error now names the param
    that controls that block's port count and its current value.
  - `change_graph` rejects a batch with every operation array empty/absent
    (`invalid_request`) instead of trivially returning `ok=true`.
  - A second, more lenient stuck-loop detector catches a model varying its
    arguments while repeating the same category of failure — cut one
    pathological run from 135 turns to 13.
  - A malformed tool call with corrupted arguments-in-name-field is now
    diagnosed as "malformed call naming X" instead of "tool not available."
  - One added declarative system-prompt fact: describing a `change_graph`
    call in reply text does not execute it.
  - A degenerate (no content, no tool calls) model response that gets
    retried used to leave zero trace anywhere but a log line. Now yields a
    `degenerate_retry` event (attempt number, `finish_reason`) so it shows
    up in saved transcripts — dev/debug observability only, not model-facing.
- **Deleted `max_tokens` entirely.** A hardcoded generation-length cap
  (2048 in the test harness, smaller than production) was confirmed by
  direct replay to truncate a model mid-reasoning before it could emit a
  tool call. No cap is sent in any request now — the backend's own output
  capacity is used, per AGENTS.md "no arbitrary limits."
- **Deleted `max_tool_rounds` entirely.** Computed every turn but never
  consumed to bound anything — the turn loop has been bounded only by
  stuck-loop detection for some time, per its own code comment. Removed the
  whole surface (`grc_agent.toml`, `LlamaConfig`, `ToolAgentsLlamaProviderConfig`,
  `ToolSurface`) rather than leaving dead config accepted and validated.

### Context & session hardening audit

- **Removed a real silent-drop bug in `sessions_store.py`.** The writer
  thread's `_drain_batch()` re-enqueued part of a drained burst back onto its
  own queue via `put_nowait`, dropping-and-logging on `queue.Full` —
  reachable in practice because blocking producers refill freed slots
  instantly. Fixed by removing the deferral entirely: once a message is
  dequeued into the local batch it is always committed, never put back.
  (A literal "make the re-enqueue blocking" fix was rejected — the writer is
  the queue's sole consumer, so blocking there would deadlock it.)
- **`param_filter.py`'s `id` exclusion is now dtype-driven, not name-driven.**
  `dtype == "id"` replaces `param_key == "id"`, matching upstream GRC's own
  property editor (which branches on this exact native dtype). The three
  remaining name-based exceptions (`showports`, `bus_structure_*`,
  `type`/`generate_options`) are now explicitly documented in the module
  docstring with why each one can't be derived from hide/category/dtype
  alone — two mirror names GRC's own object model reserves, one is
  undocumented app-level UX policy.
- **Deleted a dead code path that contradicted its own rule.** A `reminder`
  parameter injected `<runtime_directive>` text into the *system* message,
  contradicting the documented "runtime directives are user-role only" rule
  — and was never actually called with a value anywhere in production (only
  tests exercised it). The one live mechanism (a loop-detection notice)
  already correctly used a user-role message and is unaffected.
- **Removed hardcoded word caps on same-backend summarizer calls.**
  `web_answer.py` and `doc_answer.py` capped context fed into an LLM call
  that reuses the *same* configured chat backend/model as the main agent —
  an arbitrary limit on a model that already has a large context window.
- **Deleted dead guardrails config.** `max_tool_output_bytes`,
  `max_compact_list_items`, `history_compact_budget`, `max_tool_result_chars`
  had zero remaining consumers after an earlier compaction/budget-clamp
  removal; the config kept accepting and validating them anyway.
  `max_inspect_targets` (still live) is unaffected.
- **`_EMBED_MAX_WORDS` raised from a 256-word guess to 900, cited against
  real model limits.** `embeddinggemma` (Ollama) accepts 2048 tokens;
  `pplx-embed-v1-0.6b` (OpenRouter) accepts 32K — Gemma is the binding
  constraint. 900 words leaves ~16% margin even under a pessimistic
  1.8 tokens/word ratio. This was load-bearing, not theoretical: a corpus
  scan of the docs wiki found 60% of chunks were already being truncated at
  256 words (median real chunk size is 403 words); the new cap cuts that to
  28%. Verified against the live `embeddinggemma` endpoint and by rebuilding
  both vector indexes end-to-end.

### Lean-code cleanup audit

Seven parallel read-only subagents audited the 20 largest `.py` files (every
dead-code claim verified via a recursive `grep -rn` across both `src/` and
`tests/` before being reported) hunting for dead code, non-essential logic,
and duplication. High-confidence findings implemented, verified against the
full three-gate suite (default/`grc_native`/`gui`) after each file:

- `param_filter.py`: deleted an unreachable duplicate `hide == "none"` check
  (a check added earlier this session made a pre-existing later check
  permanently unreachable); extracted `_throwaway_block()` to collapse the
  duplicated platform/flow_graph/new_block dance shared by `param_metadata()`
  and `port_metadata()`.
- `change_graph.py`: deleted the orphaned `_neighbor_port_dtype()` (its sole
  caller had already been rewritten to use `_live_neighbor_dtypes_for`);
  removed an unused `existing_dtype` local from `_neighbor_dtype_for()`.
- `grc_native_adapter.py`: `classify_role_from_catalog()`'s
  `is_virtual_or_pad` check now matches GRC's own `Block.is_virtual_or_pad`
  exactly (an exact 4-key match instead of a `.startswith()` heuristic);
  `_find_port()` now reuses `port_object()` for its happy-path scan instead
  of duplicating the lookup inline.
- `catalog/schema.py`: deleted `compact_text()` and `build_signature()` (zero
  production callers — only fed each other and their own unit tests);
  `select_category_path()` simplified from a `(path, warnings)` tuple to a
  bare `list[str]` (every caller discarded the warnings half);
  `NormalizedParameter`/`NormalizedPort` dropped five unused fields
  (`label`, `option_labels`, `option_attributes`, `base_key`, `vlen`,
  `multiplicity`, `optional`, `hide` on the port side) never read outside
  their own construction.
- `chat_widget.py`: removed a vestigial `"text": ""` key on assistant-turn
  dict literals (assistant entries carry text exclusively via `fragments`,
  per the module's own documented convention); removed a no-op
  `("text", None)` tuple entry from the math-symbol replacement loop.
- `sessions_store.py`: dropped the unused `graph_exists` column from the
  sessions table schema; removed a `DELETE FROM messages` in `clear_all`
  that was redundant with the already-declared `ON DELETE CASCADE` foreign
  key (cascade deletes still fire the FTS cleanup trigger).
- `main_window.py`: removed ~15 `hasattr()` guards on attributes that are
  always set unconditionally in `__init__` (kept the one legitimate
  `hasattr(self, "_first_shown")` guard, which really is conditional);
  simplified `hasattr(self.agent, "reset_chat_session")` × 4 to a direct
  call (`GrcAgent` always defines it); deleted a dead `self.chat_display`
  alias (only `self.chat_widget.chat_display` is ever read); collapsed the
  3×-duplicated "session has a loaded flowgraph" check into
  `_has_flowgraph_loaded()` and the 2×-duplicated "prefer
  `provider_config.model` over `llama_config.model`" resolution into
  `_resolved_model_name()`.
- `toolagents_runtime.py`: deleted the orphaned `response_format` parameter
  from `create_settings()` (confirmed via `git log -S` to be leftover
  plumbing from a removed code path, never passed by any caller); extracted
  `_apply_turn_counters()` to collapse the 4×-duplicated turn-result payload
  field assembly; extracted `_model_message_event()`/`_last_message_event()`
  to collapse the 9×-duplicated `model_message` event construction.
- `run_agent_flow.py`: removed a stale `"Max tool rounds: system default
  (8)"` print left over from the `max_tool_rounds` purge; fixed a dangling
  comment citing the deleted `docs/AGENT_FLOW_FINDINGS.md`.

**Deliberately left unchanged:** `hierarchy_warnings()`, `_looks_hierarchical()`
(`catalog/schema.py`), and `resolves_to_hierarchical_class()`
(`grc_native_adapter.py`) — all flagged dead (zero callers) by the audit, but
kept per an earlier explicit decision this session to preserve the
hierarchical-detection chain.

### Config unification: one resolved `LlamaConfig`, no second silent resolution

- **`GrcAgent.__init__` no longer re-derives its own LLM/embedding backend
  state.** It used to always build `default_app_config().llama` internally
  (hardcoded `backend="ollama"`) regardless of what `grc_agent.toml` or the
  user's persisted provider preference said — so a same-process RAG
  summarizer call (`call_agent_llm`, used by `doc_answer.py`/`web_answer.py`)
  and the catalog/docs embedding index (`search_blocks.py`, `doc_answer.py`)
  could silently target Ollama even when the main chat path (routed through
  `ToolAgentsRunner`/`provider_config`) was correctly running against
  OpenRouter. Replaced the three scattered override params
  (`llama_server_url`, `llama_model`, `llama_request_timeout_seconds`) with
  one `llama_config: LlamaConfig | None` parameter — the whole backend
  identity moves as one coherent unit or not at all.
- **`app.py` now passes its already-resolved config into the agent.**
  `config = load_app_config()` is overlaid with the user's persisted
  provider preference (`apply_user_preferences_to_llama_config`) exactly as
  before; that same `config.llama` object is now threaded into
  `GrcAgent(session=session, llama_config=config.llama)` instead of being
  computed once for the window and never reaching the agent. One resolved
  config, three consumers (`bootstrap_runtime`, `MainWindow`, `GrcAgent`) —
  no second independent resolution path to drift out of sync.
  `tests/agent_flow/run_agent_flow.py` and `tests/test_catalog_vector_live.py`
  updated to the new parameter (the latter also drops a now-unnecessary
  private-attribute poke of `agent._llama_server_url`).
  `tests/test_config.py::GrcAgentLlamaConfigWiringTests` covers the fix
  directly, including the exact regression scenario (a persisted
  `provider_chosen="openrouter"` preference reaching the agent's runtime
  state).

### Packaging & embedding provider

- **`.env` is the single source of truth for model names.** Chat and embedding
  model names now live in `.env` (keyed by backend), not in `grc_agent.toml`.
  New variables: `OLLAMA_MODEL`, `OLLAMA_EMBEDDING_MODEL`,
  `OPENROUTER_EMBEDDING_MODEL` (defaults `gemma4:e4b-it-qat-120k`,
  `embeddinggemma:latest`, `perplexity/pplx-embed-v1-0.6b`). The GUI writes
  model changes back to `.env`, so hand-edits and GUI-edits stay in sync.
  _**Breaking:**_ `[llama].model` and `[llama].embedding_model` are no longer
  read from `grc_agent.toml`, and `preferences.json` no longer stores
  `last_model`. Move any custom model names into `.env` (see `.env.example`).
- **Embeddings work on both backends (Approach A).** The shared `get_embedding`
  now uses the `openai` SDK against the OpenAI-compatible `/v1/embeddings`
  endpoint for both Ollama and OpenRouter — mirroring how chat already used the
  SDK. This deletes the bespoke `httpx` POST to Ollama's deprecated
  `/api/embeddings` endpoint. OpenRouter mode now has working RAG (previously
  it silently had none).
- **Per-backend vector stores.** Each backend owns its own index pair
  (`catalog_<backend>.db` / `docs_<backend>.db`); switching backend swaps which
  pair is active without a rebuild. Dimension is probed from the first
  embedding (no more hardcoded `_EMBED_DIM`), and each DB stamps its embedding
  model and rebuilds automatically when the model changes — so swapping
  embedding models is safe.
- **GUI: embedding-model field.** The model toolbar now shows/edit the
  embedding model alongside the chat model, for both backends. Backend/model
  swaps call `GrcAgent.reconfigure_llama_runtime` so the embedding path and
  the docs-RAG synthesis model follow the active backend.
- **Direct deps declared.** `openai`, `python-dotenv`, and `httpx` are now
  declared as direct dependencies (they were used directly but only
  transitively declared — a latent packaging bug). The embedding pipeline no
  longer imports `httpx` directly (it routes through the `openai` SDK); other
  modules still use it for the Ollama REST/web-search tools.
- **Distribution:** source-only via `uv` (git clone + `uv sync`). GNU Radio is
  a hard system dep, so a PyPI `pip install` is intentionally not offered.
- **Repo pruning:** removed unreferenced `docs/adhoc_cleaner_agent_prompt.md`
  and stale `docs/superpowers/plans/`; fixed stale references in `.gitignore`,
  `grc_agent.toml`, and `AGENTS.md`.

### OpenRouter web search (plugin)

- **Web grounding on OpenRouter**: the OpenRouter backend now activates the `web` plugin (`extra_body["plugins"]=[{"id":"web",...}]`) on by default, so the model's answers are grounded with live web results. OpenRouter returns `url_citation` annotations, which are surfaced as a `Sources:` footnote on the assistant message (both streaming and non-streaming paths).
- **Backends stay separate**: the Ollama backend keeps its existing `web_search`/`web_fetch` REST tools unchanged. On OpenRouter those Ollama-hosted tools are dropped from the surfaced tool set (they cannot run against OpenRouter); the plugin replaces them.
- **Config**: opt out with `OPENROUTER_WEB_SEARCH=false`; tune `OPENROUTER_WEB_SEARCH_MAX_RESULTS` (1–10), `OPENROUTER_WEB_SEARCH_INCLUDE_DOMAINS`/`_EXCLUDE_DOMAINS` (CSV).

### Codebase Audit & Performance Polishing

- **Engineering Rule Compliance**: Eliminated all violations identified in the codebase audit (such as hidden allowlists, hardcoded heuristics, silent overrides, and duplicate logic).
- **Concise Catalog Search Output**: Renders catalog queries using Stage B parameter filtering (`OVERVIEW` mode) by default. Strip `"Core > "` category prefix, round distances to 3 decimal places, omit empty lists/dicts, and format boolean enums compactly (e.g. `bool=False`). Saves **~50% of tokens** per hit.
- **Removed Tool-Round Ceiling Enforcement**: Completely removed the hard tool-round ceiling checks in `ToolAgentsRunner` to let models iterate until completion.
- **Improved Local Model Prompting**: Refined system prompt template with clear instructions on graph validation invariants, orphaned block resolution, intermediate force flag commits, and strict catalog lookup grounding. Boosted Ornith-9B scenario 06 pass rate to **5/5 (100% success)**.

### Agent flow optimization (5/8 → 7/8 semantic success)

- **Schema flattening:** `add_connections` and `remove_blocks` now use flat strings (depth 3→2). Read/write symmetric with `inspect_graph` output.
- **Payload simplification:** `change_graph` returns `{"ok": true/false, "errors": [...]}` — no internal plumbing (`committed`, `ops_applied`, `rollback_failed`).
- **num_ctx=120000:** Was using Ollama default 4096 (model native is 131072). Eliminated output truncation.
- **Auto-resolve type:** Adapter infers missing `type` param on newly-added polymorphic blocks from the connected neighbor's port dtype. Reports via `auto_resolved` field.
- **Error locality:** Validation errors now include block+port identity (`"blocks_add_xx: Sink - in2(2): Port is not connected."`). Was bare `"Port is not connected."`.
- **Catalog enum values:** `query_knowledge` returns `"enum=[complex,float,int,short]=complex"` instead of `"enum="` with empty options.
- **Connection ordering:** `remove_connections` runs before `add_connections` (prevents transient double-upstream on inline-insert).
- **Idempotent remove_connection:** Skip silently if edge already gone via cascade.
- **System prompt:** Added `*_xx` default-complex direction + expression params direction.

### Dead code deleted (~4,200 lines)

- `validation/` package (checks.py, rules.py, errors.py, raw_parse.py — 3,415 lines)
- `transaction.py` apply path (~430 lines — apply_edit, propose_edit, apply_operations, clone_session, commit_candidate_session, build_apply_*_payload)
- `flowgraph_session.py` mutation wrappers (6 methods)
- 15 dead tests (transaction/, validation/)

### Native API consolidation

- `FlowGraph.get_block()` replaces adhoc `_find_block` (was verbatim reimplementation)
- `FlowGraph.remove_element()` replaces manual list/set manipulation for block + connection removal
- `Block.STATE_LABELS` replaces hardcoded state set
- Dead `name or key` fallbacks removed (5 sites)

### Added — GRC Native Refactor
- `grc_native_adapter.py`: bridge confining all `gnuradio` imports.
- `domain_models.py`: Pydantic V2 schemas (outbound `extra="forbid"`, inbound `extra="ignore"`).
- `runtime/param_filter.py`: single uniform rule for param visibility.

### Changed
- `flowgraph_session.py` gutted 1596→447 lines; `flowgraph` is now a live `gnuradio.grc.core.FlowGraph`.
- `inspect_graph.py` rewritten 1052→248 lines; `change_graph.py` rewritten 1277→370 lines (flat batch dispatch, stale-revision gate, noop detection, autosave).
- All mutation paths go through adapter; `export_data()`/`import_data()` used for transaction snapshots.
- CLI removed; GUI is sole surface. Dead wizard/dialog code (~1200 LOC) deleted.
- `tool_context` rendering now emits per-error `hint:` lines and structured `error:` lines.
- System prompt rewritten from imperative prose to declarative contract bullet lists.
- All in-band behavioral directives removed from model-visible strings.
- Centralized runtime helpers: `enums.py`.
- 7 legacy test files, regex/dtype heuristics, hardcoded allowlists removed.
- Mutation methods consolidated to native GRC APIs: `flow_graph.get_block()` replaces adhoc `_find_block`; `flow_graph.remove_element()` replaces manual list/set manipulation; `Block.STATE_LABELS` replaces hardcoded set.
- Dead code deleted: `validation/` package (3,415 lines), `transaction.py` apply path (~430 lines), `flowgraph_session.py` mutation wrappers (6 methods), 15 dead tests.
- Dead `name or key` fallbacks removed from `render_connection` and `history.py` (5 sites).
- System prompt: added "Parameter values are string expressions; a variable reference is the variable's name."

### Fixed
- `change_graph` error payloads are minimal: `{ok, errors, error_type}`. Validation errors surface as `errors[].code == "gnu_validation"`. Removed `committed`, `ops_applied`, `state_revision`, `validation`, `native_validation_errors`, `rejected_phase`, `graph_unchanged`, `hint`, `rollback` fields.
- `vlen` connection mismatch now has explicit hint.
- `search_blocks` false-positive `output_truncated` flag corrected.
- `validation/rules.py` block-rules cache key reads `BlockDescription.parameters` (not `to_payload()`).
- `doc_answer.py` call sites rewritten to avoid `AttributeError`.
- GUI no longer `sys.exit`s on backend failure — launches in degraded mode.
- CLI helpers/tests pruned; library logic preserved.

### Refactored — GUI
- Block taxonomy uses native `BlockRole` StrEnum dispatch table (no substring cascade).
- Slash-command routing uses dispatch table; `\`-prefixed alternates dropped.
- Magic strings hoisted to `config.py`.
- MagicMock test leak removed.

### Fixed — RAG audit (findings S1–S10)
- Background ingestion no longer pollutes production vector DB (`warmup_vector_index()` is explicit).
- `[llama].model` is now sourced from `.env` (was a required toml key; silently degraded every LLM call when missing).
- `sanitize_text` denylist removed (violated no-hand-picked-heuristics rule).
- In-band control flow in docs-synthesis prompt removed.
- Distance thresholds calibrated (`0.35`/`0.50`/`0.65`) from live wiki corpus.
- Embedding model configurable via `.env` (`OLLAMA_EMBEDDING_MODEL` / `OPENROUTER_EMBEDDING_MODEL`).
- gemma-3 task prefixes applied uniformly to queries and chunks.
- Resource safety: `try/finally` guards; `vec1.so` fallback raises clear error.
- Chunk size reduced to 256 words / 100-word overlap with full heading hierarchy.

### Changed — Catalog search: FTS5 → vector
- `query_knowledge(domain="catalog")` uses sqlite-vec vector pipeline (per-backend `catalog_<backend>.db`).
- `retrieval_backend` reports `"vector"`; `match_type` is uniformly `"vector"`.

### Added
- RAG live-integration tests (`GRC_AGENT_LIVE_RAG=1`).
- `GrcAgent.warmup_vector_index()`.

### Multi-backend LLM support
- Ollama (probed via `/v1/models`) and OpenRouter (`OPENROUTER_API_KEY` from `.env`).
- GUI inline model toolbar replaces setup wizard; first-launch provider picker.
- Degraded mode on backend failure (chat input disabled, toolbar is recovery path).
- `ChatHistory` typed model; real-token streaming via `ToolAgentsRunner.stream_turn`.
- Retry-storm guard (identical calls in one turn short-circuited).
- One-pass proportional compaction with truncation flags.
- Session-history sidebar (SQLite + FTS5, async writer, `File > Recent Sessions…`).

### `inspect_graph` data-layer refactor
- Uniform 5-field `_base_payload` shape for all views.
- `variable_references` always present (lifted from `param_filter` gate).
- `param_keys_by_block` filtered to GRC-evaluated `hide != 'all'` (87-key blocks → ~3).
- `role` added to details rows; renderer promotes all `errors[i].message` lines.
- `ambiguous_target` lists matched candidates; `target_not_found` lists valid blocks.
- `is_variable_block` bridged to native `Block.is_variable`.
- Dead code removed (~50 lines); `GuardrailsConfig` fields wired into TOML loader.
- FTS5 wiki index: `porter unicode61` tokenizer, BM25F weights, 3 cargo-cult items removed.
- 3 class-C heuristics dropped from `rank_docs_candidates`.

## [0.1.0] - 2026-06-05

First open-source release.

- CLI (`grc-agent`): REPL chat, one-shot, scripted tool invocation, vector index mgmt, history journal, dogfood, diagnostics.
- GUI (`grc-agent-gui`): PySide6 desktop with streamed chat, live flowgraph inspector, process manager.
- 3 model-facing tools (`inspect_graph`, `query_knowledge`, `change_graph`).
- Local retrieval over bundled GNU Radio corpus (Qdrant + FastEmbed).
- Shared CLI/GUI bootstrap with lazy llama.cpp auto-launch.
- MIT license, `uv` install path, CONTRIBUTING/CODE_OF_CONDUCT/SECURITY.
