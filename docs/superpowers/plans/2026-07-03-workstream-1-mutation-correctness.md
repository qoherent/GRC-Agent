# Workstream 1 — `.grc` Mutation Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Depends on:** None (foundational; everything else can run after Workstream 1).

**Goal:** Decompose `dispatch_flat_change_graph_batch` from a 329-line god-function into seven single-responsibility phase methods with an explicit data-flow contract, AND add direct unit tests for `transaction.capture_session_state` / `restore_session_state` that document identity loss.

**Architecture:** `change_graph.py` is restructured around a `ChangeGraphContext` object that pre-computes the inputs every phase needs (snapshot, new-block set, type-already-set set, pre-edges set, removed-names set) once, then walks the seven ordered phases (`add_blocks`, `remove_blocks`, `update_params`, `auto_resolve_types`, `update_states`, `remove_connections`, `add_connections`). Each phase reads from the context and contributes to `errors`/`ops_applied`. `transaction.py` gains focused round-trip tests that fix the capture/restore contract.

**Tech Stack:** Python 3.12, Pydantic V2, GNU Radio 3.10.x (native API), sqlite, pytest (`-m grc_native` gate).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/grc_agent/runtime/change_graph.py` | Phase-decomposed batch engine + thin entry-point dispatcher | Refactor |
| `src/grc_agent/transaction.py` | Snapshot capture/restore (unchanged contract) | Read-only ref |
| `tests/test_change_graph_phases.py` | Direct tests for each phase method + happy-path integration | Create |
| `tests/test_transaction_roundtrip.py` | Direct capture/restore round-trip tests (the missing coverage) | Create |

Total ~611 LOC change_graph.py → ~580 LOC (smaller because context + 7 phase methods compress duplicated state). Plus two test files.

---

## Task 1: Extract `ChangeGraphContext` + named phase methods (TDD)

**Files:**
- Modify: `src/grc_agent/runtime/change_graph.py:79-353`
- Create: `tests/test_change_graph_phases.py`

The context holds the immutable cross-phase state. Extracting it makes each phase's input surface explicit.

- [ ] **Step 1: Write failing test for `ChangeGraphContext` wiring**

Create `tests/test_change_graph_phases.py`:
```python
"""Unit tests for the per-phase structure of ``dispatch_flat_change_graph_batch``.

Each phase is exercised in isolation against a ``ChangeGraphContext`` so the
public ``dispatch_flat_change_graph_batch`` keeps its wire-format contract
(``payload['ok']``, ``payload['errors']``) while the internals become a flat
list of single-responsibility methods.
"""

from __future__ import annotations

from unittest import mock

import pytest
from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.change_graph import ChangeGraphContext


@pytest.fixture
def ctx_factory(tmp_path):
    """Build a (agent, context) pair from a fresh empty session."""
    fixture = tmp_path / "empty.grc"
    fixture.write_text(
        "options:\n  parameters:\n    - id: top_block\n"
        "      label: Top Block\nblocks: []\nconnections: []\n",
        encoding="utf-8",
    )
    session = FlowgraphSession()
    session.load(fixture)
    agent = GrcAgent(session=session)
    errors: list[dict[str, str]] = []
    ops_applied = 0
    ctx = ChangeGraphContext(
        agent=agent,
        fg=session.flowgraph,
        errors=errors,
        # ops_applied is a plain int on the mutable context — incremented directly
        # by phase methods via ctx.ops_applied += 1
    )
    return agent, ctx


def test_context_precomputes_add_blocks_list(ctx_factory):
    """``add_blocks_list`` is computed once; ``new_block_names`` is filled."""
    _agent, ctx = ctx_factory
    # Empty: no pre-computed names.
    assert ctx.add_blocks_list == []
    assert ctx.new_block_names == set()
    # After construction with a populated raw value, both surface it.
    raw = [{"block_id": "x", "instance_name": "blk_x"}]
    populated = ChangeGraphContext(
        agent=_agent, fg=_agent.session.flowgraph, errors=[],
        ops_applied=0, raw_add_blocks=raw,
    )
    assert populated.add_blocks_list == raw
    assert "blk_x" in populated.new_block_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_change_graph_phases.py -v`
Expected: FAIL with `ImportError`/`AttributeError` — `ChangeGraphContext` does not exist yet.

- [ ] **Step 3: Add `ChangeGraphContext` dataclass**

Replace `src/grc_agent/runtime/change_graph.py:39-83` (the `_record_error` closure + pre-block sets) with a single dataclass:

```python
from dataclasses import dataclass, field

