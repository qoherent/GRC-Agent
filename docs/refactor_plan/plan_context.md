# Refactor Plan Context

> **Audience:** The agent (or human) executing any phase file in this directory.
> **Purpose:** Single source of cross-phase context. Every phase file is self-contained but refers here for shared rules, env facts, file maps, and architectural decisions.

---

## 1. Project & Intent

**GRC Agent** is a Q&A agent for GNU Radio Companion (`.grc`) flowgraphs. The current architecture parses `.grc` files as raw YAML dicts and crawls them in Python. The maintainer has authorized a **non-surgical, aggressive redesign** to replace all YAML-dict parsing with the native `gnuradio.grc.core` Python bindings.

The high-level goal: the model-facing tool output should be a flattened, Pydantic-typed snapshot of the actual GRC `FlowGraph` object, with native visibility filters (`param.hide`, `param.category`, `Block.is_variable`, etc.) applied at the source rather than via post-hoc regex routing.

**Mantra:** *One uniform rule per concern. Per-block allowlists are forbidden.*

---

## 2. Companion Documents

| Doc | Role |
|---|---|
| `docs/GNU_NATIVE_METHODS.md` | **Single source of truth** for the `gnuradio.grc.core` Python API surface on this machine (GR 3.10.9.2). Class dictionary (§1), evaluation pipeline (§2), param filtering & visibility (§3), wildcard port resolution (§4), validation (§5), headless orchestration blueprint (§6). Read this before any `grc_native_adapter` work. |
| `AGENTS.md` (repo root) | Architectural rules, MVP surface rules, runtime constraints. **Authoritative for behavior.** |
| `playground/inspect_experiment/` | Existing experiment for the inspect tool. Phase 1 builds on this directly. |
| `playground/<change_graph\|query_knowledge>_experiment/` | To be created in Phases 2 and 3. |
| `docs/comprehensive_native_refactoring_plan.md` | The 87-line weak outline this plan supersedes. **Deleted** at start of Phase 0. |

---

## 3. The MVP Surface (Read First)

Per `AGENTS.md` and `src/grc_agent/runtime/model_context.py:206–232`:

| Tool | Direction | Phase | Touches the flowgraph state? |
|---|---|---|---|
| `inspect_graph` | read | **1** | yes — needs native GRC adapter |
| `change_graph` | write | **2** | yes — needs native GRC adapter |
| `query_knowledge` | read | **3** | no — routes to `search_blocks` (catalog) or `ask_grc_docs` (docs). May not need a refactor; the experiment decides. |

Internal tools that also touch flowgraph state and get handled in Phase 6:
- `summarize_graph` (`agent.py:_summarize_graph` → `session.py:94` → `flowgraph_session.py:601` `summary_payload`)
- `get_grc_context` (`agent.py:_get_grc_context` → `session.py:55` → `flowgraph_session.py:656` `context_payload`)
- `describe_block` (catalog only; not affected)
- `apply_edit`, `propose_edit`, `validate_graph`, `save_graph` (mutation + IO; handled in Phase 6)
- `insert_block_on_connection`, `auto_insert_block`, `remove_connection`, `rewire_connection` (mutation helpers; handled in Phase 6)

---

## 4. Aggressive Redesign — The Non-Negotiables

These come from `AGENTS.md` and the maintainer's instruction. Every phase must honor them.

1. **No surgical edits to `flowgraph_session.py`.** Build new modules, route traffic, then gut the old.
2. **No backward compatibility.** No shims, no dual-format persistence, no legacy synthesis layers. On legacy structures or missing payload fields, refuse the load.
3. **No in-band control flow.** No model-visible string may contain ALL-CAPS directives, "Use this when …", "Call X now", or procedural recipes. Applies to tool schemas, wrapper outputs, error messages, runtime directives, hint strings, recovery prompts.
4. **One uniform rule per concern.** No per-block per-port per-connection allowlists, no per-scenario branches, no per-dtype regex routing. If logic is needed, it applies to every case.
5. **Prefer native methods.** Use the underlying GRC API before reimplementing logic. Example: `param.hide` and `param.category` come from `gnuradio.grc.core`, not a hand-rolled filter.
6. **Fix at the source.** Correctness lives in the tool/handler that produces data, not in a post-processor that carves it down.
7. **No silent transformation.** Any truncation, filtering, or omission in model-facing output must be explicitly flagged (what + how much). Never drop data without telling the consumer.
8. **Simplify by removal.** Prefer removing code over adding it. A one-line fix at the source beats a fifty-line wrapper.
9. **Manual execution loop.** `ToolAgentsRunner._run_turn_events` with bounded `.step()`. No result caching. No daemon management.
10. **No hardware polling, no `subprocess.Popen` for external servers, no `sys.exit()` on network failure.** Launch into degraded mode if the backend is unreachable.

