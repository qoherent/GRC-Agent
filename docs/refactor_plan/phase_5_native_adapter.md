# Phase 5 — Native Adapter + Mutation Methods (Initial Build)

> **Predecessor:** Phase 4 (domain models shipped; outbound strict, inbound lenient).
> **Successor:** Phase 6 (single cutover: rewrite tools + gut session).
> **Goal:** **Build the complete native adapter from scratch** as the single source of truth for GRC access in the agent. The adapter includes loading, mutation, validation, inspection helpers, AND graph identity. **Do NOT touch `inspect_graph.py`, `change_graph.py`, or `flowgraph_session.py`.** Phase 6 wires everything in a single cutover.

> **Why "build now, with mutations":** per the consultant's architectural review, building the adapter without its mutation methods creates a split-brain state in Phase 6 — the tool handlers would call the adapter for reads but use the legacy `flowgraph_session` for writes, and the two would drift. The adapter must be complete (read + write + validate) **before** any tool handler can cut over to it.

> **Why "no deep-JSON hashing for graph IDs":** per the consultant's perf review, GNU Radio flowgraphs can be massive. Deep-dumping a Pydantic model to JSON and hashing it on every turn is an unnecessary latency bottleneck. The graph identity is the file-bytes SHA-256 (for cross-session identity) plus a per-`FlowGraph`-instance revision counter (for in-session mutation tracking).

---

## 0. Cross-Phase Context

Before starting, read `plan_context.md` in full. Inherit:
- §3 (MVP surface)
- §4 (aggressive redesign rules)
- §5 (verified environment facts) — **especially the `get_platform()` lazy singleton pattern**
- §6.1 (legacy surface — `flowgraph_session.py` is the legacy target; we are NOT touching it this phase)
- §8.1, §8.2, §8.3 (env, load-path, runtime edge cases)
- §10 (commit cadence)

Also re-read:
- The handoff docs from Phases 1, 2, 3, 4.
- `src/grc_agent/domain_models.py` (the Pydantic models the adapter fills).
- `playground/inspect_experiment/verify_native_api.py` (the role classification rules).
- `playground/change_graph_experiment/analysis.md` (the mutation API answers).
- `docs/GNU_NATIVE_METHODS.md` §1 (class dictionary), §2 (evaluation pipeline), §3 (param filtering), §4 (port resolution), §5 (validation).

---

## 1. The Design

### 1.1 What `grc_native_adapter.py` looks like

This is the **initial** creation. By the end of this phase, the file is the **only** file in `src/grc_agent/` that imports from `gnuradio.*`. It exposes:

```python
# === Singleton ===
_PLATFORM: Platform | None

def get_platform() -> Platform: ...

# === Graph identity (no deep-JSON hashing) ===
@dataclass
class GraphIdentity:
    file_sha256: str         # SHA-256 of the raw .grc file bytes (None for unsaved)
    instance_id: int         # id(FlowGraph) — stable for the lifetime of the object
    revision: int            # incremented on every mutation

def new_graph_identity(file_bytes: bytes | None) -> GraphIdentity: ...
def bump_revision(identity: GraphIdentity) -> None: ...

# === Loading ===
def load_flow_graph(file_path: Path) -> FlowGraph: ...    # raw GRC object
def load_and_inspect(file_path: Path) -> GrcFlowgraph: ... # Pydantic snapshot

# === Mutation (the 6 op_types) ===
def add_block(flow_graph: FlowGraph, block_type: str, instance_name: str, parameters: dict) -> Block: ...
def remove_block(flow_graph: FlowGraph, instance_name: str) -> None: ...
def set_param(block: Block, param_key: str, value: str) -> None: ...
def set_block_state(block: Block, state: str) -> None: ...
def connect(flow_graph: FlowGraph, src_block: str, src_port: str, dst_block: str, dst_port: str) -> Connection: ...
def disconnect(flow_graph: FlowGraph, src_block: str, src_port: str, dst_block: str, dst_port: str) -> None: ...
def apply_mutation(flow_graph: FlowGraph, op_type: str, **kwargs) -> None: ...  # dispatcher for the 6 op_types

# === Validation ===
def validate(flow_graph: FlowGraph) -> GrcValidation: ...

# === Inspection helpers ===
def classify_role(block: Block) -> BlockRole: ...
def render_block(block: Block) -> GrcBlock: ...
def render_connection(connection: Connection) -> GrcConnection: ...
def render_parameter(param_key: str, param: Param) -> GrcParameter | None: ...  # None if hidden/filtered
def render_flow_graph(flow_graph: FlowGraph) -> GrcFlowgraph: ...

# === Serialization ===
def serialize_flow_graph(flow_graph: FlowGraph) -> str: ...  # GRC's native YAML serializer
def write_flow_graph_atomic(flow_graph: FlowGraph, path: Path) -> None: ...
```

