# Phase 7 Production-Readiness Evidence: Graph-Local Set Param Alias

Date: 2026-05-15

Status: evidence improvement only. Runtime remains not production-ready.

## Scope

Phase 7 adds deterministic, graph-local target resolution for one natural target
gap found in Phase 6:

- Operation: `change_graph` with `operation_kind="set_param"` only.
- Target class: existing variable block only.
- Alias family: `sample rate`, `sample_rate`, `samp rate`.
- Resolved target: variable instance `samp_rate`, parameter key `value`.
- Resolution preconditions:
  - user supplied an explicit value;
  - active graph has exactly one sample-rate-like variable;
  - that unique variable is named `samp_rate`;
  - no explicit conflicting target or parameter key was supplied.

The resolver does not infer values, does not consult docs/RAG, does not use
tutorials as mutation authority, does not add retries, and does not bypass the
existing schema validation, preflight, `grcc`, rollback, or checkpoint path.

## Implementation

Changed files:

- `src/grc_agent/runtime/target_aliases.py`
- `src/grc_agent/runtime/wrappers/change_graph/dispatcher.py`
- `tests/test_mvp_tool_profile.py`
- `docs/PRODUCTION_READINESS_PHASE7.md`

The dispatcher invokes the resolver before existing change-graph argument
validation. If the resolver can prove a unique graph-local target, it rewrites
the target fields to:

```json
{"instance_name": "samp_rate", "param_key": "value"}
```

If the alias is missing a value, absent from the graph, ambiguous, or conflicts
with an explicit target, the dispatcher returns a normal non-mutating
`clarification_required` result.

Tool results include:

```json
{
  "resolved_target_alias": {
    "alias_text": "sample rate",
    "resolved_to": {"instance_name": "samp_rate", "param_key": "value"},
    "reason": "unique_samp_rate_variable",
    "ambiguity_count": 1,
    "source": "graph_local_alias"
  }
}
```

## Deterministic Tests

Added direct wrapper tests for:

- `Change the sample rate to 48000` resolves to `samp_rate.value` when unique.
- `Change the sample rate` without a value clarifies without mutation.
- No `samp_rate` variable clarifies without mutation.
- Multiple sample-rate-like variables clarify without mutation.
- Explicit target conflict clarifies without mutation.
- Preview wording remains dry-run and does not mutate.
- Exact `samp_rate` calls keep prior behavior and do not emit alias telemetry.
- Resolver does not call docs/RAG and does not expose raw YAML mutation.

Targeted test result:

```text
uv run python -m unittest \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_change_graph_resolves_unique_sample_rate_alias_to_variable_value \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_change_graph_sample_rate_alias_without_value_clarifies_without_mutation \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_change_graph_sample_rate_alias_without_samp_rate_clarifies \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_change_graph_sample_rate_alias_ambiguous_variables_clarifies \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_change_graph_sample_rate_alias_conflicting_target_clarifies \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_change_graph_sample_rate_alias_preview_does_not_mutate \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_change_graph_exact_samp_rate_behavior_is_unchanged \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_change_graph_sample_rate_alias_uses_no_docs_or_raw_yaml_path

Ran 8 tests in 41.197s
OK
```

Production harness tests:

```text
uv run python -m unittest tests.production
Ran 25 tests in 123.190s
OK
```

## Phase 6 Variant Rerun

Command:

```text
uv run python -m tests.production.gameplay_runner \
  --config tests/production/ollama_gameplay_phase6_config.json \
  --scenario-dir tests/production/scenarios_ollama_phase6 \
  --artifact-dir /tmp/grc_agent_phase7_phase6_ollama \
  --enable-ollama-network
```

Results:

| Variant | Result | Mutation | Alias telemetry | Forbidden events |
| --- | ---: | ---: | ---: | ---: |
| `set_param_underspecified` | 5/5 pass | 0/5 | 0/5 | 0 |
| `set_param_natural_actionable` | 5/5 pass | 5/5 | 5/5 | 0 |
| `set_param_exact_actionable` | 5/5 pass | 5/5 | 0/5 | 0 |

Aggregate:

- Total runs: 15
- Overall pass rate: 1.0
- Runtime safety rate: 1.0
- Model contract rate: 1.0
- Raw legacy attempts: 0
- Failed-validation commits: 0
- Artifact directory: `/tmp/grc_agent_phase7_phase6_ollama`
- Secret scan for `ollama_key`, `OLLAMA_API_KEY`, and `Authorization`: no matches.

## Live Dashboard Rerun

Artifacts:

- Result stores and per-suite dashboards: `/tmp/grc_agent_phase7_dashboards`
- Combined dashboard: `/tmp/grc_agent_phase7_dashboards/combined_dashboard.json`

Dashboard summary:

| Suite | Runs | Dashboard |
| --- | ---: | --- |
| `R0_READ_ONLY` | 42/42 | ready |
| `R1_SET_PARAM_ONLY` | 6/6 | ready |
| `R1_SET_STATE` | 9/9 | ready |
| `R2_DISCONNECT` | 15/15 | ready |
| `R3_REWIRE` | 21/21 | ready |
| `R4A_INSERT` | 15/15 | ready |
| `R4B_REMOVE` | 21/21 | ready |
| `R4C_ADD_VARIABLE` | 15/15 | ready |
| `R5_SAVE_LOAD` | 15/15 | ready |
| `R7_EXACT_EXTERNAL` | 27/27 | ready |
| `R7_NATURAL_EXTERNAL` | 27/27 | ready |
| `Tier5_ADVERSARIAL` | 54/54 | ready |

Combined dashboard:

- `release_ready`: true for the existing dashboard policy.
- Missing required phases: none.
- Unstable cases: none.
- Diagnostic unstable cases: none.
- Raw legacy tool entries: 0.
- Raw tool history errors: 0.
- Capability statuses remain:
  - Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
  - Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`,
    `R4A_INSERT`, `R4B_REMOVE`, `R4C_ADD_VARIABLE`, `R5_SAVE_LOAD`
  - Diagnostic-clean: `R7_EXACT_EXTERNAL`, `Tier5_ADVERSARIAL`
  - Diagnostic-partial: `R7_NATURAL_EXTERNAL`

`R7_NATURAL_EXTERNAL` passed this Phase 7 run, but it remains
diagnostic-partial until broader natural-language coverage is defined and run
as promotion evidence.

## Limitations

- Only the `samp_rate` variable alias is supported.
- The resolver does not resolve arbitrary block parameters such as throttle
  `samples_per_second` or QT sink `srate`.
- Ambiguous sample-rate-like variables fail closed.
- Missing values fail closed.
- This is not a planner, not a hidden retry, and not broad natural-language
  understanding.

## Phase 8 Recommendation

Do not expand aliases yet. Next evidence should measure whether other natural
target failures repeat across unrelated copied graphs. If a repeated generic
gap appears, prefer advisor/context improvements or graph-local typed candidate
selection over phrase dictionaries.

## Final Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`,
  `R4A_INSERT`, `R4B_REMOVE`, `R4C_ADD_VARIABLE`, `R5_SAVE_LOAD`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `Tier5_ADVERSARIAL`
- Diagnostic-partial: `R7_NATURAL_EXTERNAL`
- Runtime: not production-ready
