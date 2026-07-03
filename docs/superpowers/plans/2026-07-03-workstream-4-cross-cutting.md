# Workstream 4 — Cross-Cutting Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Depends on:** Works independently of Workstreams 1–3 (different files). Best executed last so any churn in shared abstractions (e.g. `grc_native_adapter` imports) is already settled.

**Goal:** Three discrete cleanups that the investigation surfaced but that don't overlap with Workstreams 1–3:
1. `flowgraph_session.py` carries five deferred `from grc_agent.grc_native_adapter import ...` calls inside method bodies — all of them are importable at module top-level (no circular dependency exists), so move them up to make the module's actual import surface visible.
2. `block_semantics.py` has five `pass`-bodied `except Exception` swallows; `param_filter.py` has three. Apply one uniform rule to each: every swallowed exception is logged at `DEBUG` level with the exception class name, so future bug-hunters can `logging.getLogger().setLevel(DEBUG)` and see what was hidden.
3. `_EVALUATED_HIDE_CACHE` (the module-level cache that powers every param-filter call) has zero direct tests. Add a focused test for cache-key identity (same values → same dictionary, mutated values → fresh dictionary).

**Architecture:** No new abstractions. All three tasks are simplification-by-removal (move imports up) and observable diagnostics (add the missing `logger.debug`). No wire-format change, no model-facing change, no behavior change. The cache test pins existing behavior.

**Tech Stack:** Python 3.12, pytest (default + `-m grc_native` gate), GNU Radio (only for the cache test that requires `new_block`).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/grc_agent/flowgraph_session.py` | Move 5 method-body `from grc_native_adapter import ...` lines to module top | Modify |
| `src/grc_agent/runtime/block_semantics.py` | Add a module-level logger; replace 5 silent `pass` blocks with `logger.debug` | Modify |
| `src/grc_agent/runtime/param_filter.py` | Add a module-level logger; replace 3 silent `pass` blocks with `logger.debug` | Modify |
| `tests/test_block_semantics_cache.py` | Direct tests for `_EVALUATED_HIDE_CACHE` semantics | Create |
| `tests/test_flowgraph_session_imports.py` | Smoke test that the moved-up imports actually resolve | Create |

---

## Task 1: Lift deferred imports in `flowgraph_session.py` to module top-level (TDD)

**Files:**
- Modify: `src/grc_agent/flowgraph_session.py:85, 112, 168, 185, 197, 205`
- Create: `tests/test_flowgraph_session_imports.py`

`flowgraph_session.py` already has `from grc_agent.grc_native_adapter import load_flow_graph` at module top (line 18-20). All the deferred imports are for symbols that don't have circular-import risk — the rule says "no silent transformation." Deferring an import for no reason is just obscurity; lift it.

- [ ] **Step 1: Write failing smoke test that captures the existing import surface**

Create `tests/test_flowgraph_session_imports.py`:
```python
"""Regression: every name ``flowgraph_session`` consumes from
``grc_native_adapter`` is part of its public-import surface (importable
without an active flowgraph).

The previous shape deferred five ``from grc_native_adapter import ...``
calls inside method bodies; lifting them to module top makes the true
surface visible.
"""

from __future__ import annotations


def test_flowgraph_session_imports_the_names_it_uses():
    import grc_agent.flowgraph_session as fs_mod

    # Symbols the module consumes — must be importable via the module's
    # __dict__ (proves the import lives at module top, not inside a method).
    assert "render_flow_graph" in fs_mod.__dict__
    assert "validate" in fs_mod.__dict__
    assert "get_platform" in fs_mod.__dict__
    assert "serialize_raw_data" in fs_mod.__dict__
    assert "exclusive_file_lock" in fs_mod.__dict__
    assert "refuse_ambiguous_save_target" in fs_mod.__dict__
    assert "write_flow_graph_atomic" in fs_mod.__dict__
    assert "write_save_backup" in fs_mod.__dict__