### 1.2 Graph identity — file bytes + instance id + revision

Per the consultant's perf review, **do not** deep-JSON-hash the Pydantic model on every turn. Instead:

```python
import hashlib
from dataclasses import dataclass

@dataclass
class GraphIdentity:
    file_sha256: str | None    # SHA-256 of the raw .grc file bytes (None if unsaved/empty)
    instance_id: int            # id(flow_graph) — stable for the lifetime of the object
    revision: int               # bumped on every mutation

def new_graph_identity(file_bytes: bytes | None) -> GraphIdentity:
    sha = hashlib.sha256(file_bytes).hexdigest() if file_bytes else None
    return GraphIdentity(file_sha256=sha, instance_id=0, revision=0)  # instance_id set by bind

def bind_to_flow_graph(identity: GraphIdentity, flow_graph: FlowGraph) -> None:
    identity.instance_id = id(flow_graph)

def bump_revision(identity: GraphIdentity) -> None:
    identity.revision += 1
```

**Why this is fast:**
- The file-bytes hash is computed **once** when the file is read (or when `load_flow_graph` is called). It does not re-run on every turn.
- The instance id is `id(flow_graph)` — Python's built-in, O(1).
- The revision is an integer counter — O(1) per mutation.

**When to use which:**
- **Cross-session identity** ("is this the same file the user opened yesterday?"): compare `file_sha256`.
- **In-session change detection** ("has the flowgraph been modified since the last `inspect_graph`?"): compare `revision`.
- **Cache scoping** (e.g., "invalidate the catalog when the user opens a different file"): compare `file_sha256`.

The legacy `flowgraph_session.graph_id()` was a SHA-256 of the raw YAML. The new identity is the same SHA-256 (cross-session identity) PLUS a per-`FlowGraph` revision counter (in-session change detection). Both are O(1) on every turn.

**Do NOT** add a `compute_graph_id_from_pydantic` deep-JSON-hash function. The consultant's review specifically rejected this.

### 1.3 The visibility filter (one uniform rule, hard-coded)

```python
EXCLUDED_CATEGORIES: frozenset[str] = frozenset({ADVANCED_PARAM_TAB, "Config"})

def render_parameter(param_key: str, param: Param) -> GrcParameter | None:
    """One uniform visibility filter. Returns None if the param should be hidden."""
    if param.hide == "all":
        return None
    if param.category in EXCLUDED_CATEGORIES:
        return None
    return GrcParameter(
        name=param_key,
        dtype=str(param.dtype),
        value=str(param.value),
        evaluated_value=_safe_evaluate(param),
        category=str(param.category),
        hide=str(param.hide),
    )
```

**No per-block allowlist. No per-param regex. No exceptions.** The native `param.hide` and `param.category` are the single source of truth. Per `docs/GNU_NATIVE_METHODS.md` §3, this drops only generic GRC metadata and 100%-styling `Config` parameters.

The options block is a special case: `classify_role` returns `BlockRole.OPTIONS` for it, and the renderer keeps **all** options parameters regardless of category. This is the one place where the uniform rule is intentionally relaxed, because the options block is the user-facing metadata (title, author, description) that the model needs to see.

