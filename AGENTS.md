## Role & Tone

- **Role:** Senior Systems Engineer / GNU Radio tool-use architect.
- **Tone:** Direct, data-driven, zero fluff.
- **Anti-Symptom Rule:** Reject speculative features or architectural shifts without hard empirical evidence.
- **No Assumptions:** Stop and ask when a critical architecture or dependency decision is required.

---

## Architecture (Post Phase-7 Refactor)

### Core modules

| Module | Lines | Role |
|--------|-------|------|
| `grc_native_adapter.py` | ~470 | **THE BRIDGE** — all GRC native API calls. Lazy singleton platform, load/inspect/mutate/validate/serialize. Never import `gnuradio` at module top-level. |
| `domain_models.py` | ~182 | Pydantic V2 schemas. Outbound (`extra="forbid"`) for LLM-facing output. Inbound (`extra="ignore"`) for tool args. |
| `flowgraph_session.py` | ~447 | Owns path, integrity, atomic save, revision, and 6 mutation helpers. `flowgraph` attribute is a live `gnuradio.grc.core.FlowGraph.FlowGraph`. |
| `runtime/param_filter.py` | — | **THE BIBLE** — single source of truth for param visibility. `keep_param` predicate: drop `hide==all` / `category∈{Advanced,Config}` / `dtype==gui_hint`. One uniform rule. |
| `runtime/inspect_graph.py` | ~248 | Adapter-backed. Returns `GrcFlowgraph` Pydantic model. |
| `runtime/change_graph.py` | ~370 | Flat batch mutation dispatch. Stale-revision gate, noop detection, autosave, duplicate check. |
| `_payload.py` | ~70 | ErrorCode constants (24 values), `build_error_payload()`, thin Block/Connection stubs. |
| `session_ops.py` | ~185 | `connection_id()`, `parse_connection_id()`, validation pipeline helpers. |
| `agent.py` | ~1964 | Tool registry, dispatch, lifecycle. Inline rewire resolvers. |
| `transaction.py` | ~468 | Clone/commit via `export_data()`/`import_data()`. `apply_edit` / `propose_edit`. |

### Data flow

```
GRC .grc file
  → grc_native_adapter.load_flow_graph()     # Parse → import_data() → rewrite()
  → FlowgraphSession.flowgraph                # Live native FlowGraph object
  → render_flow_graph()                       # → GrcFlowgraph Pydantic model
  → inspect_graph / change_graph dispatch      # Tool result
  → Model receives flat typed payload
```

### Mutation path

```
change_graph tool call
  → dispatch_flat_change_graph_batch()
    → integrity check (stale revision guard)
    → adapter.apply_mutation() per op
    → validate_and_finalize() (native is_valid())
    → rollback on failure / commit on success
    → autosave if file changed (serialized snapshot comparison)
```

---

## Engineering Rules (for AI coding agents)

### General rules — never ad-hoc
- **No hand-picked heuristics.** No per-field allowlists, per-scenario branches, regex routing, or prompt folklore. If logic is needed, it is one uniform rule applied to every case.
- **Prefer native methods.** Use GNU Radio GRC's Python API (`gnuradio.grc.core`) — `param.hide`, `param.category`, `Block.is_variable`, `flow_graph.is_valid()`, etc.
- **Fix at the source.** Correctness lives in the tool/handler that produces data, not in a post-processor.
- **No silent transformation.** Any truncation, filtering, or omission in model-facing output must be explicitly flagged.
- **Simplify by removal.** Prefer deleting code over adding it.

### Verification standard
- **A green test is necessary, not sufficient.** Inspect actual data flow.
- **Evidence before assertions.** Every claim cites a verified observation, never intent.

---

## Prompt & Tool Surface Architecture

### Three model-facing tools
| Tool | Direction | Backed by |
|------|-----------|-----------|
| `inspect_graph` | read | `grc_native_adapter.render_flow_graph()` → `GrcFlowgraph` |
| `change_graph` | write | `grc_native_adapter.apply_mutation()` + `validate_and_finalize()` |
| `query_knowledge` | read | `search_blocks` (catalog YAML) + `ask_grc_docs` (RAG) — no native API |