---

## 5. Verified Environment Facts (Live on This Box)

These were verified by subagent probe on the current machine. **Every phase file inherits these as ground truth.**

| Fact | Value | Implication |
|---|---|---|
| `gr.version()` | `3.10.9.2` | **There is no `gnuradio.version()`.** Use `from gnuradio import gr; gr.version()`. |
| Top-level `grc.core` is empty | `from gnuradio.grc.core import Platform` **fails** | Use `from gnuradio.grc.core.platform import Platform`. Submodule path is mandatory. |
| `from gnuradio.grc.core import FlowGraph` | **fails** | Use `from gnuradio.grc.core.FlowGraph import FlowGraph`. |
| `from gnuradio.grc.core import Constants` | works | Only `Constants` is exposed at the top level. |
| `from gnuradio.grc.core.io import yaml` after warming `platform.Platform` | works | **Import order matters.** `io/yaml.py` triggers a circular import if loaded before `platform.Platform`. Always import `platform` first. |
| `Platform().build_library()` | headless-safe | No `DISPLAY`, `gtk`, `qt`, or `gobject` references in `platform.py`. CI runners without X are fine. |
| `GRC_BLOCKS_PATH` env var | unset on stock apt | Falls back to `/usr/share/gnuradio/grc/blocks/`. Missing → `RuntimeError: Failed to find built-in GRC blocks`. |
| PyYAML version | 6.0.1 | GRC uses PyYAML (not `ruamel`). The `grc.core.io.yaml` shim is **not** a PyYAML drop-in. |
| apt package | `gnuradio 3.10.9.2-1.1ubuntu2` | No separate `python3-gnuradio` package on Ubuntu. CI install: `apt install gnuradio`. |

**Reference for the lazy `get_platform()` pattern that every adapter must use:**

```python
def get_platform():
    """Return a fully-initialized grc.core.platform.Platform, or raise a clear error.

    Lazy singleton. Never import gnuradio at module top-level — CI without
    GNU Radio must be able to import the module without crashing.
    """
    try:
        from gnuradio import gr
        from gnuradio.grc.core.platform import Platform as _PlatformCls
    except ImportError as e:
        raise RuntimeError(
            f"GRC Agent requires GNU Radio 3.10.x with grc.core. "
            f"Import failed: {e}. "
            f"On Debian/Ubuntu install: apt install gnuradio gnuradio-dev."
        ) from e
    global _PLATFORM
    if _PLATFORM is None:
        _PLATFORM = _PlatformCls(
            name="grc_agent",
            prefs=gr.prefs(),
            version=gr.version(),
            version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
        )
        _PLATFORM.build_library()
    return _PLATFORM
```

Three things this pattern must do that the legacy `session._ensure_platform` does not:
1. Use `gr.version()` from the `gr` submodule.
2. Never reach into `gnuradio.version()`.
3. Always warm the `params` module before touching `io/yaml`.

---

## 6. File Map (Current State, From Subagent Audit)

### 6.1 The legacy surface to be replaced

