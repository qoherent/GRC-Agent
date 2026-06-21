# Phase 4 — Domain Models (Pydantic V2)

> **Predecessor:** Phase 3 (`query_knowledge` experiment done; expected outcome: no refactor).
> **Successor:** Phase 5 (native adapter + mutation methods).
> **Goal:** Introduce Pydantic V2 schemas that lock the wire shape of the data the agent shows the model. **Outbound** schemas use `extra="forbid"` to lock the wire shape; **inbound** tool-input schemas use `extra="ignore"` for robustness against LLM hallucinations. **Do NOT touch `inspect_graph.py`, `change_graph.py`, or the flowgraph_session yet.** Phase 5 builds the adapter that fills these models; Phase 6 wires everything in a single cutover.

> **Why "models only":** per the consultant's architectural review, building the adapter or rewriting the tools before the models are locked creates a split-brain state (the adapter returns a dict, the tool handler expects a dict, the model_dump shape is unknown). Locking the models first gives Phase 5 and Phase 6 a stable target.

> **Why "outbound vs inbound":** per the consultant's review, `extra="forbid"` is correct for **outbound** state serialization (it locks the wire shape so the model's input is predictable). But `extra="forbid"` on **inbound** tool arguments will hard-crash the agent if the LLM hallucinates a harmless extra parameter. The two directions need different policies.

---

## 0. Cross-Phase Context

Before starting, read `plan_context.md` in full. Inherit:
- §3 (MVP surface)
- §4 (aggressive redesign rules — **no in-band control flow** is critical here; no field name or value may carry a directive)
- §5 (verified environment facts)
- §8 (cross-phase edge cases)
- §10 (commit cadence)

Also re-read:
- The handoff docs from Phases 1, 2, 3 to understand the data shapes proven out by the experiments.
- `playground/inspect_experiment/wire_shape_proposal.md` for the inspect wire shape.
- `playground/change_graph_experiment/analysis.md` for the mutation payload shape.
- `docs/GNU_NATIVE_METHODS.md` (the single source of truth for the GRC native API).

---

## 1. Why This Phase Comes After the Tool Experiments

The Pydantic schemas in this phase are **not speculative**. They are extracted from the shapes that Phase 1's `inspect_graph` experiment and Phase 2's `change_graph` experiment already proved out.

If you find yourself wanting to add a field that the experiments did not exercise, **stop**. The maintainer's rule is "no speculative expansion without live eval-harness evidence." Add the field only after an experiment shows it is needed.

---

## 2. The Design

### 2.1 Two model directions, two configs

The agent consumes Pydantic models in two directions:

- **Outbound (state to the model):** the agent serializes graph state to send to the LLM. The wire shape must be **locked** so the model's input is predictable across turns and across models. `extra="forbid"` is correct here — unknown fields are an error during serialization.
- **Inbound (LLM tool call arguments to the agent):** the LLM emits a tool call with arguments. The LLM may hallucinate extra parameters (e.g., a `verbose=true` it invented). Refusing the call because of an extra parameter hard-crashes the agent. `extra="ignore"` is correct here — unknown fields are dropped silently, and the known fields are validated.

Both directions live in `src/grc_agent/domain_models.py`, in clearly labeled sections. The outbound models use `model_config = ConfigDict(extra="forbid")`. The inbound models use `model_config = ConfigDict(extra="ignore")`.

### 2.2 Outbound models (`extra="forbid"`)

Pure data. No I/O. No `gnuradio.*` imports. No logger. ~150 lines.

```python
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

class BlockRole(str, Enum):
    VARIABLE = "variable"
    SOURCE = "source"
    SINK = "sink"
    TRANSFORM = "transform"
    VIRTUAL_OR_PAD = "virtual_or_pad"
    IMPORT = "import"
    SNIPPET = "snippet"
    OPTIONS = "options"
    OTHER = "other"

class GrcParameter(BaseModel):
    """Outbound: a single GRC block parameter as seen by the model."""
    model_config = ConfigDict(extra="forbid")
    name: str
    dtype: str
    value: Any
    evaluated_value: Any | None = None
    category: str = "General"
    hide: str = "none"

class GrcConnection(BaseModel):
    """Outbound: a single GRC connection as seen by the model."""
    model_config = ConfigDict(extra="forbid")
    connection_id: str
    src_block: str
    src_port: str
    dst_block: str
    dst_port: str
    dtype: str | None = None

class GrcBlock(BaseModel):
    """Outbound: a single GRC block as seen by the model."""
    model_config = ConfigDict(extra="forbid")
    instance_name: str
    block_type: str
    block_uid: str
    role: BlockRole
    state: str
    parameters: list[GrcParameter] = Field(default_factory=list)
    coordinate: tuple[float, float] | None = None

class GrcValidation(BaseModel):
    """Outbound: a GRC validation result as seen by the model."""
    model_config = ConfigDict(extra="forbid")
    status: str = "unknown"          # "valid" | "invalid" | "unknown"
    errors: list[str] = Field(default_factory=list)
    native_ok: bool | None = None

class GrcFlowgraph(BaseModel):
    """Outbound: a GRC flowgraph snapshot as seen by the model."""
    model_config = ConfigDict(extra="forbid")
    ok: bool
    graph_name: str
    file_format: int | None = None
    grc_version: str | None = None
    blocks: list[GrcBlock] = Field(default_factory=list)
    connections: list[GrcConnection] = Field(default_factory=list)
    validation: GrcValidation = Field(default_factory=GrcValidation)
    errors: list[dict[str, str]] = Field(default_factory=list)
    state_revision: int = 0
```

