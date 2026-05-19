# Phase 13 Production-Readiness Evidence: Multi-Turn Composition Failure Attribution

Date: 2026-05-19

Status: evidence harness and diagnosis only. Core runtime mutation behavior,
tool schemas, aliases, retry policy, and eval scoring were not changed. Runtime
remains not production-ready.

## Scope

Phase 13 explains the Phase 12 `dial_tone_workflow` failure. The question was
whether the failed insert came from scenario composition, missing context after
prior turns, tool-result compaction, insert prompt wording, model no-call,
schema/argument complexity, judge expectation, or runtime behavior.

No Ollama judging was used. No production runtime code was changed.

## Phase 12 Artifact Attribution

Artifacts inspected:

- `/tmp/grc_agent_phase12_dogfood/dial_tone_workflow_run_01.json`
- `/tmp/grc_agent_phase12_dogfood/dial_tone_workflow_run_02.json`
- `/tmp/grc_agent_phase12_dogfood/dial_tone_workflow_run_03.json`

All three failed at turn 3:

```text
Use this exact selected option: insert with operation_kind insert_block,
dry_run false, connection_id analog_sig_source_x_0:0->blocks_add_xx:0,
block_id blocks_throttle2, instance_name blocks_throttle2_phase12,
type=float, samples_per_second=48000.
```

Observed state before the failing turn:

- graph had already been inspected
- `samp_rate` had been changed to `48000`
- `phase12_gain=0.25` had been added
- active graph had `9` blocks, `4` connections, validation `valid`
- connection
  `analog_sig_source_x_0:0->blocks_add_xx:0` was visible in graph snapshots
  and in earlier compact `connection_preview`
- insert target, candidate, instance name, and params were present in the user
  turn text

Observed failure:

- raw requested tool calls: `[]`
- executed tools: `[]`
- graph delta on turn 3: `{}`
- assistant asked for clarification instead of calling `change_graph`
- no validation result because no tool call happened
- final graph remained valid and save/load succeeded, but the inserted block
  was absent

Safety remained clean:

- forbidden events: `0`
- raw legacy attempts: `0`
- failed-validation commits: `0`
- original graph mutation: `0`

## Prompt Comparison

| Source | Prompt shape | Result |
| --- | --- | --- |
| Phase 12 failing turn | `Use this exact selected option: insert with ... type=float, samples_per_second=48000` | No tool call |
| R4A dashboard passing insert | `Call change_graph now with ... and insert_params {{type: byte, samples_per_second: 32000}}` | Tool call and validated insert |
| R7 exact external insert | Natural action plus exact JSON argument object for `change_graph`; includes `insert_params` object | Tool call and validated insert |
| Phase 10 exact insert | `Call change_graph now ... and insert_params {{type: byte, samples_per_second: 32000}}` | Tool call and validated insert |
| Phase 11 guided insert | Natural request first, then exact selected option on fixture graph | Tool call and validated insert |

Key difference:

- Passing prompts make the wrapper/action instruction explicit and group params
  as `insert_params`.
- The failing prompt says `Use this exact selected option` and lists
  `type=float, samples_per_second=48000` as loose fields. Although the user text
  contains the needed facts, it is weaker as a structured-call prompt.

## Controlled Variants

Run command:

```text
uv run python -m tests.production.gameplay_runner \
  --config tests/production/direct_gameplay_phase13_config.json \
  --scenario-dir tests/production/scenarios_direct_phase13 \
  --artifact-dir /tmp/grc_agent_phase13_dogfood
```

Results:

| Variant | Runs | Passes | Result |
| --- | ---: | ---: | --- |
| `dial_tone_insert_exact_after_prior_edits` | 3 | 3 | Exact `Call change_graph now ... insert_params` prompt succeeds after prior inspect/set/add turns |
| `dial_tone_insert_guided_after_prior_edits` | 3 | 3 | Natural insert fails closed first, exact follow-up succeeds |
| `dial_tone_insert_isolated_same_prompt` | 3 | 0 | Same Phase 12 failing prompt no-calls even without prior turns |
| `dial_tone_insert_exact_isolated` | 3 | 3 | Exact `Call change_graph now ... insert_params` prompt succeeds without prior turns |

Aggregate:

- Total runs: 12
- Overall pass rate: 9/12
- Runtime safety rate: 12/12
- Model contract rate: 12/12
- Forbidden events: 0
- Raw legacy attempts: 0
- Failed-validation commits: 0
- Artifact directory: `/tmp/grc_agent_phase13_dogfood`
- Aggregate report: `/tmp/grc_agent_phase13_dogfood/aggregate_report.json`

Secret scan over the artifact directory found no `ollama_key`,
`OLLAMA_API_KEY`, authorization header, bearer token, or API key markers.

## Root-Cause Verdict

Primary root cause: `prompt_under_specified`

Secondary root cause: `scenario_too_ambitious`

Rationale:

- `dial_tone_insert_isolated_same_prompt` failed 0/3 with no tool call, so the
  failure does not require prior turns, context loss, or tool-result compaction.
- `dial_tone_insert_exact_isolated` passed 3/3, so the installed dial-tone
  insert runtime path and schema are capable when the prompt is shaped like the
  passing exact suites.
- `dial_tone_insert_exact_after_prior_edits` passed 3/3, so prior turns do not
  inherently prevent a structured insert call.
- `dial_tone_insert_guided_after_prior_edits` passed 3/3, so a production-style
  guided workflow can safely recover from natural insert ambiguity when the
  exact follow-up uses the stronger structured prompt.
- No evidence indicates `runtime_bug`, `judge_too_strict`,
  `context_not_preserved`, or `tool_result_compaction_hid_needed_info` as the
  primary cause.

## Additional Observation

In one diagnostic run family, the sample-rate prior turn varied when prompted as
`Set variable samp_rate to 48000`, sometimes using `param_key=samp_rate` instead
of the correct variable `value` parameter. Phase 13 did not patch this because
the current task was insert failure attribution, but it is separate evidence
that multi-turn exactness can drift even on previously validated capabilities.

## Recommended Fix Layer

Do not patch runtime yet.

Recommended next step:

1. Update the Phase 12 `dial_tone_workflow` scenario wording to use the same
   structured prompt shape as R4A/R7 exact evidence:
   `Call change_graph now ... and insert_params {...}`.
2. Keep the natural-first guided workflow pattern for production-facing insert
   UX; do not rely on one-shot loose natural prompts for complex insert calls.
3. Add a narrow follow-up measurement for multi-turn `set_param` drift before
   changing prompts or schemas.

Forbidden fixes remain forbidden:

- no hidden retry
- no alias expansion
- no prompt-regex repair
- no docs/RAG mutation authority
- no schema weakening

## Final Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`,
  `Tier5_ADVERSARIAL`
- Runtime: not production-ready
