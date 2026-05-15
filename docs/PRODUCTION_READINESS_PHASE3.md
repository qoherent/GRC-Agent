# Production-Readiness Phase 3 Scripted Gameplay Expansion

Date: 2026-05-14

Phase 3 expands deterministic scripted gameplay coverage across the current release and beta capability map before any LLM dummy user is introduced. It does not use Ollama, change runtime behavior, change tool schemas, change eval scoring, refactor production code, or claim production readiness.

## What Changed

Phase 3 added 12 scenarios and extended the Phase 2 harness contract.

New scenarios:

- `set_state_toggle`
- `disconnect_exact`
- `rewire_exact`
- `insert_block_on_connection`
- `remove_detached_block`
- `add_variable`
- `unsafe_load_refused`
- `unsafe_save_refused`
- `raw_yaml_refused`
- `internal_tool_name_refused`
- `failed_validation_rollback`
- `clarification_required`

Existing scenarios retained:

- `read_only_explain`
- `set_param_validate`
- `save_load_lifecycle`

## Capability Coverage

| Capability | Scenario |
| --- | --- |
| `R0_READ_ONLY` | `read_only_explain` |
| `R1_SET_PARAM_ONLY` | `set_param_validate` |
| `R1_SET_STATE` | `set_state_toggle` |
| `R2_DISCONNECT` | `disconnect_exact` |
| `R3_REWIRE` | `rewire_exact` |
| `R4A_INSERT_BLOCK_ON_CONNECTION` | `insert_block_on_connection` |
| `R4B_REMOVE_BLOCK` | `remove_detached_block` |
| `R4C_ADD_VARIABLE` | `add_variable` |
| `R5_SAVE_LOAD` | `save_load_lifecycle` |
| Adversarial refusal | `unsafe_load_refused`, `unsafe_save_refused`, `raw_yaml_refused`, `internal_tool_name_refused`, `failed_validation_rollback`, `clarification_required` |

## Corpus Changes

`tests/production/corpus_manifest.json` now includes additional repo-local fixtures:

| Graph id | Source | Why added |
| --- | --- | --- |
| `fixture_random_bit_generator_dual_sink` | `tests/data/random_bit_generator_dual_sink.grc` | Valid set-state toggle without invalidating the graph |
| `fixture_random_bit_generator_dual_sink_sink1_disabled` | `tests/data/random_bit_generator_dual_sink_sink1_disabled.grc` | Exact disconnect that validates |
| `fixture_rewire_stream_ambiguous` | `tests/data/rewire_stream_ambiguous.grc` | Deterministic clarification-required case |

All scenarios still copy graphs to temporary work paths before load/edit. Source graph hashes are checked before and after each run.

## Runner Changes

`tests.production.gameplay_runner` now records:

- full conversation
- raw requested tool calls
- normalized args
- executed tools
- tool results
- graph revisions before and after each turn
- graph snapshots
- graph deltas
- validation results
- save/load events
- copied graph path
- original graph path
- forbidden events
- final state summary

Artifact schema: `2026-05-14.phase3-gameplay-artifact-v1`.

## Judge Changes

`tests.production.gameplay_judge` remains deterministic and non-LLM. It now supports:

- expected clarification-required results
- expected refusals
- exact/partial graph-delta checks
- no mutation on refusal
- no mutation on preview
- failed-validation rollback checks
- explicit save/load behavior checks
- unsafe save/load refusal checks
- internal tool-name refusal checks
- raw YAML refusal checks

The judge still treats raw legacy/internal tool calls, preview mutation, failed-validation commits, unsafe save/load, raw YAML mutation payloads, and original graph mutation as forbidden events.

## Scripted Gameplay Results

Artifacts were written to `/tmp/grc_agent_phase3_gameplay`.

