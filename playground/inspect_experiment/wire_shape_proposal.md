# Wire Shape Proposal for `inspect_graph`

This document defines the formalized target JSON wire shape for the model-facing `inspect_graph` tool. Every field and rule is mapped to its implementation in [verify_native_api.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py).

All filtering is derived from **native GNU Radio GRC properties** (`gnuradio.grc.core`): `Param.hide`, `Param.category`, `Param.dtype`, `Param.is_enum()`, `Param.value`/`Param.default`, `Block.enabled`/`get_bypassed()`, `Block.is_variable`, and `Connection.enabled`. There are no hand-picked per-field allowlists and no string-substring heuristics.

---

## 1. The Sparse / Dense Paradigm

The tool exposes one graph at three densities via two arguments — `targets` (block names) and `params` (parameter keys):

- **Overview Mode** (`targets=[]`) is **sparse**. It strips every parameter that equals the GRC default, is hidden, or is cosmetic, retaining only the structural identity of the graph (`type` selectors, user-changed values, and variable references). This is the default context the model loads to reason about the whole flowgraph cheaply.
- **Details Mode** (`targets=["block_name"]`) is **dense** for the named blocks: it bypasses the value-based prominence rules and returns *all* visibility-surviving parameters, so the model can read defaults (e.g. `fftsize`, `wintype`) when it actually needs them. Non-targeted blocks stay sparse.
- **Filtered Mode** (`targets=[...]`, `params=[...]`) returns only the requested parameter keys across the targeted blocks.

The model is expected to read the sparse Overview, then drill into specific blocks via Details when a default it omitted is required. This keeps the idle context footprint small without ever losing information the model can fetch on demand.

---

## 2. Response Mode Schema Specifications

### 2.1 Overview Response (`targets=[]`, `params=[]`) — sparse
A hyper-minimal schema. Block parameters are omitted unless they are structural selectors, user-changed, or reference a flowgraph variable. Citations: **[verify_native_api.py:L96-105](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L96-105)**.

Real output for `random_bit_generator.grc` (see [results_native/random_bit_generator_overview.json](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/results_native/random_bit_generator_overview.json)):

```json
{
  "tool": "inspect_graph",
  "ok": true,
  "options": {
    "id": "random_bit_generator",
    "title": "Not titled yet",
    "output_language": "python",
    "generate_options": "qt_gui",
    "realtime_scheduling": ""
  },
  "blocks": {
    "random_bit_generator": {
      "block_type": "options",
      "role": "metadata",
      "parameters": {
        "id": "random_bit_generator",
        "title": "Not titled yet",
        "output_language": "python",
        "generate_options": "qt_gui",
        "realtime_scheduling": ""
      }
    },
    "samp_rate": {
      "block_type": "variable",
      "role": "variable_or_control",
      "parameters": { "id": "samp_rate", "value": "32000" }
    },
    "analog_random_source_x_0": {
      "block_type": "analog_random_source_x",
      "role": "source",
      "parameters": { "type": "byte", "repeat": "True" }
    },
    "blocks_throttle2_0": {
      "block_type": "blocks_throttle2",
      "role": "transform",
      "parameters": { "type": "byte", "samples_per_second": "samp_rate", "limit": "auto" }
    },
    "qtgui_time_sink_x_0": {
      "block_type": "qtgui_time_sink_x",
      "role": "sink",
      "parameters": {
        "type": "float",
        "size": "2048",
        "srate": "samp_rate",
        "grid": "False",
        "autoscale": "False",
        "entags": "True",
        "tr_mode": "qtgui.TRIG_MODE_FREE",
        "tr_slope": "qtgui.TRIG_SLOPE_POS"
      }
    }
  },
  "connections": [
    "analog_random_source_x_0:0->blocks_throttle2_0:0",
    "blocks_char_to_float_0:0->qtgui_time_sink_x_0:0",
    "blocks_throttle2_0:0->blocks_char_to_float_0:0"
  ],
  "validation": { "status": "valid", "errors": [] }
}
```

Note `type` is always present on typed blocks (it is a `dtype == "enum"` structural selector), even when it equals the GRC default — this is the central correctness property of the sparse mode. A block whose parameters are entirely at default (e.g. a type-fixed `blocks_char_to_float`) legitimately renders `"parameters": {}`; its signal direction is recoverable from `connections`.

### 2.2 Details Response (`targets=["block_name"]`) — dense
Targeted blocks return **all** parameters that survive the visibility filters (no value-based prominence filtering). Non-targeted blocks remain in sparse Overview mode. Citations: **[verify_native_api.py:L82-105](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L82-105)** (the prominence block at L96 is gated on `not is_targeted`).

```json
{
  "blocks": {
    "qtgui_time_sink_x_0": {
      "block_type": "qtgui_time_sink_x",
      "role": "sink",
      "parameters": {
        "type": "float",
        "size": "2048",
        "srate": "samp_rate",
        "grid": "False",
        "autoscale": "False",
        "ymin": "-1",
        "ymax": "1",
        "entags": "True",
        "tr_mode": "qtgui.TRIG_MODE_FREE"
      }
    }
  }
}
```

### 2.3 Filtered Response (`targets=["block_name"]`, `params=["param_key"]`)
Returns only the requested parameter keys, after visibility filtering, across the targeted blocks. Citations: **[verify_native_api.py:L107-109](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L107-109)**.

