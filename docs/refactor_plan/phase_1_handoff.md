# Phase 1 Handoff — `inspect_graph` Experiment

## 1. Overview of the Experiment
The objective of Phase 1 was to run the `inspect_graph` experiment comparing the legacy dictionary-crawling parser (`run_experiment.py`) with the native GRC Platform API (`verify_native_api.py`). Under the maintainer's directive, we purged the legacy ad-hoc regex filter from the playground script and relied strictly on native GRC property metadata (`hide` and `category`).

The native GRC API succeeded in:
- Preserving 100% of the parameters present in the legacy output (including GUI parameters such as `label` and `autoscale`).
- Automatically capturing a richer set of General-tab/DSP parameters (like `type` and `vlen` drop-downs) that legacy parsing missed.
- Correctly filtering out disabled blocks and their associated connections in the topology layer.

---

## 2. Gaps Summary

### Structure Gaps:
- **Legacy Structure**: Nested graph parameters under `params`, with block lists, active connections, and validation status grouped under a separate `graph` key.
- **Native Structure**: A flat JSON object containing `options`, `blocks` (with parameters inlined directly under each block's name key), `connections` (top-level list), and `validation` (top-level dictionary).

### Block & Connection Gaps:
- **Metadata**: Native includes the `options` block as a block with type `"options"` and role `"metadata"`.
- **Disabled Blocks/Connections**: Native correctly excludes them entirely from `blocks` and `connections` by inspecting `.enabled` attributes on blocks and connections. Legacy included them.

### Parameter Gaps:
- **Drops**: Zero parameter drops. Under the strict native filter pipeline, the native output is a complete superset of the legacy output.
- **Additions**: Richer General-tab parameters (e.g. `type`, `vlen`, `ignoretag`, `ylabel`, `ymin`, `ymax`) are natively captured.

---

## 3. Proposal Summary

The formalized target JSON wire shape and filtering logic has been defined in [wire_shape_proposal.md](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/wire_shape_proposal.md):

- **Inlining**: Parameters are inlined inside each block's definition under `"parameters"`.
- **Filtering Rules**: Exclude parameters only if `p.hide == "all"` or `p.category in {"Advanced", "Config"}`.
- **Role Resolution**: Resolved sequentially via native GRC block properties (`is_variable`, `is_import`, `is_snippet`, `is_virtual_or_pad`), specific keys (`key == "options"`), and input/output port counts.

---

## 4. Verification & Status
- **Source Code Integrity**: `git diff --stat src/grc_agent/` confirms **zero** source files were touched in `src/`.
- **Test suite status**: **PASSED** (all 350 tests pass).
- **Playground artifacts generated**:
  - Analysis report: [analysis.md](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/analysis.md)
  - Target wire shape proposal: [wire_shape_proposal.md](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/wire_shape_proposal.md)
- **Commit**: Completed as `chore(phase-1/inspect): refresh experiment and document wire shape`.

Successor Phase: **Phase 2** (`change_graph` experiment).