- No new model-facing tool, schema field, or system-prompt change without maintainer authorization.
- No speculative expansion without live eval-harness evidence.
- Tool schemas describe **capability** — what a function does, not when or how to use it.
- No in-band control flow: no ALL-CAPS directives, behavioral commands, or procedural recipes in model-visible strings.

---

## Runtime & State Management

- **Manual execution loop:** `ToolAgentsRunner._run_turn_events` with bounded `.step()`.
- **No result caching.** Every call hits the live backend fresh.
- **Repeat-payload escalator:** `_last_failed_ops_hash` flags duplicate failing payloads.
- **Context compaction:** one-pass proportional slicing with truncation flags.
- **Wire-format role safety:** runtime directives injected as `user`-role only.

---

## Constraints (hard prohibitions)

- **No daemon management.** Never manage OS services/daemons.
- **No hardware polling.** No `psutil`, `nvidia-smi`, or telemetry.
- **Non-blocking flow.** Launch into degraded mode if backend unreachable; never `sys.exit()`.
- **No backward compatibility.** No shims, dual-format persistence, or legacy synthesis layers.
- **No application-flow changes without permission.**
- **No `gnuradio` imports outside `grc_native_adapter.py` and auxiliary files (doctor, dogfood, session catalog paths).**

---

## Key Conventions

### Param visibility
- Single source of truth: `runtime/param_filter.py` `keep_param()` predicate.
- Drop: `hide == "all"`, `category ∈ {Advanced, Config}`, `dtype == "gui_hint"`.
- Keep: `enum` OR `value != default` OR `references_variable`.

### State values
- Valid: `enabled`, `disabled`, `bypassed` (accept `bypass` as alias).
- Enforced at `_update_state_operation()` in change_graph.py.

### Disconnect precision
- Native `flow_graph.disconnect(src, dst)` removes ALL edges from source port.
- Adapter `disconnect()` finds exact `Connection` object and drops from set.

### Graph identity
- File-bytes SHA-256 (cross-session) + `state_revision` counter (in-session).
- No deep-JSON hashing.

### Atomic save
- `_atomic_write_text()`: temp file → fsync → `os.replace()` → directory fsync.
- Lock via `fcntl.flock` on `.grc_agent/<name>.lock`.
- Backup saved before each save.

---

## File Map (Current State)

| File | Lines | Status |
|------|-------|--------|
| `grc_native_adapter.py` | ~470 | Live — all GRC native API |
| `domain_models.py` | ~182 | Live — Pydantic V2 schemas |
| `flowgraph_session.py` | ~447 | Live — path, integrity, save, revision |
| `_payload.py` | ~70 | Live — ErrorCode, error envelope, thin stubs |
| `session_ops.py` | ~185 | Live — validation helpers, connection parsing |
| `agent.py` | ~1964 | Live — tool registry + dispatch |
| `runtime/param_filter.py` | — | Live — the Bible |
| `runtime/inspect_graph.py` | ~248 | Live — adapter-backed |
| `runtime/change_graph.py` | ~370 | Live — flat batch dispatch |

---

## Test Gate

| Marker | Count | Command |
|--------|-------|---------|
| (default) | 390 passed, 10 skipped | `pytest` |
| `grc_native` | 28 passed | `pytest -m grc_native` |
| `gui` | 6 passed | `xvfb-run pytest -m gui` |
| collection | 400 total | `pytest --collect-only` |

### Default CI command
```bash
pytest -m "not grc_native and not gui and not llama_eval"
```

---

## Definition of Done

1. ✅ `pytest` passes (390/400)
2. ✅ `pytest -m grc_native` passes (28/28)
3. ✅ `pytest -m gui` passes (6/6)
4. ✅ No `yaml.safe_load` / `grcc` subprocess in `src/grc_agent/`
5. ✅ `gnuradio` imports only in adapter + auxiliary files
6. ✅ `flowgraph_session.py` reduced 1596 → 447 (-1149 lines)
7. ✅ No deep-JSON-hash function
8. ✅ `param_filter.py` is single source of truth — no per-block allowlists
9. ✅ Pydantic `extra="forbid"` outbound, `extra="ignore"` inbound
10. ✅ GUI inspector reads new flat shape, no `_block_params` sidecar
