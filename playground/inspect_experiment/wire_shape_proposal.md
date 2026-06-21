# Wire Shape Proposal for `inspect_graph`

This document defines the formalized target JSON wire shape for the rewritten model-facing `inspect_graph` tool, along with the rules for parameter filtering and block role classification. Every field is mapped to its exact line number implementation in the verified native API script [verify_native_api.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py).

---

## 1. Target JSON Structure

The native API produces the following flat structure, avoiding legacy nesting under a `graph` key and inlining parameters under their respective blocks:

```json
{
  "tool": "inspect_graph",
  "ok": true,
  "options": {
    "output_language": "python",
    "generate_options": "qt_gui"
  },
  "blocks": {
    "samp_rate": {
      "block_type": "variable",
      "role": "variable_or_control",
      "parameters": {
        "id": "samp_rate",
        "value": "32000"
      }
    }
  },
  "connections": [
    "analog_random_source_x_0:0->blocks_throttle2_0:0"
  ],
  "validation": {
    "status": "valid",
    "errors": []
  }
}
```

### Citations for Output Fields:
- **`tool`**: Verbatim `"inspect_graph"`. Citations: **[verify_native_api.py:L109](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L109)**.
- **`ok`**: Boolean execution status. Citations: **[verify_native_api.py:L110](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L110)**.
- **`options`**: Dict of flowgraph-wide option block values. Citations: **[verify_native_api.py:L55-61](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L55-61)**.
- **`blocks`**: Map of enabled blocks keyed by instance name. Citations: **[verify_native_api.py:L63-89](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L63-89)**.
  - **`block_type`**: The block's native GRC key. Citations: **[verify_native_api.py:L85](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L85)**.
  - **`role`**: The block classification role string. Citations: **[verify_native_api.py:L83](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L83)** (resolving via `resolve_block_role`).
  - **`parameters`**: Key-value map of filtered block parameters. Citations: **[verify_native_api.py:L73-81](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L73-81)**.
- **`connections`**: List of enabled source-to-sink connections. Citations: **[verify_native_api.py:L90-106](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L90-106)**.
- **`validation`**: GRC validation status. Citations: **[verify_native_api.py:L114-117](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L114-117)**.
  - **`status`**: `"valid"` or `"invalid"` via topological checks. Citations: **[verify_native_api.py:L115](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L115)**.
  - **`errors`**: Full list of compile-time GRC error strings. Citations: **[verify_native_api.py:L116](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L116)**.

---

## 2. Strict Parameter Visibility Filter

Parameters are filtered strictly using native properties to avoid the fragility of ad-hoc string regex blacklists:

1. **`hide == "all"`**: Excludes parameters hidden under GRC visibility rules. Citations: **[verify_native_api.py:L75-76](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L75-76)**.
2. **`category in {ADVANCED_PARAM_TAB, "Config"}`**: Excludes advanced parameters and configuration metadata tabs. Citations: **[verify_native_api.py:L77-78](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L77-78)**.

---

## 3. Role Classification Rules

Block roles are resolved sequentially in the `resolve_block_role` function:

1. **`variable_or_control`**: Resolved via block attributes if `block.is_variable` is `True`. Citations: **[verify_native_api.py:L12-13](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L12-13)**.
2. **`import`**: Resolved if `block.is_import` is `True`. Citations: **[verify_native_api.py:L14-15](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L14-15)**.
3. **`snippet`**: Resolved if `block.is_snippet` is `True`. Citations: **[verify_native_api.py:L16-17](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L16-17)**.
4. **`virtual_or_pad`**: Resolved if `block.is_virtual_or_pad` is `True`. Citations: **[verify_native_api.py:L18-19](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L18-19)**.
5. **`metadata`**: Assigned if `block.key` is `"options"`. Citations: **[verify_native_api.py:L22-23](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L22-23)**.
6. **`source`**: Assigned if the block has output ports but no input ports. Citations: **[verify_native_api.py:L29-30](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L29-30)**.
7. **`sink`**: Assigned if the block has input ports but no output ports. Citations: **[verify_native_api.py:L31-32](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L31-32)**.
8. **`transform`**: Assigned if the block has both input and output ports. Citations: **[verify_native_api.py:L33-34](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L33-34)**.
9. **`metadata` fallback**: Default if none of the above are matched. Citations: **[verify_native_api.py:L35](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/inspect_experiment/verify_native_api.py#L35)**.
