# Changelog

## [Unreleased]

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
- Centralized runtime helpers: `text_utils.py`, `enums.py`, `integrity.py`.
- 7 legacy test files, regex/dtype heuristics, hardcoded allowlists removed.
- Mutation methods consolidated to native GRC APIs: `flow_graph.get_block()` replaces adhoc `_find_block`; `flow_graph.remove_element()` replaces manual list/set manipulation; `Block.STATE_LABELS` replaces hardcoded set.
- Dead code deleted: `validation/` package (3,415 lines), `transaction.py` apply path (~430 lines), `flowgraph_session.py` mutation wrappers (6 methods), 15 dead tests.
- Dead `name or key` fallbacks removed from `render_connection` and `history.py` (5 sites).
- System prompt: added "Parameter values are string expressions; a variable reference is the variable's name."

### Fixed
- `change_graph` error payloads are minimal: `{ok, committed, ops_applied, errors, error_type}`. Validation errors surface as `errors[].code == "gnu_validation"`. Removed triplicated `state_revision`, `validation`, `native_validation_errors`, `rejected_phase`, `graph_unchanged`, `hint`, `rollback` fields.
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
- `[llama].model` is now required (config without it silently degraded every LLM call).
- `sanitize_text` denylist removed (violated no-hand-picked-heuristics rule).
- In-band control flow in docs-synthesis prompt removed.
- Distance thresholds calibrated (`0.35`/`0.50`/`0.65`) from live wiki corpus.
- Embedding model configurable via `[llama].embedding_model`.
- gemma-3 task prefixes applied uniformly to queries and chunks.
- Resource safety: `try/finally` guards; `vec1.so` fallback raises clear error.
- Chunk size reduced to 256 words / 100-word overlap with full heading hierarchy.

### Changed — Catalog search: FTS5 → vector
- `query_knowledge(domain="catalog")` uses vec1+embeddinggemma pipeline (`.grc_agent/vectors/catalog_v1.db`).
- `retrieval_backend` reports `"vector"`; `match_type` is uniformly `"vector"`.

### Added
- RAG live-integration tests (`GRC_AGENT_LIVE_RAG=1`).
- `GrcAgent.warmup_vector_index()`, `is_db_usable()`, `[llama].embedding_model`.

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