| File | Lines | Role | Phase that touches it |
|---|---:|---|---|
| `src/grc_agent/flowgraph_session.py` | **1596** | YAML load/save, dict-crawling inspection, mutation, `grcc` subprocess validation. **No `gnuradio.*` imports.** | 1 (experiment only, no rewrite), 6 (gut, single cutover) |
| `src/grc_agent/runtime/inspect_graph.py` | **1157** | Model-facing `inspect_graph` tool. Heaviest dict-crawler: `_param_keys_by_block` (lines 727–792). | 1 (experiment only, no rewrite), 6 (rewrite, single cutover) |
| `src/grc_agent/runtime/change_graph.py` | **1432** | Model-facing `change_graph` tool. Normalizes flat batch, dispatches via `agent._apply_edit` → `transaction.apply_edit`. | 2 (experiment only, no rewrite), 6 (rewrite, single cutover) |
| `src/grc_agent/grc_native_adapter.py` | 0 (does not exist) | The new native adapter. | 5 (initial build, complete with mutations), 6 (used by the cutover) |
| `src/grc_agent/domain_models.py` | 0 (does not exist) | Pydantic V2 outbound (`extra="forbid"`) and inbound (`extra="ignore"`) schemas. | 4 (initial build) |
| `src/grc_agent/session.py` | (TBD) | Wraps `FlowgraphSession` with high-level helpers. | 6 (rewrite) |
| `src/grc_agent/transaction.py` | (TBD) | `apply_edit` / `propose_edit` mutation pipeline. | 6 (rewrite) |
| `src/grc_agent/session_ops.py` | (TBD) | 8 `shared_*` helpers used by `flowgraph_session.py`. | 6 (delete) |
| `src/grc_agent/_payload.py` | (TBD) | Legacy `Block` / `Connection` / `Flowgraph` types. | 6 (delete) |
| `src/grc_agent/agent.py` | 76,835 bytes | Tool registry, dispatch, lifecycle. | 6 (5 tool handlers updated) |

### 6.2 GUI files (Phase 7)

| File | Lines | What it reads from `inspect_graph` output |
|---|---:|---|
| `src/grc_agent_gui/inspector.py` | 252 | `payload["summary"]["blocks"]` (legacy) + `payload["_block_params"]` (sidecar). Both die in Phase 7. |
| `src/grc_agent_gui/main_window.py` | (snippet 233–244) | Produces the `_block_params` sidecar. Dies in Phase 7. |
| `tests/gui/test_inspector_widget.py` | 278 | 8 tests, all mock `summary.blocks` + `_block_params`. Rewritten in Phase 7. |
| `tests/eval_chat/harness.py` | (lines 28–43) | 1 `tool_stubs["inspect_graph"]` fixture. Updated in Phase 7. |

### 6.3 The 29 `FlowgraphSession` importers

All 29 sites import only the `FlowgraphSession` symbol. After Phase 6, the symbol survives (mutation + integrity + atomic save). The methods that get gutted are listed in Phase 6.

| Category | Files |
|---|---|
| `src/grc_agent/` | `__init__.py:7`, `agent.py:27`, `session.py:22–26`, `history.py:22`, `transaction.py:11`, `runtime/inspect_graph.py:12`, `runtime/change_graph.py:13`, `validation/checks.py:18`, `validation/errors.py:14` |
| `src/grc_agent_gui/` | `app.py:11` |
| Tests | `test_save_integrity.py`, `test_change_graph_flat_batch.py`, `transaction/test_commit.py`, `test_history_journal.py`, `test_graph_safety_regressions.py`, `test_agent_loop_fixes.py`, `test_yaml_refusal_and_save_path.py`, `test_maintenance_watch_guards.py`, `test_reliability_hardening.py`, `session/test_load_grc.py`, `session/test_load_grc_errors.py`, `session/test_summarize_graph.py`, `session/test_graph_id_roundtrip.py`, `test_runtime_tool_validation.py`, `retrieval_eval/test_rag_integration.py` (conditional) |
| Eval harnesses | `llama_eval/harness.py`, `llama_eval/r2_fixer_eval.py`, `llama_eval/r2_pivot_trace.py` |

---

## 7. The Plan — Phases at a Glance

**Architectural principle (per consultant review):** the model-facing tool rewrite and the `flowgraph_session.py` gut **must** happen in a single coordinated cutover (Phase 6), not in stages. Phases 1–3 are **experiment-only** — they prove the native API works but do not rewrite any agent code. Phases 4–5 build the foundation (Pydantic models + complete native adapter including mutations) that Phase 6 needs to do the cutover cleanly.