```json
{
  "blocks": {
    "qtgui_time_sink_x_0": {
      "block_type": "qtgui_time_sink_x",
      "role": "sink",
      "parameters": { "type": "float" }
    }
  }
}
```

---

## 3. The Uniform Filter Pipeline

Every parameter of every enabled block is evaluated against one ordered pipeline. There is one rule per stage, applied identically to every case — no per-field allowlists, no per-scenario branches.

### 3.1 Stage A — Visibility filters (applied in every mode)
These drop parameters that are never useful to the model regardless of density.

| # | Rule (drop if) | Native basis | Citation |
|---|---|---|---|
| 1 | `p.hide == "all"` | `Param.hide` (evaluated enum `none`/`part`/`all`) | [L83-85](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L83-85) |
| 2 | `p.category == ADVANCED_PARAM_TAB` | native constant `"Advanced"` | [L86-88](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L86-88) |
| 3 | `p.category == "Config"` | GRC community-convention tab (QT-GUI cosmetic styling) | [L89-91](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L89-91) |
| 4 | `p.dtype == "gui_hint"` | `Param.dtype` (Qt layout positioning) | [L92-94](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L92-94) |

Stages 1, 2, and 4 are pure native-property reads. Stage 3 is a category-level uniform rule; `"Config"` has no native constant (only `"General"` and `"Advanced"` do), so the convention string is the available signal that a parameter is QT-cosmetic. Disabled/bypassed blocks are excluded before this pipeline via `Block.enabled` / `Block.get_bypassed()` ([L72-73](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L72-73)).

### 3.2 Stage B — Prominence filters (Overview / non-targeted blocks only)
A parameter surviving Stage A is retained in the sparse Overview if **any** of:

| # | Rule (keep if) | Native basis | Citation |
|---|---|---|---|
| 5 | `p.is_enum()` | `Param.dtype == "enum"` — structural selector (`type`, `wintype`, …) | [L100](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L100) |
| 6 | `str(p.value) != str(p.default)` | `Param.value` / `Param.default` — user-changed value | [L101](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L101) |
| 7 | value references a flowgraph variable | whole-identifier token match against `Block.is_variable` names | [L102](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L102), helper [L14-17](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L14-17) |

Stage B is **skipped** for targeted blocks in Details Mode, which is what makes Details dense.

**Why `is_enum()` (rule 5) is load-bearing:** the `type` parameter is `hide == "part"`, so it is not always-visible, and at the GRC default it fails rule 6. Without rule 5, `type` would vanish from any block whose type equals the default — silently destroying the signal-schema of the graph. `is_enum()` is the native property shared by every structural selector; keeping all enums is the one uniform rule that guarantees `type` is always emitted. Its accepted cost is that cosmetic boolean enums (`grid`, `autoscale`, …) also leak through; this is preferred over a forbidden per-field allowlist.

**Why the `hide == "none"` rule was removed:** the prior pipeline kept any parameter whose `hide == "none"`. `hide` is a *GUI display* attribute, not a relevance signal, so this retained large amounts of default-valued always-visible noise (`offset`, `phase`, materialized PDU sizes). It has been deleted; rules 5–7 are the only prominence signals.

**Variable-reference matching (rule 7):** the flowgraph value namespace is the set of `Block.is_variable` names, computed once per graph ([L64-65](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L64-65)). A parameter value is tokenized into Python identifiers (`[A-Za-z_]\w*`) and matched as whole tokens — not as substrings — against that set. This replaces the previous O(n²) substring scan over every block, which was both slow and false-positive-prone (e.g. a block named `x` matched any value containing the letter `x`).

### 3.3 Stage C — Explicit key filter (Filtered mode only)
If `params` is provided, only parameters whose key is in `params` survive ([L107-109](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L107-109)). This is tool-argument semantics, not a heuristic.

### 3.4 Options block
There is no options whitelist. The `options` block flows through Stages A and B like every other block; it additionally populates the top-level `options` field ([L119-122](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L119-122)). The previously hand-picked 4-key allowlist (`id`/`title`/`generate_options`/`output_language`) has been deleted; those keys now survive naturally (id/title differ from default; the two enums survive rule 5). Its one accepted side-effect is that an empty default enum such as `realtime_scheduling: ""` also survives rule 5.

---

## 4. Block Role Classification Rules

Block roles are resolved sequentially in `resolve_block_role` ([verify_native_api.py:L19-44](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L19-44)), all from native `Block` properties:

1. **`variable_or_control`**: `block.is_variable == True`.
2. **`import`**: `block.is_import == True`.
3. **`snippet`**: `block.is_snippet == True`.
4. **`virtual_or_pad`**: `block.is_virtual_or_pad == True`.
5. **`metadata`**: options block `block.key == "options"`.
6. **`source`**: output ports present, no input ports (`len(sources) > 0 and len(sinks) == 0`).
7. **`sink`**: input ports present, no output ports.
8. **`transform`**: both input and output ports present.
9. **`metadata`** fallback: neither ports category matches.

Note: message-domain ports (`Connection`/port `domain == "message"`, native `GR_MESSAGE_DOMAIN`) count toward `sinks`/`sources` identically to stream ports, so a message-only block with both a message sink and a message source classifies as `transform`. This is per-spec; surfacing per-port domain/dtype is a future schema consideration, not a classification bug.