### 1.4 The role classification (one uniform rule, hard-coded)

```python
def classify_role(block: Block) -> BlockRole:
    if block.is_variable:
        return BlockRole.VARIABLE
    if block.is_import:
        return BlockRole.IMPORT
    if block.is_snippet:
        return BlockRole.SNIPPET
    if block.is_virtual_or_pad:
        return BlockRole.VIRTUAL_OR_PAD
    if block.key == "options":
        return BlockRole.OPTIONS
    has_out = len(block.active_sources) > 0
    has_in = len(block.active_sinks) > 0
    if has_out and not has_in: return BlockRole.SOURCE
    if has_in and not has_out: return BlockRole.SINK
    if has_in and has_out:    return BlockRole.TRANSFORM
    return BlockRole.OTHER
```

### 1.5 The error path

```python
def load_and_inspect(file_path: Path) -> GrcFlowgraph:
    try:
        raw_text = file_path.read_text(encoding="utf-8-sig")
    except (IsADirectoryError, FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
        return _err_graph(file_path, code="FILE_READ_ERROR", message=str(e))
    try:
        platform = get_platform()
        flow_graph = platform.make_flow_graph()
        flow_graph.grc_file_path = str(file_path.resolve())
        raw_data = platform.parse_flow_graph(str(file_path))
        flow_graph.import_data(raw_data)
    except Exception as e:
        return _err_graph(file_path, code="YAML_PARSE_ERROR", message=str(e))
    try:
        flow_graph.rewrite()
    except Exception as e:
        return _err_graph(file_path, code="REWRITE_FAILED", message=str(e))
    try:
        flow_graph.validate()
    except Exception as e:
        return _err_graph(file_graph, code="VALIDATION_THREW", message=str(e))
    return render_flow_graph(flow_graph)
```

`_err_graph` constructs a `GrcFlowgraph(ok=False, graph_name=file_path.stem, errors=[{"code": code, "message": message}], validation=GrcValidation(status="unknown"))`. The model gets a structured error, not a stack trace.

### 1.6 The mutation helpers (the 6 op_types)

Per the consultant's review, the mutation helpers must be in the adapter from day one — Phase 6 wires them into the tool handlers in a single cutover. The helpers are the **only** code that mutates a `FlowGraph` object.

```python
def add_block(flow_graph: FlowGraph, block_type: str, instance_name: str, parameters: dict) -> Block:
    block = flow_graph.new_block(block_type)
    block.name = instance_name
    for k, v in parameters.items():
        if k in block.params:
            block.params[k].set_value(str(v))
    return block

def remove_block(flow_graph: FlowGraph, instance_name: str) -> None:
    # GRC has no public remove_block; mutate the internal list.
    # Per Phase 2 experiment's analysis.md.
    for i, b in enumerate(flow_graph.blocks):
        if b.name == instance_name:
            del flow_graph.blocks[i]
            return
    raise KeyError(f"Block {instance_name!r} not found")

def set_param(block: Block, param_key: str, value: str) -> None:
    if param_key not in block.params:
        raise KeyError(f"Param {param_key!r} not in block {block.name!r}")
    block.params[param_key].set_value(str(value))

def set_block_state(block: Block, state: str) -> None:
    if state not in {"enabled", "disabled", "bypassed"}:
        raise ValueError(f"Invalid state {state!r}; must be enabled/disabled/bypassed")
    block.state = state

def connect(flow_graph: FlowGraph, src_block: str, src_port: str, dst_block: str, dst_port: str) -> Connection:
    src = _find_port(flow_graph, src_block, src_port, kind="source")
    dst = _find_port(flow_graph, dst_block, dst_port, kind="sink")
    return flow_graph.connect(src, dst)

def disconnect(flow_graph: FlowGraph, src_block: str, src_port: str, dst_block: str, dst_port: str) -> None:
    src = _find_port(flow_graph, src_block, src_port, kind="source")
    dst = _find_port(flow_graph, dst_block, dst_port, kind="sink")
    flow_graph.disconnect(src, dst)

def apply_mutation(flow_graph: FlowGraph, op_type: str, **kwargs) -> None:
    """Dispatcher for the 6 mutation op_types. Raises on invalid op_type."""
    if op_type == "add_block":
        add_block(flow_graph, **kwargs)
    elif op_type == "remove_block":
        remove_block(flow_graph, **kwargs)
    elif op_type == "update_params":
        block = _find_block(flow_graph, kwargs.pop("instance_name"))
        for k, v in kwargs.pop("params").items():
            set_param(block, k, v)
    elif op_type == "update_states":
        block = _find_block(flow_graph, kwargs.pop("instance_name"))
        set_block_state(block, kwargs.pop("state"))
    elif op_type == "add_connection":
        connect(flow_graph, **kwargs)
    elif op_type == "remove_connection":
        disconnect(flow_graph, **kwargs)
    else:
        raise ValueError(f"Unknown op_type: {op_type!r}")
```