### 2.3 Inbound models (`extra="ignore"`)

These are the tool-input argument shapes. The LLM emits them; the agent validates them. Extra parameters are silently dropped.

```python
class InspectGraphArgs(BaseModel):
    """Inbound: arguments to the inspect_graph tool. Extra fields ignored."""
    model_config = ConfigDict(extra="ignore")
    view: str = "overview"           # "overview" | "details"
    targets: list[str] = Field(default_factory=list)
    params: list[str] = Field(default_factory=list)
    debug: bool = False

class ChangeGraphAddBlock(BaseModel):
    """Inbound: one add_block operation. Extra fields ignored."""
    model_config = ConfigDict(extra="ignore")
    block_id: str
    instance_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    state: str | None = None

class ChangeGraphRemoveBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    instance_name: str
    block_type: str | None = None

class ChangeGraphUpdateParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    instance_name: str
    params: dict[str, Any]
    block_type: str | None = None

class ChangeGraphUpdateStates(BaseModel):
    model_config = ConfigDict(extra="ignore")
    instance_name: str
    state: str
    block_type: str | None = None

class ChangeGraphAddConnection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    src: dict[str, str]              # {"block": "...", "port": "..."}
    dst: dict[str, str]

class ChangeGraphRemoveConnection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    connection_id: str

class ChangeGraphArgs(BaseModel):
    """Inbound: arguments to the change_graph tool. Extra fields ignored."""
    model_config = ConfigDict(extra="ignore")
    add_blocks: list[ChangeGraphAddBlock] = Field(default_factory=list)
    remove_blocks: list[ChangeGraphRemoveBlock] = Field(default_factory=list)
    update_params: list[ChangeGraphUpdateParams] = Field(default_factory=list)
    update_states: list[ChangeGraphUpdateStates] = Field(default_factory=list)
    add_connections: list[ChangeGraphAddConnection] = Field(default_factory=list)
    remove_connections: list[ChangeGraphRemoveConnection] = Field(default_factory=list)
    force: bool = False
    debug: bool = False

class QueryKnowledgeArgs(BaseModel):
    """Inbound: arguments to the query_knowledge tool. Extra fields ignored."""
    model_config = ConfigDict(extra="ignore")
    query: str
    domain: str = "blocks"           # "blocks" | "docs"
    debug: bool = False
```

### 2.4 Constraints (per AGENTS.md)

1. **Outbound: `extra="forbid"`.** Unknown fields are an error during serialization. The wire shape is locked.
2. **Inbound: `extra="ignore"`.** Unknown fields are silently dropped. The known fields are validated. The agent never crashes on an extra parameter.
3. **No ALL-CAPS directive strings anywhere.** `BlockRole.SOURCE.value == "source"`, not `"SOURCE_BLOCK"`. `GrcValidation.status` is `"valid" | "invalid" | "unknown"`, not a magic number.
4. **No "Use this when …" prose in field descriptions.** Pydantic's `Field(description=...)` is allowed only for short, factual descriptions. The model's system prompt is the only behavioral authority (per `AGENTS.md`).
5. **Defaults match the GRC native defaults.** `category = "General"` matches `Constants.DEFAULT_PARAM_TAB`. `hide = "none"` matches the unset case.
6. **`evaluated_value` is `Any | None = None`.** When the native evaluation fails or the value is not yet evaluated, the field is omitted via `exclude_none=True` in the wire dump.

### 2.5 The wire contract (outbound only)

```python
# Canonical model-visible payload shape after this phase
grc_flowgraph.model_dump(exclude_none=True)
# Returns:
# {
#   "ok": bool,
#   "graph_name": str,
#   "file_format": int | None,            # omitted if None
#   "grc_version": str | None,             # omitted if None
#   "blocks": [{"instance_name": ..., "block_type": ..., ...}, ...],
#   "connections": [{"connection_id": ..., ...}, ...],
#   "validation": {"status": ..., "errors": [...], "native_ok": bool | None},
#   "errors": [{"code": ..., "message": ...}, ...],
#   "state_revision": int,
# }
```

