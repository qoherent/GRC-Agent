# Phase 8 Production-Readiness Evidence: Broader Natural Gameplay Measurement

Date: 2026-05-15

Status: measurement only. Runtime behavior, tool schemas, eval scoring, and
alias resolution were not changed. Runtime remains not production-ready.

## Scope

Phase 8 measures Ollama dummy-user natural-language usability across beta
capabilities that were already validated by deterministic and dashboard gates.
Ollama remains only the dummy user. The deterministic local judge remains the
only scorer.

New natural gameplay scenarios:

| Scenario | Capability | Runs | Result |
| --- | --- | ---: | ---: |
| `natural_disconnect` | `R2_DISCONNECT` | 5 | 5/5 |
| `natural_rewire` | `R3_REWIRE` | 5 | 0/5 |
| `natural_add_variable` | `R4C_ADD_VARIABLE` | 5 | 5/5 |
| `natural_insert_block` | `R4A_INSERT_BLOCK_ON_CONNECTION` | 5 | 0/5 |
| `natural_remove_block` | `R4B_REMOVE_BLOCK` | 5 | 5/5 |

Configuration:

- Model: `gemma3:4b`
- Provider: Ollama Cloud
- Temperature: `0.0`
- Seed: `8100`
- Runs: `5` per scenario
- Artifact directory: `/tmp/grc_agent_phase8_ollama`

## Aggregate Results

| Metric | Result |
| --- | ---: |
| Total runs | 25 |
| Overall pass rate | 15/25 |
| Runtime safety rate | 25/25 |
| Model contract rate | 25/25 |
| Forbidden events | 0 |
| Raw legacy attempts | 0 |
| Failed-validation commits | 0 |
| Average turns | 1.0 |
| Average tool calls | 1.56 |
| Average dummy-user latency | 944.92 ms |

Secret scan over `/tmp/grc_agent_phase8_ollama` found no `ollama_key`,
`OLLAMA_API_KEY`, authorization header, bearer token, or API key markers.

## Failure Classification

Requested Phase 8 labels:

| Scenario | Runs failed | Classification | Evidence |
| --- | ---: | --- | --- |
| `natural_rewire` | 5 | `missing arg` | Each run requested `change_graph` with `operation_kind="rewire"` and a `connection_id` describing the desired new connection, but omitted required replacement endpoint fields. No graph delta was committed. |
| `natural_insert_block` | 2 | `missing arg` | Two runs requested `change_graph` with incomplete insert arguments, including missing destination endpoint fields and an inserted block identifier/name mixup. No graph delta was committed. |
| `natural_insert_block` | 3 | `safe refusal` | Three runs formed a structured insert request, but validation rejected the candidate because the inserted throttle block type/parameters produced incompatible IO types. Rollback held and no failed-validation commit occurred. |

Observed zero failures for:

- `dummy_user_underspecified`
- `model no-call`
- `wrong wrapper`
- `target-resolution gap`
- `runtime bug`
- `judge bug`

## Safety Observations

- No raw legacy/internal tool attempts appeared in raw requested tool history.
- No raw YAML mutation requests were executed.
- No preview mutation was detected.
- No failed validation committed.
- No source/original graph mutation was detected.
- Runtime refusals during insert-block failures were safe and non-mutating.

No STOP_THE_LINE issue was found.

## Resolver Recommendation

Phase 8 does not justify adding a new narrow graph-local alias resolver.

The repeated `natural_rewire` failures are not a simple alias problem. The model
selected the correct wrapper and operation, but did not construct the full typed
rewire contract. Fixing that with a phrase mapping would risk becoming topology
planning logic.

The `natural_insert_block` failures split between incomplete arguments and
validation-safe IO type rejection. This points to block-candidate/parameter
selection and clarification ergonomics, not a unique graph-local synonym like
the Phase 7 `sample rate` -> `samp_rate` case.

Recommended next evidence step: create controlled Phase 9 variants for rewire
and insert-block that separate dummy-user specificity, inspect-before-edit
behavior, block candidate selection, and required argument construction before
considering any runtime change.

## Artifacts

- Aggregate report: `/tmp/grc_agent_phase8_ollama/aggregate_report.json`
- Per-run artifacts: `/tmp/grc_agent_phase8_ollama/*_run_*.json`
- Scenario definitions: `tests/production/scenarios_ollama_phase8/`
- Run config: `tests/production/ollama_gameplay_phase8_config.json`

## Final Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`,
  `R4A_INSERT`, `R4B_REMOVE`, `R4C_ADD_VARIABLE`, `R5_SAVE_LOAD`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`,
  `Tier5_ADVERSARIAL`
- Runtime: not production-ready
