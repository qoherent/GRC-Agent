# Inspect Graph Experiment Output Comparison Analysis

This document compares the JSON structure and contents generated against the native `gnuradio.grc.core` Python API, under the hardened (Fourth Pass) filter pipeline: native-property visibility filtering, enum/value/variable-reference prominence, and the retired options whitelist.

The authoritative rule set is documented in [wire_shape_proposal.md §3](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/wire_shape_proposal.md). This file records the *observed effects* of that pipeline.

---

## 1. Top-Level Structure Comparison

The native API returns a clean, flat JSON structure where parameters are inlined under their respective blocks:

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
    "samp_rate": {
      "block_type": "variable",
      "role": "variable_or_control",
      "parameters": { "id": "samp_rate", "value": "32000" }
    }
  },
  "connections": [
    "analog_random_source_x_0:0->blocks_throttle2_0:0"
  ],
  "validation": { "status": "valid", "errors": [] }
}
```

The top-level `options` field mirrors the `options` block's surviving parameters (same uniform pipeline — see §3).

---

## 2. The Three Modes (Sparse / Dense / Filtered)

### 2.1 Overview Mode (`targets=[]`, `params=[]`) — sparse
Hyper-minimal. Drops defaults so the idle context footprint is small, while **guaranteeing structural selectors (`type`, `wintype`) are always emitted**.

A parameter survives Overview iff it passes the uniform pipeline:
- **Visibility (every mode):** drop `hide == "all"`; drop `category == "Advanced"`; drop `category == "Config"`; drop `dtype == "gui_hint"`.
- **Prominence (Overview only):** keep if `p.is_enum()` OR `value != default` OR value references a flowgraph variable.

Accepted, deliberate trade-offs of this rule set:
- Cosmetic boolean enums (`grid`, `autoscale`, `average`, `tr_mode`, …) leak through rule `is_enum()`. This is the cost of guaranteeing `type` is never dropped without resorting to a forbidden per-field allowlist.
- Meaningful defaults (`fftsize`, `noise_type`) are omitted in Overview. They are recoverable on demand via Details Mode, which exists precisely for this.
- An empty default enum (`realtime_scheduling: ""`) survives in `options`. This is the cost of retiring the hand-picked options whitelist (see §3).

Result for `random_bit_generator.grc`:
- Output: [random_bit_generator_overview.json](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/results_native/random_bit_generator_overview.json)
- **1898 chars** from a **4237-char** raw `.grc` → **55.2% reduction** (~1059 → ~475 tokens, chars/4 estimate).
- `type` is now present on every typed block (`analog_random_source_x_0`, `blocks_throttle2_0`, `qtgui_time_sink_x_0`); previously `type` was dropped whenever it equaled the GRC default.
- `blocks_char_to_float_0` renders `"parameters": {}` — correct: it is a type-fixed char→float transform with no `type` selector and all other params at default; its direction is recoverable from `connections`.

### 2.2 Details Mode (`targets=["qtgui_time_sink_x_0"]`) — dense
Targeted blocks bypass the Stage-B prominence rules and return all visibility-surviving parameters (defaults included). Non-targeted blocks remain sparse.
- Output: [random_bit_generator_details.json](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/results_native/random_bit_generator_details.json)
- The targeted QT sink returns its full non-hidden, non-Config parameter set, including the defaults (`size`, `srate`, `ymin`, `ymax`, …) that Overview omits.

### 2.3 Filtered Mode (`targets=["qtgui_time_sink_x_0"]`, `params=["type"]`)
Only the requested keys, after visibility filtering, across targeted blocks.
- Output: [random_bit_generator_filtered.json](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/results_native/random_bit_generator_filtered.json)
- The targeted QT sink returns only `"type": "float"`.

---

## 3. Options Block — Uniform Rules (whitelist retired)

The `options` block is no longer carved out by a hand-picked key whitelist. It flows through the same Stage A + Stage B pipeline as every other block, and its surviving parameters also populate the top-level `options` field.

The previously whitelisted keys now survive on their own merits:
- `id`, `title` — differ from default → Stage B rule 6.
- `generate_options`, `output_language` — `dtype == "enum"` → Stage B rule 5.

All other GRC options defaults (`author`, `cmake_opt`, `qt_qss_theme`, `gen_linking`, `run_options`, `window_size`, …) are dropped: the hidden ones by `hide == "all"`, the rest by failing Stage B (string/numeric, at default, no variable reference). The sole residual is the empty default enum `realtime_scheduling: ""` (Stage B rule 5), an accepted cost of the uniform rule.

User-set, non-default options metadata (e.g. `author`, `description` when the author actually filled them in, as in `dial_tone.grc`) is now surfaced honestly — the prior whitelist silently discarded it.

---

## 4. Measured Footprint (raw `.grc` → Overview JSON)

Char counts via `wc -m` on the regenerated artifacts in [results_native/](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/results_native/). Token estimate = chars / 4.

| Graph | Raw `.grc` | Overview JSON | Reduction |
|---|---|---|---|
| random_bit_generator | 4237 | 1898 | **55.2 %** |
| 16qam_upgrade | 4192 | 1879 | 55.2 % |
| r2_broken_fixer | 4173 | 2304 | 44.8 % |
| notch_test | 3206 | 2041 | 36.3 % |
| dial_tone | 3278 | 2778 | 15.2 % |
| mac_sniffer | 1548 | 908 | 41.3 % |
| rewire_message_ambiguous | 2404 | 1619 | 32.7 % |
| random_bit_generator_with_unused_var | 4381 | 2075 | 52.6 % |
| random_bit_generator_dual_sink_sink1_disabled | 6072 | 1898 | 68.7 % |

Reduction scales with graph complexity (more blocks/defaults → more stripped). Small graphs (`dial_tone`) compress less because most of their parameters are already user-changed and legitimately retained.