| Phase | Name | Key outputs | Verification gate |
|---|---|---|---|
| **0** | Aggressive cleanup | Dead code removed, dangling imports fixed, no functional change | `pytest -m "not grc_native and not gui and not llama_eval"` passes; `git diff --stat` is purely deletions + import-fixes |
| **1** | `inspect_graph` (experiment only) | `playground/inspect_experiment/analysis.md` + `wire_shape_proposal.md`. **No source-code changes.** | Both experiment scripts run cleanly; `analysis.md` documents the legacy-vs-native diff; source tree unchanged |
| **2** | `change_graph` (experiment only) | `playground/change_graph_experiment/analysis.md`. **No source-code changes.** | 5 mutations × 9 fixtures exercise legacy + native; `analysis.md` documents gaps and latency; source tree unchanged |
| **3** | `query_knowledge` (experiment only) | `playground/query_knowledge_experiment/analysis.md`. Expected outcome: no refactor. | Catalog/docs queries return sensible results; `verify_native_api.py` proves no native API exists for catalog/docs |
| **4** | Domain models | `src/grc_agent/domain_models.py` with Pydantic V2 outbound (`extra="forbid"`) AND inbound (`extra="ignore"`) schemas | `tests/test_domain_models.py` passes 11+ tests; `model_json_schema()` is the canonical wire shape; inbound drops extra fields silently |
| **5** | Native adapter (initial build) | `src/grc_agent/grc_native_adapter.py` with loading, mutations, validation, identity (file-bytes SHA + instance id + revision counter — **no deep-JSON hashing**), inspection helpers, serialization | 25+ tests in `tests/test_grc_native_adapter.py` pass; `rg -n 'gnuradio' src/grc_agent/` matches only in the adapter; `rg -n 'compute_graph_id\|hashing' src/grc_agent/` returns zero matches |
| **6** | Single cutover | `flowgraph_session.py` gutted (1596 → ~300 lines); `session_ops.py` + `_payload.py` deleted; `inspect_graph.py` + `change_graph.py` + `transaction.py` + `history.py` + `validation/checks.py` + `agent.py` all rewired to the adapter in one coordinated pass. **No blue/green flag.** | `pytest -m "not grc_native and not gui and not llama_eval"` passes; net reduction ≥ 1000 lines in `flowgraph_session.py`; `git tag phase-6-cutover` |
| **7** | GUI burn | `inspector.py` consumes new flat shape; `_block_params` sidecar deleted; 8 GUI tests + eval stub rewritten | `pytest -m gui` passes under `xvfb-run`; manual smoke run on `examples/random_bit_generator.grc` renders correctly |

**Phase ordering constraint:** 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7. No skipping, no reordering. The downstream phases depend on the upstream outputs.

**Per-tool pattern (Phases 1, 2, 3 — experiment only):**
1. **Experiment** in `playground/<tool>_experiment/` (existing for inspect; new for change and query).
2. **Capture results** in `playground/<tool>_experiment/results_native/`.
3. **Diff the legacy vs native output** in `<tool>_experiment/analysis.md`.
4. **Document the proposed wire shape / mutation API** in `<tool>_experiment/wire_shape_proposal.md` (inspect only) or `analysis.md` (change, query).
5. **Do NOT touch any source file in `src/grc_agent/`.** The agent continues to operate on the legacy code; behavior is unchanged.

**Per-phase pattern (Phases 4, 5, 6, 7 — build & cutover):**
1. **Read this context** to inherit env facts and architectural rules.
2. **Read the upstream phase's handoff doc** to understand what was proven out.
3. **Build, test, gut** per the phase's deliverable list.
4. **Verify** against the phase's verification gate.
5. **Write the phase's handoff doc** with the verification evidence.

**Critical consultant-corrections encoded in the plan:**
- **No split-brain mutation:** Phases 1, 2, 3 are experiment-only. The tool rewrite and the session gut happen together in Phase 6 as a single cutover. Mutations and validations operate on the same native `FlowGraph` object from start to finish.
- **Pydantic `extra="forbid"` only for outbound:** the models serialize graph state to the LLM. Inbound tool arguments (LLM → agent) use `extra="ignore"` so the agent never hard-crashes on a hallucinated extra parameter.
- **No deep-JSON hashing for graph IDs:** identity is the file-bytes SHA-256 (cross-session) plus a per-`FlowGraph` revision counter (in-session). Both are O(1) on every turn. The consultant specifically rejected hashing a `model_dump_json()` of the Pydantic model.

---

## 8. Cross-Phase Edge Cases

These are the env-specific or model-facing pitfalls that any phase may hit. **Every phase file re-references the relevant subset.**

### 8.1 Environment

- **GNU Radio not installed** → `ModuleNotFoundError: gnuradio` on first import. Mitigation: lazy import + `RuntimeError` with apt-install message (see `get_platform()` pattern above).
- **`grc.core` is stripped** → `from gnuradio.grc.core.platform import Platform` fails. Same `RuntimeError` path.
- **Wrong import path** → `from gnuradio.grc.core import Platform` raises `ImportError: cannot import name 'Platform'`. The top-level `grc.core` package is empty; always use submodule paths.
- **Import order** → `from gnuradio.grc.core.io import yaml` before warming the platform raises `ImportError: cannot import name 'Param' from partially initialized module 'gnuradio.grc.core.params'`. Looks like an internal GRC bug; not. Always warm the platform first.