After every `apply_mutation`, the caller must call `flow_graph.rewrite()` to refresh the namespace and propagate wildcard types. The adapter provides a `validate_and_finalize(flow_graph)` helper that does both.

```python
def validate_and_finalize(flow_graph: FlowGraph) -> GrcValidation:
    """One call to use after a batch of mutations. Returns the validation result."""
    flow_graph.rewrite()
    flow_graph.validate()
    return GrcValidation(
        status="valid" if flow_graph.is_valid() else "invalid",
        errors=[str(msg) for _elem, msg in flow_graph.iter_error_messages()],
        native_ok=flow_graph.is_valid(),
    )
```

### 1.7 The serialization helper

```python
def serialize_flow_graph(flow_graph: FlowGraph) -> str:
    """Return the GRC-native YAML representation of the flow graph.

    Uses grc.core.io.yaml.GRCDumper. The output is suitable for writing
    to a .grc file. The output is NOT necessarily byte-identical to the
    input; GRC normalizes formatting.
    """
    from gnuradio.grc.core.io import yaml as _grc_yaml
    return _grc_yaml.dump(flow_graph.export_data())
```

**Import order matters:** the `from gnuradio.grc.core.io import yaml` import is inside the function, not at module top-level, because `io/yaml` triggers a circular import if loaded before the platform is warmed. The `get_platform()` call earlier in the call sequence has already warmed it.

---

## 2. Step-by-Step

### 2.1 Module creation (Day 1)

- [ ] **Step 1:** Create `src/grc_agent/grc_native_adapter.py` with the public API from §1.1, organized by section header comments (singleton, graph identity, loading, mutation, validation, inspection helpers, serialization).
- [ ] **Step 2:** Implement the lazy `get_platform()` from `plan_context.md` §5.
- [ ] **Step 3:** Implement `GraphIdentity` + `new_graph_identity` + `bind_to_flow_graph` + `bump_revision` per §1.2. **No deep-JSON-hashing function exists.**
- [ ] **Step 4:** Implement `load_and_inspect` with the error-path from §1.5.
- [ ] **Step 5:** Implement the 6 mutation helpers + `apply_mutation` + `validate_and_finalize` per §1.6.
- [ ] **Step 6:** Implement the inspection helpers (classify_role, render_block, render_connection, render_parameter, render_flow_graph) per §1.3 and §1.4.
- [ ] **Step 7:** Implement `serialize_flow_graph` and `write_flow_graph_atomic` per §1.7.
- [ ] **Step 8:** Add a module docstring that references `plan_context.md` §5 (env facts) and §4 (no in-band control flow). Quote the rule.

### 2.2 Tests (Days 2–3)