The downstream tool handlers (in `inspect_graph`, `change_graph`, etc.) consume this dict and wrap it in their own model-visible payloads (e.g., `inspect_graph` adds the `view`, `state_revision`, `params`, `targets`, `omitted` fields). The `domain_models.py` module is the **inner** contract; the tool handlers' payloads are the **outer** contract.

The inbound models are not serialized to the wire — they are only used to validate the LLM's tool calls before they reach the adapter.

---

## 3. Step-by-Step

### 3.1 Module creation (Day 1)

- [ ] **Step 1:** Create `src/grc_agent/domain_models.py` with the 6 outbound models, 7 inbound models, and 1 enum per §2.2 and §2.3.
- [ ] **Step 2:** Add a module docstring that references `plan_context.md` §3 (MVP surface) and §4 (no in-band control flow). Quote the rule explicitly. State the outbound/inbound config split clearly in the docstring.
- [ ] **Step 3:** Verify no `gnuradio.*` import. `rg -n 'gnuradio' src/grc_agent/domain_models.py` returns zero matches.

### 3.2 Tests (Days 1–2)

- [ ] **Step 4:** Create `tests/test_domain_models.py` with:
  - `test_grc_flowgraph_round_trip` — construct a `GrcFlowgraph` with two blocks and one connection; dump with `model_dump(exclude_none=True)`; assert top-level keys are exactly `{ok, graph_name, blocks, connections, validation, state_revision}` (other keys are `None` and excluded).
  - `test_grc_block_extra_forbid` — construct a `GrcBlock` with an unknown key `foobar=42`; assert `ValidationError`. (Outbound behavior.)
  - `test_grc_parameter_evaluated_value_omitted_when_none` — construct a `GrcParameter` without `evaluated_value`; assert the dumped dict does not contain the key.
  - `test_block_role_enum_values` — assert `BlockRole.SOURCE.value == "source"` and all 9 members exist.
  - `test_grc_validation_default` — `GrcValidation()` returns `status="unknown"`, `errors=[]`, `native_ok=None`.
  - `test_model_json_schema_is_stable` — call `GrcFlowgraph.model_json_schema()`; assert the schema's `properties` keys are exactly the expected set.
  - `test_no_in_band_directives` — spot-check: every string field on every model does not match `^[A-Z_]+$` (no ALL-CAPS), does not contain "Use this when", "Call X now", or "Retry".
  - `test_inspect_graph_args_extra_ignored` — construct `InspectGraphArgs(view="overview", targets=["all"], verbose=True)`; assert `verbose` is silently dropped, no `ValidationError`. (Inbound behavior.)
  - `test_change_graph_args_extra_ignored` — construct `ChangeGraphArgs(add_blocks=[...], mystery_field="hello")`; assert `mystery_field` is dropped.
  - `test_change_graph_args_missing_required` — `ChangeGraphArgs()` returns the default (empty lists, `force=False`); not a `ValidationError` because all fields have defaults.
  - `test_inspect_graph_args_default_view` — `InspectGraphArgs()` returns `view="overview"`, `targets=[]`, `params=[]`, `debug=False`.
- [ ] **Step 5:** Run `pytest tests/test_domain_models.py -v`. All 11+ tests pass.

### 3.3 Commit (Day 2)

- [ ] **Step 6:** Run `pytest -m "not grc_native and not gui and not llama_eval" -x`. All pass. **The agent's source tree must be unchanged** (`git diff --stat src/grc_agent/` returns zero matches, except for the new file).
- [ ] **Step 7:** Commit `feat(phase-4/models): add Pydantic v2 outbound and inbound schemas`. The new file is the only addition.

### 3.4 What this phase does NOT do (deferred)

- **Do NOT touch `src/grc_agent/runtime/inspect_graph.py`.** Phase 6.
- **Do NOT touch `src/grc_agent/runtime/change_graph.py`.** Phase 6.
- **Do NOT touch `src/grc_agent/flowgraph_session.py`.** Phase 6.
- **Do NOT create `src/grc_agent/grc_native_adapter.py`.** Phase 5.
- **Do NOT touch `src/grc_agent/runtime/tool_schemas.py`.** The Pydantic inbound models here are the source of truth; the JSON schema generator in `tool_schemas.py` may be retrofitted to read from them in a follow-up, but it's not in this phase.
- **Do NOT touch any test file other than `tests/test_domain_models.py`.**

---

## 4. Files to Touch

### 4.1 Creates

- `src/grc_agent/domain_models.py`
- `tests/test_domain_models.py`

### 4.2 Modifies

Nothing.

### 4.3 Deletes

Nothing.

