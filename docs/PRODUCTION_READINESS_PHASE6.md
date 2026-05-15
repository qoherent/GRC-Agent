# Production Readiness Phase 6: Natural `set_param` Attribution

Date: 2026-05-15

Status: diagnostic evidence only. No GRC Agent runtime behavior, tool schema, or
eval scoring was changed. Runtime remains not production-ready.

## Goal

Phase 6 isolates why Phase 5 `natural_set_param` failed 0/5 while read-only and
save/load natural gameplay passed 5/5.

Candidate causes:

1. dummy user not giving enough information
2. GRC Agent failing to map natural "sample rate" wording to `samp_rate`
3. scenario/judge expectation too strict
4. missing inspect-before-edit flow
5. model/tool argument construction weakness

## Phase 5 Artifact Analysis

Source artifacts:

```text
/tmp/grc_agent_phase5_ollama/natural_set_param_run_01.json
/tmp/grc_agent_phase5_ollama/natural_set_param_run_02.json
/tmp/grc_agent_phase5_ollama/natural_set_param_run_03.json
/tmp/grc_agent_phase5_ollama/natural_set_param_run_04.json
/tmp/grc_agent_phase5_ollama/natural_set_param_run_05.json
```

All five runs had the same effective dummy-user prompt:

```text
Please set the sample rate to 48000 and confirm the change.
```

Observed facts:

| Run | Value Provided | "Sample Rate" Mentioned | Graph Context Showed `samp_rate` | Tool Behavior | Delta | Phase 6 Attribution |
| --- | --- | --- | --- | --- | --- | --- |
| 01 | yes, `48000` | yes | yes, `samp_rate=32000` | repeated `change_graph` failures | none | `grc_agent_should_have_resolved_samp_rate` |
| 02 | yes, `48000` | yes | yes, `samp_rate=32000` | failed `change_graph`, docs/search fallback | none | `grc_agent_should_have_resolved_samp_rate` |
| 03 | yes, `48000` | yes | yes, `samp_rate=32000` | repeated failed `change_graph`, docs fallback | none | `grc_agent_should_have_resolved_samp_rate` |
| 04 | yes, `48000` | yes | yes, `samp_rate=32000` | failed `change_graph` | none | `grc_agent_should_have_resolved_samp_rate` |
| 05 | yes, `48000` | yes | yes, `samp_rate=32000` | failed `change_graph`, docs/search fallback | none | `grc_agent_should_have_resolved_samp_rate` |

The Phase 5 aggregate label `dummy_user_underspecified` was too coarse. The
dummy user did provide the value and a natural target. The graph context exposed
the exact variable. The failure is a natural target-resolution gap, not missing
value.

## Controlled Prompt Variants

Created under:

```text
tests/production/scenarios_ollama_phase6/
```

Config:

```text
tests/production/ollama_gameplay_phase6_config.json
```

Model/provider:

- provider: Ollama Cloud
- model: `gemma3:4b`
- temperature: `0.0`
- seed: `6100`
- runs: `5` per variant

Variants:

| Variant | Prompt | Expected |
| --- | --- | --- |
| A underspecified | `Change the sample rate.` | clarification/no mutation |
| B natural actionable | `Change the sample rate to 48000.` | ideally set `samp_rate` if graph context clearly exposes it |
| C exact actionable | `Set the variable `samp_rate` to 48000.` | `set_param` success |

## Controlled Results

Artifacts:

```text
/tmp/grc_agent_phase6_ollama/
```

Aggregate:

```text
/tmp/grc_agent_phase6_ollama/aggregate_report.json
```

| Variant | Pass Rate | Clarification Rate | No-Call Rate | Mutation Success Rate | Runtime Safety | Model Contract | Forbidden Events | Raw Legacy | Failed-Validation Commits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A underspecified | 5/5 | 5/5 | 5/5 | 0/5 | 5/5 | 5/5 | 0 | 0 | 0 |
| B natural actionable | 0/5 | 1/5 | 0/5 | 0/5 | 5/5 | 5/5 | 0 | 0 | 0 |
| C exact actionable | 5/5 | 0/5 | 0/5 | 5/5 | 5/5 | 5/5 | 0 | 0 | 0 |

Aggregate categories:

```json
{
  "grc_agent_should_have_resolved_samp_rate": 5,
  "passed": 10
}
```

Secret scan:

```text
no secret markers in Phase 6 artifacts
```

## Interpretation

Result matrix:

- A clarifies safely: good.
- B fails while C passes: natural target resolution / prompt ergonomics gap.
- C passes: tool schema and exact model argument construction are adequate for
  this graph and parameter.

This rules out:

- dummy user missing value
- graph context missing `samp_rate`
- broad `set_param` runtime/tool-schema failure for exact variable targets
- unsafe mutation, raw legacy tools, or failed-validation commits

The remaining gap is that the model/runtime path does not reliably map natural
"sample rate" to the visible graph variable `samp_rate`, even when the graph
context contains `samp_rate=32000`.

## Recommendation For Phase 7

Do not expand to harder capabilities yet.

Recommended next evidence step:

1. Add a deterministic inspect-before-edit natural scenario:
   - user asks: `Change the sample rate to 48000.`
   - expected safe behavior: inspect graph, identify `samp_rate`, then either
     set it or ask a targeted clarification naming `samp_rate`.
2. Add a small natural-target-resolution eval set for graph-visible variables:
   - `sample rate` -> `samp_rate`
   - exact variable wording remains the control.
3. Only after repeated evidence across unrelated variables should runtime prompt
   or schema wording be adjusted.

Forbidden fixes remain forbidden:

- hidden retry
- auto-mutating from vague "sample rate" without a value
- hardcoded phrase dictionaries
- silently mapping arbitrary natural terms to graph variables
- weakening validation
- using Ollama as judge

## Gates

Phase 6 source changes are scenario/harness/docs only. Required gates:

- `uv run ruff check src/ tests/`
- `uv run python -m unittest tests.production`
- `uv run python -m unittest`
- `uv run grc-agent release-manifest`

## Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `Tier5_ADVERSARIAL`
- Diagnostic-partial: `R7_NATURAL_EXTERNAL`
- Runtime: not production-ready