### 8.2 Load-path

- **Malformed YAML** → `yaml.YAMLError` from `parse_flow_graph`. Return `ok=False`, `errors=[{code: "YAML_PARSE_ERROR", message: str(e)}]`. Never propagate the stack trace to the model.
- **Empty `blank.grc`** → only `options` block. Return `GrcFlowgraph(ok=True, blocks=[], connections=[], validation.status="valid")`.
- **Variable references itself or creates a cycle** → `RuntimeError` from `_reload_variables` after `rewrite()`. Catch, set `validation.status="invalid"`, append the error to `validation.errors`. The model still gets a partial graph.
- **Variable references an undefined name** → `NameError` from `eval()`. Same catch path.
- **Block has no `key` attribute** (rare custom block) → default to `BlockRole.OTHER`. Do not skip.
- **Block has no `params`** (broken `.grc`) → skip the parameter loop for that block. Log a structured warning. Block still renders.
- **`state` property returns something unexpected** → native property is `'enabled' | 'disabled' | 'bypassed'`. Map to `"enabled" | "disabled" | "bypass"` (the GUI spelling) at the rendering layer.
- **Wildcard port type not resolved at parse time** → call `flow_graph.rewrite()` before reading `port.dtype`. After rewrite, dtype is populated.
- **`iter_enabled_blocks` skips a block the model needs to see** → render **all** blocks via `flow_graph.blocks`, set `state="disabled"` or `state="bypass"` on disabled ones. Model can filter.
- **File path with non-UTF-8 BOM** → open with `encoding="utf-8-sig"`.
- **File path is a directory** → catch `IsADirectoryError`, return `errors=[{code: "NOT_A_FILE", message: "..."}]`.

### 8.3 Runtime / state

- **`state_revision` is bumped by the adapter but the session doesn't know** → adapter must call `agent.session._bump_state_revision()` after each `load_and_inspect`. The model-visible `state_revision` is now a function of the native `flow_graph`, not the YAML dict.
- **`agent.session.flowgraph` is a `Flowgraph` (parsed-model) and downstream code expected a `dict`** → `TypeError: 'Flowgraph' object is not subscriptable`. This is the core tension. The 29 importers of `FlowgraphSession` must be migrated in Phase 6 (single cutover). **No backward-compat shim, no blue/green flag.**
- **`validation_state()` no longer runs `grcc`** → faster, but the `grc.core.FlowGraph.validate()` already runs internal validation. If compile-time confidence is needed, expose `flow_graph.generate()` as a future phase.
- **`graph_id()` (SHA-256 of raw YAML) changes shape** → use the new `GraphIdentity` (file-bytes SHA-256 + per-`FlowGraph` revision counter). The file-bytes SHA handles cross-session identity; the revision counter handles in-session change detection. **Do not hash a `model_dump_json()` of the Pydantic model** — the consultant rejected this for latency.
- **`persisted_file_sha256`** → the file is read once; capture the SHA-256 of the file bytes at the same moment. This is the same as the file-bytes SHA in `GraphIdentity`.
- **`metadata.file_format` (legacy field)** → read `flow_graph.options_block.params['file_format'].value` if present; default to `1`.

### 8.4 GUI / display

- **Inspector receives a payload without `summary`** → add a defensive fallback: `graph = data.get("graph", data.get("summary", {}))`.
- **Parameters now inlined into each block** → tree may explode. The existing `_parameter_sample` preview cap (6 keys) applies.
- **`_block_params` removed but legacy integration tests still reference it** → Phase 7 rewrites all 8 mock payloads.
- **`category_for_role` mapping uses string keys** → update the mapping in `inspector.py:201–205` to match new `BlockRole` enum members.

### 8.5 Test

- **`tests/test_reliability_hardening.py`** asserts on `result.get("errors", [])` being empty after a successful call. The new `inspect_graph` returns `errors=[]` (not `None`) on success. Verify after rewrite.
- **`tests/session/test_summarize_graph.py`** (3 tests) asserts on the legacy `summarize_graph` shape. Updated in Phase 6.
- **`tests/eval_chat/fixtures/*.json`** reference `summary.blocks` indirectly. Phase 7 audits and updates.
- **`tests/test_model_context_bible.py`** regenerates `MODEL_CONTEXT_BIBLE.md` and asserts byte-equality. Run with `UPDATE_MODEL_CONTEXT_BIBLE=1` once after Phase 1 to refresh.
- **`tests/test_runtime_tool_validation.py`** tests input schemas only. Unaffected by all phases.