### 4.4 Untouched (Phase 5 / 6 / 7's job)

- `src/grc_agent/grc_native_adapter.py` (Phase 5)
- `src/grc_agent/runtime/inspect_graph.py` (Phase 6)
- `src/grc_agent/runtime/change_graph.py` (Phase 6)
- `src/grc_agent/flowgraph_session.py` (Phase 6)
- `src/grc_agent/runtime/tool_schemas.py` (out of scope; the inbound models in `domain_models.py` are the source of truth for the future retrofit)
- `src/grc_agent_gui/inspector.py` (Phase 7)
- `tests/gui/test_inspector_widget.py` (Phase 7)

---

## 5. Verification Gate

The phase is done when **all** of the following hold:

- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes.
- [ ] `pytest -m grc_native` passes on the dev box.
- [ ] `pytest tests/test_domain_models.py -v` passes with 11+ test cases.
- [ ] `GrcFlowgraph.model_dump(exclude_none=True)` returns a dict whose top-level keys are exactly `{ok, graph_name, blocks, connections, validation, state_revision}` for a hand-constructed model.
- [ ] `GrcBlock(extra_field="x")` raises `ValidationError` (outbound is strict).
- [ ] `InspectGraphArgs(view="overview", verbose=True)` does NOT raise; `verbose` is silently dropped (inbound is lenient).
- [ ] No field on any model contains an ALL-CAPS directive, a "Use this when" phrase, or a procedural recipe.
- [ ] `rg -n 'gnuradio' src/grc_agent/domain_models.py` returns zero matches.
- [ ] `git diff --stat src/grc_agent/` shows only the new `domain_models.py` file. No other file is touched.

---

## 6. Edge Cases Specific to This Phase

| Edge case | Symptom | Mitigation |
|---|---|---|
| A Pydantic V1 feature is used by mistake (e.g., `class Config` instead of `model_config`) | Validation behavior is silently different | Use only Pydantic V2 idioms. The docstring states "Pydantic V2". Verify with `python -c "import pydantic; print(pydantic.VERSION)"` (must be ≥ 2.0). |
| An experiment-produced shape doesn't fit any model | The model is too restrictive | **Stop and ask.** Do not loosen the model's constraints to fit. The right move is to add a missing field with a default, document why, and commit. |
| A test asserts on a field that the model now marks `exclude_none=True` | The test fails when the field is `None` | The model is correct; the test should not depend on `None` being present. Update the test. |
| The LLM emits an inbound argument that has the right field name but the wrong type (e.g., `targets=42` instead of `targets=["all"]`) | `ValidationError` on inbound | Inbound `extra="ignore"` does NOT mean "ignore type errors." Type errors are still `ValidationError`. This is correct — the LLM emitted nonsense, the agent refuses. The error message is structured (`{code, message}`) and the model can recover. |
| Two models need to share a sub-model (e.g., `GrcBlock.parameters` is also a top-level dict) | Duplication | Extract the shared sub-model. Don't inline the same shape in two places. |
| Pydantic V2's `model_dump()` is slow on large flowgraphs | Performance regression | Use `model_dump(exclude_none=True, mode="json")` if `Any` fields contain non-JSON-serializable values. Profile with a 50-block fixture. |
| A field needs a custom serializer (e.g., `coordinate` as a tuple → list) | Default serializer is not JSON-friendly | Use `@field_serializer` on the model. Document the conversion. |
| `GrcBlock.role` is a `BlockRole` enum, but the wire payload expects a string | The model emits `"role": "BlockRole.SOURCE"` | `.value` is auto-applied by Pydantic V2 for `str` enums. Verify with a test. |
| The inbound `ChangeGraphArgs` is more permissive than the legacy tool input validation | An LLM call that the legacy would reject now passes | This is intentional per the consultant's review. The agent's downstream adapter (Phase 5) applies business-logic validation. Inbound is for shape, not for semantics. |

---

## 7. Handoff

When this phase finishes:

1. The implementing agent commits with the convention `feat(phase-4/models): add Pydantic v2 outbound and inbound schemas`.
2. The implementing agent writes `docs/refactor_plan/phase_4_handoff.md` with:
   - The `GrcFlowgraph.model_json_schema()` output (for the record)
   - The list of fields and their defaults
   - The list of inbound models and their `extra="ignore"` policy
   - Any decision the maintainer made about a non-obvious field (e.g., `evaluated_value` semantics)
   - The number of tests in `tests/test_domain_models.py`
   - The new test pass count
   - Confirmation that the agent's source tree is unchanged except for the new file
3. The next phase is Phase 5 (native adapter + mutation methods). The Phase 5 subagent reads `src/grc_agent/domain_models.py` and the Phase 1+2 experiment results, then builds the adapter that fills these models and performs the 6 mutation types.