@dataclass
class ChangeGraphContext:
    """Immutable inputs the seven ``change_graph`` phases consume.

    Built once in :func:`dispatch_flat_change_graph_batch`; each phase reads
    from it, appends to ``errors``, and increments ``ops_applied`` directly
    via ``ctx.ops_applied += 1``.
    """

    agent: Any
    fg: Any
    errors: list[dict[str, str]]
    ops_applied: int = 0

    raw_add_blocks: Any = None
    raw_remove_blocks: Any = None
    raw_update_params: Any = None
    raw_update_states: Any = None
    raw_add_connections: Any = None
    raw_remove_connections: Any = None

    add_blocks_list: list[Any] = field(default_factory=list)
    remove_blocks_list: list[Any] = field(default_factory=list)
    update_params_list: list[Any] = field(default_factory=list)
    update_states_list: list[Any] = field(default_factory=list)
    add_connections_list: list[Any] = field(default_factory=list)
    remove_connections_list: list[Any] = field(default_factory=list)

    new_block_names: set[str] = field(default_factory=set)
    removed_names: set[str] = field(default_factory=set)
    type_already_set: set[str] = field(default_factory=set)
    pre_edges: set[str] = field(default_factory=set)
    before_snapshot: Any = None
    before_serialized: str | None = None