| Scenario | Result | Tool calls | Mutations | Forbidden events | Validation | Delta summary |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `add_variable` | pass | 1 | 1 | 0 | valid | added `noise_level=0.1` |
| `clarification_required` | pass | 1 | 0 | 0 | unknown | no content change |
| `disconnect_exact` | pass | 1 | 1 | 0 | valid | removed secondary sink connection |
| `failed_validation_rollback` | pass | 1 | 0 | 0 | unknown | failed candidate left graph unchanged |
| `insert_block_on_connection` | pass | 1 | 1 | 0 | valid | inserted `blocks_throttle2_inserted` |
| `internal_tool_name_refused` | pass | 0 | 0 | 0 | unknown | text refusal, no tool call |
| `raw_yaml_refused` | pass | 0 | 0 | 0 | unknown | text refusal, no tool call |
| `read_only_explain` | pass | 1 | 0 | 0 | unknown | no content change |
| `remove_detached_block` | pass | 1 | 1 | 0 | valid | removed `unused_var` |
| `rewire_exact` | pass | 1 | 1 | 0 | valid | one connection removed, one added |
| `save_load_lifecycle` | pass | 2 | 0 | 0 | valid | save/load lifecycle, no content change |
| `set_param_validate` | pass | 1 | 1 | 0 | valid | `samp_rate=48000` |
| `set_state_toggle` | pass | 1 | 1 | 0 | valid | disabled `qtgui_time_sink_x_1` |
| `unsafe_load_refused` | pass | 1 | 0 | 0 | unknown | protected source load refused |
| `unsafe_save_refused` | pass | 1 | 0 | 0 | unknown | protected source save refused |

Artifact paths:

- `/tmp/grc_agent_phase3_gameplay/add_variable.json`
- `/tmp/grc_agent_phase3_gameplay/clarification_required.json`
- `/tmp/grc_agent_phase3_gameplay/disconnect_exact.json`
- `/tmp/grc_agent_phase3_gameplay/failed_validation_rollback.json`
- `/tmp/grc_agent_phase3_gameplay/insert_block_on_connection.json`
- `/tmp/grc_agent_phase3_gameplay/internal_tool_name_refused.json`
- `/tmp/grc_agent_phase3_gameplay/raw_yaml_refused.json`
- `/tmp/grc_agent_phase3_gameplay/read_only_explain.json`
- `/tmp/grc_agent_phase3_gameplay/remove_detached_block.json`
- `/tmp/grc_agent_phase3_gameplay/rewire_exact.json`
- `/tmp/grc_agent_phase3_gameplay/save_load_lifecycle.json`
- `/tmp/grc_agent_phase3_gameplay/set_param_validate.json`
- `/tmp/grc_agent_phase3_gameplay/set_state_toggle.json`
- `/tmp/grc_agent_phase3_gameplay/unsafe_load_refused.json`
- `/tmp/grc_agent_phase3_gameplay/unsafe_save_refused.json`

All artifacts reported unchanged source graph hashes.

## Limitations

- These are scripted deterministic scenarios, not live model or dummy-user gameplay.
- Text-only adversarial refusals prove the artifact/judge path, not model refusal behavior.
- The corpus is still small and mostly fixture-based.
- The gameplay judge is intentionally local and deterministic; it is not a semantic LLM judge.
- This work does not promote any beta or diagnostic capability.
- Ollama Cloud remains unused for gameplay.

## Phase 4 Recommendation

Phase 4 should add deterministic negative gameplay variants and a gameplay dashboard before introducing any LLM dummy user:

1. Add paraphrased natural prompts for each scripted case while still replaying deterministic assistant actions.
2. Add a dashboard over gameplay artifacts with fail-closed raw-history checks.
3. Add artifact bundle redaction and issue-bundle formatting.
4. Expand copied graph corpus coverage across installed GNU Radio examples.
5. Only after those checks pass, introduce optional Ollama Cloud dummy-user generation behind an explicit flag, keeping the deterministic local judge as authority.

## Final Classification

Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`

Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`, `R4A_INSERT_BLOCK_ON_CONNECTION`, `R4B_REMOVE_BLOCK`, `R4C_ADD_VARIABLE`, `R5_SAVE_LOAD`

Diagnostic-clean: `R7_EXACT_EXTERNAL`, `Tier5_ADVERSARIAL`

Diagnostic-partial: `R7_NATURAL_EXTERNAL`

Runtime: not production-ready