- [ ] **Step 9:** Create `tests/test_grc_native_adapter.py` (all tests marked `@pytest.mark.grc_native`):
  - `test_get_platform_returns_singleton` — call `get_platform()` twice, assert the same instance.
  - `test_get_platform_missing_grc_raises_runtime_error` — patch `sys.modules` to remove `gnuradio`; assert `RuntimeError` with the apt-install message.
  - `test_load_and_inspect_random_bit_generator` — full lifecycle on a real fixture; assert all 5 blocks present, no `Advanced`/`Config` parameters, all connections present.
  - `test_load_and_inspect_blank` — empty flowgraph; assert `ok=True`, `blocks=[]`, `connections=[]`.
  - `test_load_and_inspect_broken_grc` — `r2_broken_fixer.grc`; assert `ok=False`, `errors[0].code` is one of the structured codes.
  - `test_load_and_inspect_nonexistent_file` — assert `ok=False`, `errors[0].code == "FILE_READ_ERROR"`.
  - `test_load_and_inspect_directory` — assert `ok=False`, `errors[0].code == "FILE_READ_ERROR"`.
  - `test_classify_role_options` — `flow_graph.options_block` returns `BlockRole.OPTIONS`.
  - `test_classify_role_variable` — a variable block returns `BlockRole.VARIABLE`.
  - `test_classify_role_source_sink_transform` — a 3-block test fixture with known roles.
  - `test_render_parameter_filters_advanced` — a parameter with `category == "Advanced"` returns `None`.
  - `test_render_parameter_filters_config` — a parameter with `category == "Config"` returns `None`.
  - `test_render_parameter_filters_hide_all` — a parameter with `hide == "all"` returns `None`.
  - `test_validate_valid_flowgraph` — `validate(flow_graph)` returns `GrcValidation(status="valid", errors=[])`.
  - `test_serialize_flow_graph_round_trip` — load a fixture, serialize, parse the YAML back, assert the same `GrcFlowgraph` model.
  - `test_graph_identity_file_bytes` — `new_graph_identity(b"hello")` returns the expected SHA-256.
  - `test_graph_identity_no_bytes` — `new_graph_identity(None)` returns `file_sha256=None`.
  - `test_graph_identity_revision_bumps` — `bump_revision(identity)` increments `revision` by 1.
  - `test_graph_identity_no_deep_json_hash` — sanity check: there is no function in the adapter named `compute_graph_id_from_pydantic` or similar. The grep command `rg 'compute_graph_id|hashing|hashlib' src/grc_agent/grc_native_adapter.py` shows only the file-bytes hash use.
  - `test_add_block_mutation` — `add_block(fg, "analog_sig_source_x", "src", {"frequency": "1000"})` adds a block named "src".
  - `test_remove_block_mutation` — `remove_block(fg, "src")` removes it.
  - `test_set_param_mutation` — `set_param(block, "frequency", "1000")` updates the param.
  - `test_set_block_state_mutation` — `set_block_state(block, "disabled")` updates the state.
  - `test_connect_mutation` — `connect(fg, "src", "0", "dst", "0")` adds a connection.
  - `test_disconnect_mutation` — `disconnect(fg, "src", "0", "dst", "0")` removes it.
  - `test_apply_mutation_dispatcher` — `apply_mutation(fg, "add_block", ...)` calls `add_block`.
  - `test_apply_mutation_invalid_op_type` — `apply_mutation(fg, "bad_op")` raises `ValueError`.
  - `test_validate_and_finalize_after_mutation` — `validate_and_finalize(fg)` returns `GrcValidation` and triggers `rewrite()` + `validate()`.
- [ ] **Step 10:** Run `pytest -m grc_native tests/test_grc_native_adapter.py -v`. All 25+ tests pass.

### 2.3 Adapter hygiene (Day 3)