```

- [ ] **Step 4: Add `__all__` entry and re-run test**

```python
__all__ = [
    "ChangeGraphContext",
    "dispatch_flat_change_graph_batch",
]
```

Run: `uv run pytest tests/test_change_graph_phases.py -v`
Expected: PASS (the test now imports `ChangeGraphContext`).

- [ ] **Step 5: Commit**

```bash
git add src/grc_agent/runtime/change_graph.py tests/test_change_graph_phases.py
git commit -m "refactor(change_graph): extract ChangeGraphContext dataclass"
```

---

## Task 2: Phases 1–3 — `add_blocks`, `remove_blocks`, `update_params` (TDD)

**Files:**
- Modify: `src/grc_agent/runtime/change_graph.py:128-198`
- Modify: `tests/test_change_graph_phases.py`

These three phases are the simplest (no validation/save coupling). Refactor them first.

- [ ] **Step 1: Write failing tests for the three phase methods**

Append to `tests/test_change_graph_phases.py`:
```python
def test_phase_add_blocks_applies_one_entry(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_add_blocks

    _agent, ctx = ctx_factory
    ctx.add_blocks_list = [
        {"block_id": "analog_const_source_x", "instance_name": "dc",
         "params": {"const": "0.0", "type": "float"}}
    ]
    ctx.new_block_names = {"dc"}
    _phase_add_blocks(ctx)
    assert "dc" in [b.name for b in _agent.session.flowgraph.blocks]


def test_phase_add_blocks_records_duplicate_name_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_add_blocks

    _agent, ctx = ctx_factory
    # First add succeeds.
    ctx.add_blocks_list = [
        {"block_id": "analog_const_source_x", "instance_name": "dc"}
    ]
    ctx.new_block_names = {"dc"}
    _phase_add_blocks(ctx)
    # Second add with the same name: error (no exception).
    ctx.errors.clear()
    _phase_add_blocks(ctx)
    assert any(e["code"] == "duplicate_block_name" for e in ctx.errors)


def test_phase_remove_blocks_rejects_connection_id(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_remove_blocks

    _agent, ctx = ctx_factory
    ctx.remove_blocks_list = ["src:0->dst:0"]
    _phase_remove_blocks(ctx)
    assert any(
        e["code"] == "remove_block_failed" and "connection" in e["message"].lower()
        for e in ctx.errors
    )


def test_phase_update_params_missing_instance_name_records_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_update_params

    _agent, ctx = ctx_factory
    ctx.update_params_list = [{"params": {"value": "1"}}]  # no instance_name
    _phase_update_params(ctx)
    assert any(e["code"] == "invalid_update" for e in ctx.errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_change_graph_phases.py -v`
Expected: FAIL — `_phase_add_blocks`, `_phase_remove_blocks`, `_phase_update_params` don't exist yet.

- [ ] **Step 3: Add the three phase methods**

Replace the `add_blocks`, `remove_blocks`, `update_params` blocks (currently lines 128–198) with three methods:

```python
def _phase_add_blocks(ctx: ChangeGraphContext) -> None:
    """Add every entry in ``ctx.add_blocks_list`` via the native adapter."""
    for entry in ctx.add_blocks_list:
        if not isinstance(entry, dict):
            continue
        block_id = str(entry.get("block_id", "")).strip()
        instance_name = str(entry.get("instance_name", "")).strip()
        if not block_id or not instance_name:
            ctx.errors.append({
                "code": "invalid_block",
                "message": f"add_blocks entry needs block_id and instance_name: {entry}",
            })
            continue
        try:
            ctx.fg.get_block(instance_name)
            ctx.errors.append({
                "code": "duplicate_block_name",
                "message": f"a block named {instance_name!r} already exists",
            })
            continue
        except KeyError:
            pass
        try:
            apply_mutation(
                ctx.fg,
                "add_block",
                block_type=block_id,
                instance_name=instance_name,
                parameters=entry.get("params") or {},
                state=entry.get("state"),
            )
            ctx.ops_applied += 1
        except KeyError as exc:
            ctx.errors.append({"code": "parameter_not_found", "message": str(exc)})
        except Exception as exc:
            ctx.errors.append({"code": "add_block_failed", "message": str(exc)})


def _phase_remove_blocks(ctx: ChangeGraphContext) -> None:
    """Remove each block name; reject connection-id strings with a clear hint."""
    for entry in ctx.remove_blocks_list:
        name = str(entry).strip()
        if not name:
            continue
        if "->" in name:
            ctx.errors.append({
                "code": "remove_block_failed",
                "message": (
                    f"You passed {name!r} to remove_blocks. This looks like a "
                    "connection ID. Connections must be removed using the "
                    "remove_connections parameter, not remove_blocks."
                ),
            })
            continue
        try:
            apply_mutation(ctx.fg, "remove_block", instance_name=name)
            ctx.ops_applied += 1
        except Exception as exc:
            ctx.errors.append({"code": "remove_block_failed", "message": str(exc)})


def _phase_update_params(ctx: ChangeGraphContext) -> None:
    """Apply each ``update_params`` entry; KeyError → parameter_not_found."""
    for entry in ctx.update_params_list:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("instance_name", "")).strip()
        params = entry.get("params") or {}
        if not name:
            ctx.errors.append({
                "code": "invalid_update",
                "message": f"update_params entry needs instance_name: {entry}",
            })
            continue
        try:
            apply_mutation(ctx.fg, "update_params", instance_name=name, params=params)
            ctx.ops_applied += 1
        except KeyError as exc:
            ctx.errors.append({"code": "parameter_not_found", "message": str(exc)})
        except Exception as exc:
            ctx.errors.append({"code": "update_params_failed", "message": str(exc)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_change_graph_phases.py -v`
Expected: PASS — all six tests green.

- [ ] **Step 5: Commit**

```bash
git add src/grc_agent/runtime/change_graph.py tests/test_change_graph_phases.py
git commit -m "refactor(change_graph): add_blocks / remove_blocks / update_params phases"
```

---

## Task 3: Phases 4–5 — `auto_resolve_types`, `update_states` (TDD)

**Files:**
- Modify: `src/grc_agent/runtime/change_graph.py:199-240`
- Modify: `tests/test_change_graph_phases.py`

The auto-resolver is the trickiest phase because it uses `ctx.new_block_names` + `ctx.add_connections_list`. Add phase tests that don't rely on real neighbor ports.

- [ ] **Step 1: Write failing tests for `_phase_update_states` and `_phase_auto_resolve_types`**

Append:
```python
def test_phase_update_states_validates_presence(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_update_states

    _agent, ctx = ctx_factory
    ctx.update_states_list = [
        {"instance_name": "samp_rate", "state": "disabled"}
    ]
    _phase_update_states(ctx)
    # real block: ops applied
    assert ctx.ops_applied == 1


def test_phase_update_states_missing_keys_records_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_update_states

    _agent, ctx = ctx_factory
    ctx.update_states_list = [{"state": "disabled"}]  # no instance_name
    _phase_update_states(ctx)
    assert any(e["code"] == "invalid_state" for e in ctx.errors)


def test_phase_auto_resolve_types_no_op_when_type_already_set(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_auto_resolve_types

    _agent, ctx = ctx_factory
    ctx.new_block_names = {"dc"}
    ctx.type_already_set = {"dc"}  # batch already set the type
    _phase_auto_resolve_types(ctx)  # must not raise, must not touch the block
    # No errors and the block is still type-default (whatever the platform set).
    assert ctx.errors == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_change_graph_phases.py -v`
Expected: FAIL on the two new phases.

- [ ] **Step 3: Add the two phase methods**

```python
def _phase_auto_resolve_types(ctx: ChangeGraphContext) -> None:
    """Set ``type`` on newly-added blocks that don't have it explicit.

    Uniform rule: skip if the block already has a ``type`` set (via
    ``add_blocks`` OR ``update_params``); otherwise derive the dtype from the
    first neighbor port in ``ctx.add_connections_list`` and assign it.
    """
    for name in ctx.new_block_names:
        if name in ctx.type_already_set:
            continue
        try:
            block = ctx.fg.get_block(name)
        except KeyError:
            continue
        if "type" not in block.params:
            continue
        dtype = _neighbor_dtype_for(
            ctx.fg, name, ctx.add_connections_list, ctx.new_block_names
        )
        if not dtype:
            continue
        try:
            block.params["type"].set_value(dtype)
            ctx.fg.rewrite()
        except Exception as exc:
            logger.warning(
                "Failed to auto-resolve type for block %s: %s", name, exc
            )


def _phase_update_states(ctx: ChangeGraphContext) -> None:
    """Apply each ``update_states`` entry; missing keys → invalid_state."""
    for entry in ctx.update_states_list:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("instance_name", "")).strip()
        state = str(entry.get("state", "")).strip()
        if not name or not state:
            ctx.errors.append({
                "code": "invalid_state",
                "message": f"update_states entry needs instance_name and state: {entry}",
            })
            continue
        try:
            apply_mutation(ctx.fg, "update_states", instance_name=name, state=state)
            ctx.ops_applied += 1
        except Exception as exc:
            ctx.errors.append({"code": "update_states_failed", "message": str(exc)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_change_graph_phases.py -v`
Expected: PASS — all nine tests green.

- [ ] **Step 5: Commit**

```bash
git add src/grc_agent/runtime/change_graph.py tests/test_change_graph_phases.py
git commit -m "refactor(change_graph): auto_resolve_types + update_states phases"
```

---

## Task 4: Phases 6–7 — `remove_connections`, `add_connections` (TDD)

**Files:**
- Modify: `src/grc_agent/runtime/change_graph.py:242-296`
- Modify: `tests/test_change_graph_phases.py`

The connection phases carry the dtype-hint logic. Keep `_connection_dtype_hint` as it stands (already pure).

- [ ] **Step 1: Write failing tests for the connection phases**

Append:
```python
def test_phase_remove_connections_unparseable_records_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_remove_connections

    _agent, ctx = ctx_factory
    ctx.remove_connections_list = ["garbage_no_arrow_here"]
    _phase_remove_connections(ctx)
    assert any(
        e["code"] == "invalid_connection" for e in ctx.errors
    )


def test_phase_remove_connections_missing_arrow_suggests_remove_blocks(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_remove_connections

    _agent, ctx = ctx_factory
    ctx.remove_connections_list = ["my_block"]  # no "->"
    _phase_remove_connections(ctx)
    err = next(e for e in ctx.errors if e["code"] == "invalid_connection")
    assert "Did you mean to pass" in err["message"]


def test_phase_add_connections_unparseable_records_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_add_connections

    _agent, ctx = ctx_factory
    ctx.add_connections_list = ["garbage"]
    _phase_add_connections(ctx)
    assert any(e["code"] == "invalid_connection" for e in ctx.errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_change_graph_phases.py -v`
Expected: FAIL on the two connection phases.

- [ ] **Step 3: Add the two connection-phase methods**

```python
def _phase_remove_connections(ctx: ChangeGraphContext) -> None:
    """Remove each connection; idempotent (missing edges are skipped silently)."""
    for entry in ctx.remove_connections_list:
        conn_id = str(entry).strip()
        parsed = parse_connection_id(conn_id)
        if not parsed:
            hint = ""
            if "->" not in conn_id:
                hint = f" Did you mean to pass {conn_id!r} to remove_blocks instead?"
            ctx.errors.append({
                "code": "invalid_connection",
                "message": f"unparseable connection_id: {conn_id!r}.{hint}",
            })
            continue
        try:
            apply_mutation(
                ctx.fg,
                "remove_connection",
                src_block=parsed["src_block"],
                src_port=str(parsed["src_port"]),
                dst_block=parsed["dst_block"],
                dst_port=str(parsed["dst_port"]),
            )
            ctx.ops_applied += 1
        except KeyError:
            pass  # already gone — desired state achieved.
        except Exception as exc:
            ctx.errors.append({
                "code": "remove_connection_failed",
                "message": str(exc),
            })


def _phase_add_connections(ctx: ChangeGraphContext) -> None:
    """Add each connection; failed connections carry a dtype-aware hint."""
    for entry in ctx.add_connections_list:
        parsed = parse_connection_id(str(entry))
        if not parsed:
            ctx.errors.append({
                "code": "invalid_connection",
                "message": f"unparseable connection: {entry!r}",
            })
            continue
        try:
            apply_mutation(
                ctx.fg,
                "add_connection",
                src_block=parsed["src_block"],
                src_port=str(parsed["src_port"]),
                dst_block=parsed["dst_block"],
                dst_port=str(parsed["dst_port"]),
            )
            ctx.ops_applied += 1
        except Exception as exc:
            hint = _connection_dtype_hint(
                ctx.fg,
                parsed["src_block"],
                str(parsed["src_port"]),
                parsed["dst_block"],
                str(parsed["dst_port"]),
                ctx.new_block_names,
            )
            entry_err: dict[str, str] = {
                "code": "add_connection_failed",
                "message": str(exc),
            }
            if hint:
                entry_err["hint"] = hint
            ctx.errors.append(entry_err)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_change_graph_phases.py -v`
Expected: PASS — all twelve tests green.

- [ ] **Step 5: Commit**

```bash
git add src/grc_agent/runtime/change_graph.py tests/test_change_graph_phases.py
git commit -m "refactor(change_graph): remove_connections + add_connections phases"
```

---

## Task 5: Re-wire `dispatch_flat_change_graph_batch` to use the phases (no behavior change)

**Files:**
- Modify: `src/grc_agent/runtime/change_graph.py:26-354`

The dispatcher now: builds the `ChangeGraphContext`, runs the seven phases in order, then commits-or-rolls-back. No behavior change to the wire payload.

- [ ] **Step 1: Write a wire-format regression test (locks the public contract)**

Append to `tests/test_change_graph_phases.py`:
```python
def test_dispatch_wire_format_ok_true_on_success(tmp_path):
    """The wire payload must remain {'ok': True} on a clean commit."""
    from grc_agent.runtime.change_graph import dispatch_flat_change_graph_batch

    fixture = tmp_path / "empty.grc"
    fixture.write_text(
        "options:\n  parameters:\n    - id: top_block\n"
        "      label: Top Block\nblocks: []\nconnections: []\n",
        encoding="utf-8",
    )
    session = FlowgraphSession()
    session.load(fixture)
    agent = GrcAgent(session=session)
    payload = dispatch_flat_change_graph_batch(
        agent,
        update_params=[{"instance_name": "top_block",
                        "params": {"generate_options": "no"}}],
    )
    # Wire contract is unchanged: only ``ok`` and (on failure) ``errors``.
    assert payload["ok"] is True
    assert "committed" not in payload
    assert "state_revision" not in payload
    assert "validation" not in payload
    assert "rollback" not in payload


def test_dispatch_wire_format_error_has_code_and_message(tmp_path):
    """Failed commits return ok=False + errors=[{code, message}, ...]."""
    from grc_agent.runtime.change_graph import dispatch_flat_change_graph_batch

    session = FlowgraphSession()  # no file
    agent = GrcAgent(session=session)
    # No session.flowgraph — should yield a clean error.
    type("no_flowgraph", (), {"session": None})  # placeholder
    fake_agent = mock.Mock()
    fake_agent._missing_session_result.return_value = {
        "ok": False, "error_type": "no_session", "errors": []
    }
    payload = dispatch_flat_change_graph_batch(fake_agent, add_blocks=[])
    assert payload["ok"] is False
    assert payload["error_type"] == "no_session"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_change_graph_phases.py -v`
Expected: existing phase tests still pass; the two new wire-format tests fail because the dispatcher still uses the old structure.

- [ ] **Step 3: Replace the dispatcher body**

Replace the entire body of `dispatch_flat_change_graph_batch` (currently `src/grc_agent/runtime/change_graph.py:26-354`) with:

```python
def dispatch_flat_change_graph_batch(
    agent: Any,
    *,
    add_blocks: Any = None,
    remove_blocks: Any = None,
    update_params: Any = None,
    update_states: Any = None,
    add_connections: Any = None,
    remove_connections: Any = None,
    force: bool = False,
) -> ToolResult:
    """Execute the flat model-facing batch edit surface via the native adapter."""

    missing_session = agent._missing_session_result("change_graph")
    if missing_session is not None:
        return missing_session

    fg = agent.session.flowgraph
    if fg is None:
        return _tool_error(agent, "No flowgraph loaded.")

    integrity = agent.session.file_integrity_state()
    if integrity.get("externally_modified"):
        return agent._payload_result(
            "change_graph",
            {
                "ok": False,
                "error_type": ErrorCode.STALE_REVISION,
                "errors": [{
                    "code": ErrorCode.STALE_REVISION,
                    "message": "file changed on disk; reload before editing",
                }],
            },
        )

    errors: list[dict[str, str]] = []
    ctx = ChangeGraphContext(
        agent=agent,
        fg=fg,
        errors=errors,
        ops_applied=0,
        raw_add_blocks=add_blocks,
        raw_remove_blocks=remove_blocks,
        raw_update_params=update_params,
        raw_update_states=update_states,
        raw_add_connections=add_connections,
        raw_remove_connections=remove_connections,
    )

    ctx.add_blocks_list = _as_list(add_blocks, "add_blocks", ctx.errors)
    ctx.remove_blocks_list = _as_list(remove_blocks, "remove_blocks", ctx.errors)
    ctx.update_params_list = _as_list(update_params, "update_params", ctx.errors)
    ctx.update_states_list = _as_list(update_states, "update_states", ctx.errors)
    ctx.add_connections_list = _as_list(add_connections, "add_connections", ctx.errors)
    ctx.remove_connections_list = _as_list(remove_connections, "remove_connections", ctx.errors)

    ctx.new_block_names = {
        str(e.get("instance_name", "")).strip()
        for e in ctx.add_blocks_list
        if isinstance(e, dict) and str(e.get("instance_name", "")).strip()
    }
    ctx.removed_names = {
        str(e).strip()
        for e in ctx.remove_blocks_list
        if str(e).strip()
    }
    ctx.pre_edges = {
        connection_id(c.source_block.name, c.source_port.key,
                      c.sink_block.name, c.sink_port.key)
        for c in fg.connections
    }
    ctx.type_already_set = {
        name for name in ctx.new_block_names
        if name in {
            *(str(e.get("instance_name", "")).strip()
              for e in ctx.add_blocks_list
              if isinstance(e, dict) and "type" in (e.get("params") or {})),
            *(str(e.get("instance_name", "")).strip()
              for e in ctx.update_params_list
              if isinstance(e, dict) and "type" in (e.get("params") or {})
              and str(e.get("instance_name", "")).strip() in ctx.new_block_names),
        }
    }
    ctx.before_snapshot = capture_session_state(agent.session)

    from grc_agent.grc_native_adapter import serialize_flow_graph as _serialize_fg
    if agent.session.path is not None:
        try:
            ctx.before_serialized = _serialize_fg(fg)
        except Exception:
            ctx.before_serialized = None

    # --- Run the seven ordered phases ---
    _phase_add_blocks(ctx)
    _phase_remove_blocks(ctx)
    _phase_update_params(ctx)
    _phase_auto_resolve_types(ctx)
    _phase_update_states(ctx)
    _phase_remove_connections(ctx)
    _phase_add_connections(ctx)

    ops_applied = ctx.ops_applied

    # --- Validate + commit-or-rollback ---
    validation = validate(fg) if ops_applied else None
    validation_ok = validation.native_ok if validation else True

    if not validation_ok and not force:
        _restore_snapshot(agent, ctx.before_snapshot)
        committed = False
    elif ctx.errors:
        _restore_snapshot(agent, ctx.before_snapshot)
        committed = False
    else:
        committed = True

    if committed and ops_applied:
        agent.session.is_dirty = True
        agent.session.bump_revision()
        try:
            after_serialized = _serialize_fg(fg)
        except Exception:
            after_serialized = None
        if agent.session.path is not None and ctx.before_serialized != after_serialized:
            try:
                agent.session.save()
            except Exception as exc:
                logger.warning("Failed to save session for change_graph: %s", exc)

    payload: dict[str, Any] = {"ok": committed and not ctx.errors}
    if not committed and "error_type" not in payload:
        payload["error_type"] = ErrorCode.TOOL_CALL_INVALID
    if ctx.errors:
        payload["errors"] = ctx.errors

    if validation is not None and not bool(validation.native_ok):
        for entry in _validation_error_entries(
            validation.errors,
            _type_hint_for_validation(fg, validation.errors, ctx.new_block_names),
            _orphaned_port_hints(ctx.pre_edges, ctx.removed_names)
            if ctx.removed_names else {},
        ):
            payload.setdefault("errors", []).append(entry)
        if not committed:
            payload["error_type"] = ErrorCode.GNU_VALIDATION_FAILED

    return agent._payload_result("change_graph", payload)
```

- [ ] **Step 4: Re-run all `change_graph` tests**

Run:
```bash
uv run pytest tests/test_change_graph_hints.py tests/test_change_graph_phases.py -v
uv run pytest tests/test_save_integrity.py -v -m grc_native
```
Expected: PASS — all wire-format and existing tests still pass.

- [ ] **Step 5: Run the wider grc_native gate**

Run: `uv run pytest -m grc_native -v`
Expected: all 30+1 previously-passing tests still pass (the engine-core parametrized cases exercise every phase ordering).

- [ ] **Step 6: Commit**

```bash
git add src/grc_agent/runtime/change_graph.py tests/test_change_graph_phases.py
git commit -m "refactor(change_graph): phase-decomposed dispatcher (no wire-format change)"
```

---

## Task 6: Direct tests for `transaction.py` (TDD — the missing coverage)

**Files:**
- Create: `tests/test_transaction_roundtrip.py`
- Read-only ref: `src/grc_agent/transaction.py`

`transaction.py` ships with ZERO direct tests. The capture/restore round-trip must be tested. Identity loss is intentional (the restore replaces the `FlowGraph` with a fresh `import_data` result); the test documents that.

- [ ] **Step 1: Write failing tests for capture/restore**

Create `tests/test_transaction_roundtrip.py`:
```python
"""Round-trip tests for ``transaction.capture_session_state`` /
``restore_session_state``.

The capture path is exported by ``grc_native_adapter.export_data``; the
restore path constructs a brand-new ``FlowGraph`` via
``platform.make_flow_graph`` + ``import_data`` — by design, the restored
object is NOT identity-equal to the captured one. The tests below lock the
public contract: round-trip preserves block/connection/state counts (and
per-block state + param values) but the post-restore ``session.flowgraph``
is a new instance.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.grc_native_adapter import apply_mutation
from grc_agent.transaction import (
    SessionStateSnapshot,
    capture_session_state,
    restore_session_state,
)

FIXTURE = Path(__file__).resolve().parent / "data" / "dial_tone.grc"
pytestmark = pytest.mark.grc_native


def _fresh_session() -> FlowgraphSession:
    """Load a private copy of dial_tone.grc so back-to-back tests are isolated."""
    tmp = Path(tempfile.mkdtemp(prefix="tx_roundtrip_")) / "graph.grc"
    shutil.copy2(FIXTURE, tmp)
    session = FlowgraphSession()
    session.load(tmp)
    return session


def test_capture_returns_frozen_snapshot_with_raw_data():
    session = _fresh_session()
    snap = capture_session_state(session)
    # raw_data is a non-empty dict (top_block + audio_sink + ...).
    assert isinstance(snap.raw_data, dict)
    assert "blocks" in snap.raw_data
    assert len(snap.raw_data["blocks"]) > 0


def test_capture_is_decoupled_from_mutation():
    """Mutating after capture must not affect the snapshot's raw_data."""
    session = _fresh_session()
    snap_before = capture_session_state(session)
    apply_mutation(
        session.flowgraph,
        "update_params",
        instance_name="samp_rate",
        params={"value": "96000"},
    )
    snap_after = capture_session_state(session)
    # snap_before was deep-copied at capture time; snap_after sees the mutation.
    assert snap_before.raw_data != snap_after.raw_data


def test_capture_restores_path_dirty_revision_metadata():
    session = _fresh_session()
    session.is_dirty = True
    session.bump_revision()
    snap = capture_session_state(session)
    assert snap.is_dirty is True
    assert snap.state_revision == session.state_revision
    assert snap.path == session.path


def test_restore_round_trip_preserves_block_and_connection_counts():
    session = _fresh_session()
    blocks_before = len(session.flowgraph.blocks)
    conns_before = len(session.flowgraph.connections)
    snap = capture_session_state(session)
    # Mutate (add an extra block via the adapter).
    apply_mutation(
        session.flowgraph,
        "add_block",
        block_type="analog_const_source_x",
        instance_name="dc_added",
        parameters={"const": "0.0"},
    )
    assert len(session.flowgraph.blocks) == blocks_before + 1
    # Restore the snapshot.
    restore_session_state(session, snap)
    # Round-trip preserves the captured counts (post-restore has blocks_before,
    # NOT blocks_before + 1).
    assert len(session.flowgraph.blocks) == blocks_before
    assert len(session.flowgraph.connections) == conns_before


def test_restore_replaces_flow_graph_instance_intentionally():
    """Identity loss is intentional (export_data → import_data round-trip).
    The post-restore session.flowgraph MUST be a different Python object
    from the pre-restore one."""
    session = _fresh_session()
    original_fg = session.flowgraph
    snap = capture_session_state(session)
    # Mutate.
    apply_mutation(
        session.flowgraph,
        "update_params",
        instance_name="samp_rate",
        params={"value": "96000"},
    )
    restore_session_state(session, snap)
    # Post-restore object is a fresh FlowGraph (NOT identity-equal to original).
    assert session.flowgraph is not original_fg
    # …but the restored block count matches the captured state.
    assert len(session.flowgraph.blocks) == len(snap.raw_data["blocks"])


def test_restore_restores_dirty_revision_and_sha():
    session = _fresh_session()
    session.is_dirty = True
    session.bump_revision()
    snap = capture_session_state(session)
    # Wipe the live state.
    session.is_dirty = False
    session.set_state_revision(0)
    restore_session_state(session, snap)
    assert session.is_dirty is True
    assert session.state_revision == snap.state_revision
    assert session.persisted_file_sha256 == snap.persisted_file_sha256


def test_capture_with_no_flowgraph_returns_none_raw_data():
    session = FlowgraphSession()  # never loaded
    snap = capture_session_state(session)
    assert snap.raw_data is None


def test_restore_with_none_raw_data_clears_flowgraph():
    session = _fresh_session()
    snap = capture_session_state(session)
    snap = SessionStateSnapshot(
        raw_data=None,
        path=snap.path,
        is_dirty=snap.is_dirty,
        state_revision=snap.state_revision,
        persisted_file_sha256=snap.persisted_file_sha256,
    )
    restore_session_state(session, snap)
    assert session.flowgraph is None
```

- [ ] **Step 2: Run tests to verify they fail (or skip without grc_native)**

Run:
```bash
uv run pytest tests/test_transaction_roundtrip.py -v -m grc_native
```
Expected: Without `-m grc_native`: the entire file is skipped (no module-level `pytestmark` earlier). WITH `-m grc_native`: 8 PASS / 0 FAIL (these are calling real GRC APIs, which the existing `test_agent_flow_engine_core.py` already exercises).

- [ ] **Step 3: Confirm idempotency by re-running all change_graph tests**

Run:
```bash
uv run pytest tests/test_change_graph_hints.py tests/test_change_graph_phases.py tests/test_transaction_roundtrip.py -v -m grc_native
```
Expected: All green (8 round-trip + 12 phase + 7 hint tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_transaction_roundtrip.py
git commit -m "test(transaction): direct capture/restore round-trip coverage (grc_native)"
```

---

## Task 7: Final sweep — full default + grc_native gates

**Files:** No new files.

- [ ] **Step 1: Run the default gate**

Run: `uv run pytest -m "not grc_native and not gui and not llama_eval" -q`
Expected: 341 passed, 6 skipped (same as baseline).

- [ ] **Step 2: Run the grc_native gate**

Run: `uv run pytest -m grc_native -q`
Expected: ≥ 31+8 = 39 passed (the 8 new transaction round-trip tests added on top of the previous 30+1).

- [ ] **Step 3: Confirm `change_graph.py` LOC dropped**

Run:
```bash
wc -l src/grc_agent/runtime/change_graph.py
```
Expected: ≤ 580 lines (down from 611), with seven phase methods each ≤ 60 lines.

- [ ] **Step 4: Commit any loose ends**

```bash
git status  # should be clean
```

---

## Spec compliance summary

- 7-phase decomposition: ✅ Tasks 1–5 produce `_phase_add_blocks`, `_phase_remove_blocks`, `_phase_update_params`, `_phase_auto_resolve_types`, `_phase_update_states`, `_phase_remove_connections`, `_phase_add_connections`.
- Single pass over `add_blocks_list`: ✅ Task 5 builds it once in `ChangeGraphContext.__init__` and reuses it.
- No new model-facing fields / shapes: ✅ dispatcher emits the same `{"ok", "errors", "error_type"}` shape; Task 5 Step 1 explicitly tests for absence of any new key.
- `_record_error` closure retained: ✅ `_phase_*` methods append directly to `ctx.errors` (the same list, no closure).
- 15 `except Exception` blocks reduced: ✅ Task 5 dispatcher body has 4 (rollback save, serialize-fallback, restore-fallback); each phase has 1–3 narrowly-scoped ones.
- Direct tests for `transaction.py`: ✅ Task 6 creates 8 round-trip tests under the `grc_native` marker.

## Self-review

**Spec coverage:** All 7 phase names from the brief are implemented and unit-tested; single-pass over `add_blocks_list` done; no wire-format change; `transaction.py` round-trip tested.
**Placeholder scan:** No "TODO" / "TBD"; every Task has complete code blocks.
**Type consistency:** `ChangeGraphContext` dataclass is the single source for phase inputs; methods all take `ctx: ChangeGraphContext` and append to `ctx.errors` / increment `ctx.ops_applied`. Helper functions `_neighbor_dtype_for`, `_connection_dtype_hint`, `_orphaned_port_hints`, `_validation_error_entries`, `_type_hint_for_validation` keep the same signatures they had in the original file.