---

## 9. Testing Strategy (Cross-Phase)

| Layer | Pytest marker | When to run |
|---|---|---|
| Unit (no GRC) | (none) | every CI |
| GRC-dependent | `@pytest.mark.grc_native` | dev box or CI with `apt install gnuradio` |
| GUI | `@pytest.mark.gui` | dev box or `xvfb-run` |
| Llama eval | `@pytest.mark.llama_eval` | manual / pre-release |
| Eval chat | (none) | manual / pre-release |

**Default CI command:** `pytest -m "not grc_native and not gui and not llama_eval"`

---

## 10. Commit Cadence (TDD, Frequent Commits)

The convention is small atomic commits. Use `feat`, `refactor`, `test`, `docs`, `chore` prefixes.

- `chore(phase-0): remove dead module X` — Phase 0
- `chore(phase-1/inspect): refresh experiment and document wire shape` — Phase 1 (no source-code changes; this is the experiment commit)
- `chore(phase-2/change): refresh mutation experiment and document legacy-vs-native gaps` — Phase 2 (no source-code changes)
- `chore(phase-3/query): verify query_knowledge needs no native refactor` — Phase 3 (expected to be a docs-only commit)
- `feat(phase-4/models): add Pydantic v2 outbound and inbound schemas` — Phase 4 (single new file)
- `feat(phase-5/adapter): add complete native adapter (load + mutate + validate + identity)` — Phase 5 (single new file + tests)
- `refactor(phase-6/cutover): single cutover to native adapter` — Phase 6 (one or more atomic commits; no blue/green)
- `refactor(phase-7/gui): burn GUI to new flat shape, delete _block_params sidecar` — Phase 7

---

## 11. Definition of Done (Cross-Phase)

The full refactor is complete when **all** of the following hold (any phase file can re-reference this list):

1. `pytest -m "not grc_native and not gui and not llama_eval"` passes.
2. `pytest -m grc_native` passes on the dev box (with `apt install gnuradio`).
3. `pytest -m gui` passes via `xvfb-run`.
4. `git grep -n 'yaml\.safe_load\|yaml\.safe_dump\|subprocess.*grcc' src/grc_agent/` returns zero matches.
5. `git grep -n 'gnuradio' src/grc_agent/` returns matches only in `grc_agent/grc_native_adapter.py`.
6. `git diff --stat` on `flowgraph_session.py` shows net reduction of ≥ 1000 lines (Phase 6 reduces 1596 → ~300).
7. `git grep -n 'compute_graph_id\|hashing' src/grc_agent/` returns zero matches — no deep-JSON-hash function exists.
8. The 4 `tests/test_reliability_hardening.py` inspect-shape tests pass.
9. The 8 `tests/gui/test_inspector_widget.py` tests pass.
10. `docs/MODEL_CONTEXT_BIBLE.md` regenerates clean.
11. The Qt GUI opens `examples/random_bit_generator.grc` and the inspector renders the 5 blocks across the correct categories with no `Advanced` or `Config` parameters in any tree.
12. `flowgraph_session.py` still owns mutation, integrity, and atomic save; it no longer owns YAML parsing, `grcc` subprocess, or dict-crawling inspection.
13. The Pydantic outbound models use `extra="forbid"`; the inbound models use `extra="ignore"`. Both are documented in `domain_models.py`.
14. `session_ops.py` and `_payload.py` are deleted.

---

## 12. Handoff Conventions

When a phase finishes:

1. The implementing agent runs the phase's verification gate.
2. The implementing agent updates `docs/MODEL_CONTEXT_BIBLE.md` if the phase changed tool schemas (run `UPDATE_MODEL_CONTEXT_BIBLE=1 pytest tests/test_model_context_bible.py -v`).
3. The implementing agent commits the changes with the convention from §10.
4. The implementing agent reports back with: files created, files modified, files deleted, test output, the rendered schema (if any), and any deviation from the phase file.

The next phase is dispatched only after the current phase's gate passes and the maintainer (or the subagent driver) approves the handoff.