- [ ] **Step 11:** `rg -n 'gnuradio' src/grc_agent/`. Assert that matches appear **only** in `src/grc_agent/grc_native_adapter.py`. If any other file matches, that file is leaking a direct import. Fix.
- [ ] **Step 12:** `rg -n 'subprocess' src/grc_agent/`. Assert no matches in `grc_native_adapter.py` (the adapter is in-process; the `grcc` subprocess path is still in `flowgraph_session.py`, which Phase 6 removes).
- [ ] **Step 13:** `rg -n 'yaml\.safe_load\|yaml\.safe_dump' src/grc_agent/`. Assert no matches in `grc_native_adapter.py` (it should use the GRC-native serializer, not PyYAML).
- [ ] **Step 14:** `rg -n 'compute_graph_id\|hashing' src/grc_agent/grc_native_adapter.py`. Assert no matches (the consultant rejected deep-JSON hashing; only the file-bytes hash is present).

### 2.4 Verify source tree isolation (Day 4)

- [ ] **Step 15:** `git diff --stat src/grc_agent/runtime/`. Zero matches. The tool handlers are **not** touched in this phase. Phase 6 wires them.
- [ ] **Step 16:** `git diff --stat src/grc_agent/flowgraph_session.py`. Zero matches. The legacy session is **not** touched in this phase. Phase 6 guts it.
- [ ] **Step 17:** `pytest -m "not grc_native and not gui and not llama_eval" -x`. All pass. The legacy `inspect_graph`, `change_graph`, and `flowgraph_session` are unchanged. The new adapter is added but not yet wired in.

### 2.5 Doc regen (Day 4)

- [ ] **Step 18:** `UPDATE_MODEL_CONTEXT_BIBLE=1 pytest tests/test_model_context_bible.py -v`. Inspect the diff. The model schemas may not have changed (the wire payload shape is the same), but verify.
- [ ] **Step 19:** Commit `feat(phase-5/adapter): add complete native adapter (load + mutate + validate + identity)`. Include the new file, the new tests, and the model-context-bible regeneration.

---

## 3. Files to Touch

### 3.1 Creates

- `src/grc_agent/grc_native_adapter.py` (the complete adapter, ~500 lines)
- `tests/test_grc_native_adapter.py` (25+ tests)

### 3.2 Modifies

- `docs/MODEL_CONTEXT_BIBLE.md` (regenerated)

### 3.3 Deletes

Nothing.

### 3.4 Untouched (Phase 6's job)

- `src/grc_agent/runtime/inspect_graph.py`
- `src/grc_agent/runtime/change_graph.py`
- `src/grc_agent/flowgraph_session.py`
- `src/grc_agent/transaction.py`
- `src/grc_agent/agent.py`
- `src/grc_agent_gui/inspector.py`
- `tests/gui/test_inspector_widget.py`
- All other source files

---

## 4. Verification Gate

The phase is done when **all** of the following hold:

- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes.
- [ ] `pytest -m grc_native` passes on the dev box.
- [ ] `pytest tests/test_grc_native_adapter.py -v` has 25+ test cases, all pass.
- [ ] `rg -n 'gnuradio' src/grc_agent/` returns matches **only** in `src/grc_agent/grc_native_adapter.py`.
- [ ] `rg -n 'yaml\.safe_load\|yaml\.safe_dump' src/grc_agent/grc_native_adapter.py` returns zero matches.
- [ ] `rg -n 'compute_graph_id\|hashing' src/grc_agent/grc_native_adapter.py` returns no matches (no deep-JSON hashing).
- [ ] `classify_role(block)` returns one of the 9 `BlockRole` members for every block in `random_bit_generator.grc`.
- [ ] `render_parameter(key, param)` returns `None` for every `Advanced` or `Config` param in `random_bit_generator.grc`.
- [ ] `serialize_flow_graph(flow_graph)` produces a valid YAML string that round-trips through `load_and_inspect_from_yaml`.
- [ ] `add_block`, `remove_block`, `set_param`, `set_block_state`, `connect`, `disconnect` all work end-to-end on a real `FlowGraph` object.
- [ ] `apply_mutation(flow_graph, op_type, **kwargs)` dispatches to the correct helper for each of the 6 op_types.
- [ ] `validate_and_finalize(flow_graph)` runs `rewrite()` + `validate()` and returns a `GrcValidation`.
- [ ] `git diff --stat src/grc_agent/runtime/` returns zero matches. Tool handlers untouched.
- [ ] `git diff --stat src/grc_agent/flowgraph_session.py` returns zero matches. Legacy session untouched.
- [ ] `docs/MODEL_CONTEXT_BIBLE.md` regenerates cleanly.

