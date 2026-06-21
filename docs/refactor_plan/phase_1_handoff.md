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

The formalized target JSON wire shape and filtering logic has been defined in [wire_shape_proposal.md](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/wire_shape_proposal.md). It models three target interaction modes:

### 3.1 Overview Mode (`targets=[]`, `params=[]`)
A hyper-minimal representation of the entire flowgraph with an average **60.42% character size reduction** (up to 68% on complex graphs). It applies the following rules:
- **`options` block whitelist**: Strictly includes only `id`, `title`, `generate_options`, and `output_language`. All other parameters (author, cmake options, etc.) are dropped.
- **Tab/Category Exclusion**: Parameters categorized under `Advanced` (GRC-appended boilerplate like `alias`, `affinity`, `minoutbuf`, etc.) or `Config` (Pure QT styling properties) are excluded.
- **Visibility**: Excludes parameters where evaluated `p.hide == "all"`.
- **Value-Based Prominence (Overview Mode Only)**: Includes a parameter only if it is always visible (`p.hide == "none"`), its value has been changed from default (`p.value != p.default`), or its value references a variable or another block's name in the graph.

### 3.2 Details Mode (`targets=["block_name"]`)
- **Targeted Blocks**: Return the full list of active parameters (filtering out only `Advanced`/`Config` tabs and evaluated `hide == "all"`).
- **Non-Targeted Blocks**: Remain in the hyper-minimal Overview Mode.

### 3.3 Param Filter Mode (`targets=["block_name"]`, `params=["param_key"]`)
- **Targeted Blocks**: Only return parameters whose key is explicitly listed in `params`.
- **Non-Targeted Blocks**: Remain in Overview Mode, returning only overview parameters that match keys in `params`.

### 3.4 Role Resolution Rules
Resolved sequentially via native GRC properties:
1. `variable_or_control` if `block.is_variable`
2. `import` if `block.is_import`
3. `snippet` if `block.is_snippet`
4. `virtual_or_pad` if `block.is_virtual_or_pad`
5. `metadata` if `block.key == "options"`
6. `source` if `len(sinks) == 0` and `len(sources) > 0`
7. `sink` if `len(sources) == 0` and `len(sinks) > 0`
8. `transform` if both sinks and sources are present.

---

## 4. Verification & Status
- **Source Code Integrity**: `git diff --stat src/grc_agent/` confirms **zero** source files were touched in `src/`.
- **Test suite status**: **PASSED** (all 350 tests pass).
- **Playground artifacts generated**:
  - Analysis report: [analysis.md](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/analysis.md)
  - Target wire shape proposal: [wire_shape_proposal.md](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/wire_shape_proposal.md)
- **Commit**: Completed as `chore(phase-1/inspect): refresh experiment and document wire shape`.

Successor Phase: **Phase 2** (`change_graph` experiment).