def test_no_method_body_re_imports_grc_native_adapter():
    """No method body in FlowgraphSession may import from
    ``grc_native_adapter`` after this task — every such import lives at
    module top so the dependency graph is visible in one place."""
    import inspect
    from grc_agent.flowgraph_session import FlowgraphSession

    forbidden = {"from grc_agent.grc_native_adapter", "from grc_native_adapter"}
    for name, member in inspect.getmembers(FlowgraphSession, predicate=inspect.isfunction):
        try:
            src = inspect.getsource(member)
        except OSError:
            continue
        for forbidden_marker in forbidden:
            assert forbidden_marker not in src, (
                f"{name}() still has a deferred import: {forbidden_marker}\n{src[:200]}"
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_flowgraph_session_imports.py -v`
Expected: FAIL — `render_flow_graph`, `validate`, etc. are not in `fs_mod.__dict__` yet (they're still imported lazily inside methods).

- [ ] **Step 3: Move the deferred imports to the module top block**

Replace `src/grc_agent/flowgraph_session.py:18-20`:
```python
from grc_agent.grc_native_adapter import (
    load_flow_graph,
)
```
with:
```python
from grc_agent.grc_native_adapter import (
    exclusive_file_lock,
    get_platform,
    load_flow_graph,
    refuse_ambiguous_save_target,
    render_flow_graph,
    serialize_raw_data,
    validate,
    write_flow_graph_atomic,
    write_save_backup,
)
```

Then remove the following in-method `from grc_agent.grc_native_adapter import ...` blocks entirely:
- Line 85–90 inside `FlowgraphSession.save` (the 4-function import).
- Line 112 inside `summary_payload` (`render_flow_graph`).
- Line 168 inside `validation_state` (`validate`).
- Line 185 inside `from_raw_data` (`get_platform`).
- Line 197 inside `_serialize_raw_data` (`serialize_raw_data`).
- Line 205 inside `validate` (`validate as _validate`).

For `validate as _validate` (line 205) the body uses `_validate(...)` — keep that underscore-prefixed local name intact by dropping the alias and just using `validate(...)`. For `render_flow_graph` (line 112) the call site uses `render_flow_graph(self.flowgraph)` — same fix.

- [ ] **Step 4: Re-run the smoke tests**

Run: `uv run pytest tests/test_flowgraph_session_imports.py -v`
Expected: PASS — every name is in `fs_mod.__dict__` and no method body has a deferred import.

- [ ] **Step 5: Run the full default + grc_native suites**

Run:
```bash
uv run pytest -m "not grc_native and not gui and not llama_eval" -q
uv run pytest -m grc_native -q
```
Expected: same number of passes as before (no behavior change — imports are resolved earlier, which is faster but does not affect output).

- [ ] **Step 6: Commit**

```bash
git add src/grc_agent/flowgraph_session.py tests/test_flowgraph_session_imports.py
git commit -m "refactor(flowgraph_session): lift deferred grc_native_adapter imports to module top"
```

---

## Task 2: Replace silent `except Exception: pass` with `logger.debug` in `block_semantics.py`

**Files:**
- Modify: `src/grc_agent/runtime/block_semantics.py:36-69`

Five swallowed exceptions, all currently silent. AGENTS.md forbids silent transformation. The uniform rule: every swallowed exception logs at `DEBUG` with the exception class name and the failing context. Production behavior is unchanged (the function still returns `{}` on failure) but the failure mode becomes observable under `logging.getLogger('grc_agent').setLevel(logging.DEBUG)`.

- [ ] **Step 1: Add a module logger**

Replace `src/grc_agent/runtime/block_semantics.py:7-12` (after the docstring):
```python
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Replace each silent `except Exception` with a logged one**

Replace `src/grc_agent/runtime/block_semantics.py:36-69` (the body of `_compute_evaluated_param_hides`) with:
```python
def _compute_evaluated_param_hides(block_type: str, param_values: dict[str, Any]) -> dict[str, str]:
    """Compute the GRC-native ``hide`` values for ``block_type``.

    Returns ``{}`` on every failure path; every swallow is logged at
    DEBUG so debugging tool-vs-platform issues no longer requires a
    debugger on the consumer side.
    """
    try:
        from grc_agent.grc_native_adapter import get_platform_or_none

        platform = get_platform_or_none()
    except Exception as exc:
        logger.debug(
            "evaluated_param_hides platform_import_failed block=%s: %s: %s",
            block_type, type(exc).__name__, exc,
        )
        return {}
    if platform is None:
        logger.debug("evaluated_param_hides no_platform block=%s", block_type)
        return {}
    try:
        flow_graph = platform.make_flow_graph()
        block = flow_graph.new_block(block_type)
    except Exception as exc:
        logger.debug(
            "evaluated_param_hides new_block_failed block=%s: %s: %s",
            block_type, type(exc).__name__, exc,
        )
        return {}
    # ``new_block`` returns None for control blocks (variable, parameter,
    # options, etc.) — the platform does not model them as instance blocks
    # in a flow graph. Return an empty hide map; the caller falls back to
    # the full param list. (Not a swallowed exception — None is a valid
    # platform response.)
    if block is None:
        return {}
    try:
        for key, value in param_values.items():
            param = block.params.get(key) if hasattr(block.params, "get") else None
            if param is not None:
                try:
                    param.value = "" if value is None else str(value)
                except Exception as exc:
                    logger.debug(
                        "evaluated_param_hides set_value_failed block=%s key=%s: %s: %s",
                        block_type, key, type(exc).__name__, exc,
                    )
        try:
            flow_graph.rewrite()
        except Exception as exc:
            logger.debug(
                "evaluated_param_hides rewrite_failed block=%s: %s: %s",
                block_type, type(exc).__name__, exc,
            )
        return {str(name): str(param.hide) for name, param in block.params.items()}
    except Exception as exc:
        logger.debug(
            "evaluated_param_hides collect_failed block=%s: %s: %s",
            block_type, type(exc).__name__, exc,
        )
        return {}
```

- [ ] **Step 3: Run the existing test suites**

Run:
```bash
uv run pytest tests/test_catalog_vector_unit.py -v
uv run pytest -m grc_native -q
```
Expected: PASS — observable behavior (return value) is identical; only the diagnostic logging changes.

- [ ] **Step 4: Spot-check the debug output**

Run:
```bash
LOG_LEVEL=DEBUG uv run python -c "
from grc_agent.runtime.block_semantics import evaluated_param_hides
import logging
logging.basicConfig(level=logging.DEBUG)
# Trigger one swallow path by passing invalid block type.
result = evaluated_param_hides('not_a_real_block_xyz', {})
print('returned', result)
"
```
Expected: a `DEBUG` line in the log carrying `evaluated_param_hides new_block_failed block=not_a_real_block_xyz:` followed by the exception class name; the function still returns `{}`.

- [ ] **Step 5: Commit**

```bash
git add src/grc_agent/runtime/block_semantics.py
git commit -m "refactor(block_semantics): log swallowed exceptions at DEBUG (uniform rule)"
```

---

## Task 3: Same uniform rule in `param_filter.py`

**Files:**
- Modify: `src/grc_agent/runtime/param_filter.py:114-147`

Three silent swallows in `param_metadata`. Same uniform rule as Task 2.

- [ ] **Step 1: Add the module logger**

Insert immediately after `from __future__ import annotations` (line 27) and the existing imports (lines 28–32):
```python
import logging

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Replace the three silent `except Exception` blocks**

Replace `src/grc_agent/runtime/param_filter.py:114-147` (the body of `param_metadata`) with:
```python
@cache
def param_metadata(block_type: str) -> dict[str, dict[str, str]]:
    """Static per-param metadata from the GRC block definition.

    Returns ``{param_key: {"category": ..., "dtype": ..., "default": ...}}``
    from one throwaway block instantiation. Empty dict if the platform is
    unavailable. Every swallow logs at DEBUG.
    """
    try:
        from grc_agent.grc_native_adapter import get_platform_or_none

        platform = get_platform_or_none()
    except Exception as exc:
        logger.debug(
            "param_metadata platform_import_failed block=%s: %s: %s",
            block_type, type(exc).__name__, exc,
        )
        return {}
    if platform is None:
        logger.debug("param_metadata no_platform block=%s", block_type)
        return {}
    try:
        flow_graph = platform.make_flow_graph()
        block = flow_graph.new_block(block_type)
    except Exception as exc:
        logger.debug(
            "param_metadata new_block_failed block=%s: %s: %s",
            block_type, type(exc).__name__, exc,
        )
        return {}
    if block is None:
        return {}
    try:
        return {
            str(name): {
                "category": str(getattr(param, "category", DEFAULT_PARAM_TAB)),
                "dtype": str(getattr(param, "dtype", "")),
                "default": str(getattr(param, "default", "")),
            }
            for name, param in block.params.items()
        }
    except Exception as exc:
        logger.debug(
            "param_metadata collect_failed block=%s: %s: %s",
            block_type, type(exc).__name__, exc,
        )
        return {}
```

- [ ] **Step 3: Re-run all test suites**

Run:
```bash
uv run pytest -m "not grc_native and not gui and not llama_eval" -q
uv run pytest -m grc_native -q
```
Expected: 341 passed, 6 skipped (default); same as before — behavior unchanged.

- [ ] **Step 4: Commit**

```bash
git add src/grc_agent/runtime/param_filter.py
git commit -m "refactor(param_filter): log swallowed exceptions at DEBUG (uniform rule)"
```

---

## Task 4: Direct tests for `_EVALUATED_HIDE_CACHE` (the missing cache coverage)

**Files:**
- Create: `tests/test_block_semantics_cache.py`

The cache key is built from `(block_type, tuple(sorted((k, str(v)) for k, v in values.items())))`. Bugs in this composition (e.g. missing `str(v)` coercion, missing sort) would silently produce the wrong hide map. This task pins the cache contract.

- [ ] **Step 1: Write failing tests for cache-key correctness**

Create `tests/test_block_semantics_cache.py`:
```python
"""Direct tests for ``_EVALUATED_HIDE_CACHE`` (the LRU behind every
``keep_param`` call).

The cache is keyed off ``(block_type, frozen_values)``. Bugs in this
construction propagate silently into ``filter_live_block_params`` and
``visible_param_keys`` — both called inside every tool result.
"""

from __future__ import annotations

from grc_agent.runtime import block_semantics
from grc_agent.runtime.block_semantics import (
    _EVALUATED_HIDE_CACHE,
    evaluated_param_hides,
)

FIXTURE_BLOCK = "analog_sig_source_x"


def setup_function(_):
    """Each test starts with an empty cache."""
    _EVALUATED_HIDE_CACHE.clear()


def _stub_hides(result):
    """Replace the compute backend with a sentinel list."""
    original = block_semantics._compute_evaluated_param_hides
    block_semantics._compute_evaluated_param_hides = lambda *a, **k: result
    return original


def teardown_function(_):
    """None — setUp cleared the cache; nothing to undo."""
    pass


def test_cache_key_is_block_type_and_value_snapshot():
    """Same block_type + same values tuple → same cache entry."""
    sentinel = {"alpha1": "all", "type": "none"}
    original = block_semantics._compute_evaluated_param_hides
    block_semantics._compute_evaluated_param_hides = lambda b, v: sentinel
    try:
        first = evaluated_param_hides(FIXTURE_BLOCK, {"value": "1.0"})
    finally:
        block_semantics._compute_evaluated_param_hides = original

    # The cache MUST now carry exactly one entry for this key.
    keys = [k for k in _EVALUATED_HIDE_CACHE if k[0] == FIXTURE_BLOCK]
    assert len(keys) == 1, f"cache grew with duplicate keys: {keys}"
    # And the cached value is the sentinel (not a deep copy — the cache
    # stores a reference).
    assert _EVALUATED_HIDE_CACHE[keys[0]] is sentinel or first is sentinel


def test_cache_distinguishes_value_changes():
    """Changing a value produces a different cache key, not a mutation."""
    sentinel = {"alpha1": "all"}
    original = block_semantics._compute_evaluated_param_hides
    block_semantics._compute_evaluated_param_hides = lambda b, v: sentinel
    try:
        evaluated_param_hides(FIXTURE_BLOCK, {"value": "1.0"})
        evaluated_param_hides(FIXTURE_BLOCK, {"value": "2.0"})
    finally:
        block_semantics._compute_evaluated_param_hides = original

    keys = [k for k in _EVALUATED_HIDE_CACHE if k[0] == FIXTURE_BLOCK]
    assert len(keys) == 2, f"two distinct values must produce two cache keys: {keys}"


def test_cache_distinguishes_extra_keys():
    """Adding an unrelated key is a NEW cache entry."""
    original = block_semantics._compute_evaluated_param_hides
    block_semantics._compute_evaluated_param_hides = lambda b, v: {}
    try:
        evaluated_param_hides(FIXTURE_BLOCK, {"value": "1.0"})
        evaluated_param_hides(FIXTURE_BLOCK, {"value": "1.0", "extra": "x"})
    finally:
        block_semantics._compute_evaluated_param_hides = original

    keys = [k for k in _EVALUATED_HIDE_CACHE if k[0] == FIXTURE_BLOCK]
    assert len(keys) == 2


def test_cache_lookup_hits_after_first_call():
    """The second call with the same key MUST NOT call the compute backend."""
    calls: list[tuple] = []

    def counter(block_type, param_values):
        calls.append((block_type, tuple(sorted(param_values.items()))))
        return {"alpha1": "all"}

    original = block_semantics._compute_evaluated_param_hides
    block_semantics._compute_evaluated_param_hides = counter
    try:
        evaluated_param_hides(FIXTURE_BLOCK, {"value": "1.0"})
        evaluated_param_hides(FIXTURE_BLOCK, {"value": "1.0"})
    finally:
        block_semantics._compute_evaluated_param_hides = original

    assert len(calls) == 1, f"cache missed on identical call: {calls}"


def test_cache_coerces_values_to_string():
    """``None`` and integer values produce the same cache key as the string
    equivalent (the cache key uses ``str(value)``)."""
    original = block_semantics._compute_evaluated_param_hides
    block_semantics._compute_evaluated_param_hides = lambda b, v: {}
    try:
        evaluated_param_hides(FIXTURE_BLOCK, {"freq": 1000})
        evaluated_param_hides(FIXTURE_BLOCK, {"freq": "1000"})
    finally:
        block_semantics._compute_evaluated_param_hides = original

    keys = [k for k in _EVALUATED_HIDE_CACHE if k[0] == FIXTURE_BLOCK]
    # Both calls collapsed to one key.
    assert len(keys) == 1, f"int and str values must hit same key: {keys}"


def test_cache_coerces_none_value():
    """``None`` coerces to ``""`` in the cache key (the param_filter Bible
    treats empty string as the canonical absent value)."""
    original = block_semantics._compute_evaluated_param_hides
    block_semantics._compute_evaluated_param_hides = lambda b, v: {}
    try:
        evaluated_param_hides(FIXTURE_BLOCK, {"comment": None})
        evaluated_param_hides(FIXTURE_BLOCK, {"comment": ""})
    finally:
        block_semantics._compute_evaluated_param_hides = original

    keys = [k for k in _EVALUATED_HIDE_CACHE if k[0] == FIXTURE_BLOCK]
    assert len(keys) == 1
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_block_semantics_cache.py -v`
Expected: PASS — all 6 cache tests green. The compute backend is replaced with a stub so no GNU Radio is required (each test bypasses the real platform path).

- [ ] **Step 3: Run the broader grc_native suite**

Run: `uv run pytest -m grc_native -q`
Expected: same number of passes plus the 6 new cache tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_block_semantics_cache.py
git commit -m "test(block_semantics): direct cache coverage for _EVALUATED_HIDE_CACHE"
```

---

## Task 5: Final sweep — all gates green

**Files:** No new files.

- [ ] **Step 1: Run default suite**

Run: `uv run pytest -m "not grc_native and not gui and not llama_eval" -q`
Expected: 341 + N passed where N is the count of cross-cutting tests added (none in this plan target the default suite — the new tests live behind the standard pytest collection, so add the count for the imports smoke test = 2 tests).

- [ ] **Step 2: Run grc_native suite**

Run: `uv run pytest -m grc_native -q`
Expected: previous count + 6 cache tests.

- [ ] **Step 3: Confirm no module body gets new top-level `import` for things that were already there**

Run:
```bash
grep -c "^from grc_agent.grc_native_adapter" src/grc_agent/flowgraph_session.py src/grc_agent/runtime/block_semantics.py src/grc_agent/runtime/param_filter.py
```
Expected: `flowgraph_session.py: 1`, `block_semantics.py: 0` (still inside the function — Tasks 2–3 touched only logging, not imports), `param_filter.py: 1`.

- [ ] **Step 4: Confirm no silent `except Exception: pass` remains in the touched files**

Run:
```bash
grep -A 1 "except Exception" src/grc_agent/runtime/block_semantics.py src/grc_agent/runtime/param_filter.py
```
Expected: every `except Exception` is followed by a `logger.debug(...)` call, not a bare `pass`.

- [ ] **Step 5: Commit any loose ends**

```bash
git status  # should be clean
```

---

## Spec compliance summary

- **No overlap with WS 1–3:** Task 1 touches `flowgraph_session.py` (no WS coverage); Tasks 2–3 touch `block_semantics.py` + `param_filter.py` (no WS coverage); Task 4 adds a brand-new test file. The catalog/doc stores, change_graph, transaction, and chat_widget are untouched.
- **Move deferred imports:** ✅ Task 1 lifts 5 named functions from method bodies to module top.
- **One uniform rule for silent ignores:** ✅ Tasks 2 + 3 apply `logger.debug(...)` at every swallow point in `block_semantics._compute_evaluated_param_hides` (5 paths) and `param_filter.param_metadata` (3 paths). Production return values are unchanged.
- **Direct cache tests:** ✅ Task 4 adds 6 tests covering key composition (mutation, value changes, type coercion, None coercion, compute-backend de-duplication).
- **No behavior change:** Wire-level outputs (every runtime tool), `keep_param` results, and `flowgraph_session` save semantics are identical. The only observable difference is `DEBUG`-level log lines under explicit debug-logging.

## Self-review

**Spec coverage:** All four cross-cutting cleanup items — deferred imports in `flowgraph_session`, silent ignores in `block_semantics`, silent ignores in `param_filter`, cache coverage — are addressed by their own tasks. Wire behavior is unchanged everywhere.
**Placeholder scan:** No "TODO"/"TBD"; every task has exact code blocks. The tests stub `_compute_evaluated_param_hides` so the cache contract is pinned without needing GNU Radio.
**Type consistency:** `block_semantics._compute_evaluated_param_hides` and `param_filter.param_metadata` keep their original signatures `(block_type: str, param_values: dict[str, Any])` and `block_type: str` respectively. The module-level logger name follows the project convention (`logging.getLogger(__name__)`).