---

## 5. Edge Cases Specific to This Phase

| Edge case | Symptom | Mitigation |
|---|---|---|
| The Phase 1 experiment's `is_gui_or_style_param` regex filter caught a parameter the category filter misses | The category filter is "less aggressive" than the regex | Document the gap. Phase 5 uses the category filter only. The maintainer can decide in a follow-up whether to add a separate cosmetic filter. **Per the maintainer's rule, no per-param regex.** |
| `flow_graph.new_block(block_type)` raises because the block is not in the catalog | The mutation helper crashes | Catch the exception, return `None` from `add_block`. The caller (the mutation pipeline in Phase 6) handles the `None` return as a structured error. |
| `block.name = instance_name` raises because the name collides | The mutation helper crashes | Catch `ValueError`; return `None`. The caller surfaces the collision. |
| GRC has no public `remove_block` method | `remove_block` requires a workaround (mutate `flow_graph.blocks` directly) | The Phase 2 experiment's `analysis.md` documents the actual API. Phase 5's `remove_block` uses whatever the experiment found. If the experiment's answer is "mutate `flow_graph.blocks` directly," that's what Phase 5 does (with a comment citing `analysis.md`). |
| GRC's `state` property is read-only on some versions | `set_block_state` requires a workaround | Document. The Phase 2 experiment determines the actual API. |
| The native `serialize_flow_graph` output is not byte-identical to the input `.grc` (e.g., comments stripped, key order changed) | A `git diff` after a save looks like a huge change | This is GRC's normal behavior. Document it. The integrity check uses the file-bytes SHA-256, not the YAML canonical form. |
| The `from gnuradio.grc.core.io import yaml` import inside `serialize_flow_graph` raises the circular import error | The function crashes | The function is only called after `get_platform()` has been called (which warms the `params` module). The circular import only triggers if `serialize_flow_graph` is the **first** call to GRC. Document this ordering. |
| Two `FlowGraph` objects are created in quick succession and `id()` collides (Python recycles ids after gc) | `GraphIdentity.instance_id` is not unique | Bind the identity at the moment the `FlowGraph` is created, not at object construction. Use `weakref.ref(flow_graph)` if Python's `id` is unreliable. The test `test_graph_identity_instance_id_unique` verifies. |
| A mutation creates a block but `flow_graph.rewrite()` is never called | The namespace is stale; subsequent `validate()` returns wrong results | The mutation helpers do **not** call `rewrite()` automatically. The caller is responsible for calling `validate_and_finalize(flow_graph)` after a batch. The test `test_validate_and_finalize_after_mutation` verifies the contract. |

---

## 6. Handoff

When this phase finishes:

1. The implementing agent commits with the convention `feat(phase-5/adapter): add complete native adapter (load + mutate + validate + identity)`.
2. The implementing agent writes `docs/refactor_plan/phase_5_handoff.md` with:
   - The full public API of `grc_native_adapter.py` (the list from §1.1)
   - The test count and pass rate
   - The `rg 'gnuradio' src/grc_agent/` output (showing only the adapter matches)
   - The `rg 'compute_graph_id|hashing' src/grc_native_adapter.py` output (showing no deep-JSON-hash function exists)
   - Any decision the maintainer made about a non-obvious API gap (e.g., `remove_block` workaround)
   - Confirmation that `git diff --stat src/grc_agent/runtime/` and `git diff --stat src/grc_agent/flowgraph_session.py` are both empty
3. The next phase is Phase 6 (single cutover: rewrite `inspect_graph` + `change_graph` + gut `flowgraph_session.py` together, all cutting over to the adapter cleanly at the same time).
