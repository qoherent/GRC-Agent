# Changelog

## [Unreleased]

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
