# Production-Readiness Phase 2 Harness Skeleton

Date: 2026-05-14

Phase 2 adds a deterministic production-evidence harness foundation. It does not optimize GRC Agent behavior, change runtime mutation logic, change tool schemas, change eval scoring, use Ollama for gameplay, or claim production readiness.

## What Was Added

Files:

- `tests/production/corpus_manifest.json`
- `tests/production/scenarios/read_only_explain.json`
- `tests/production/scenarios/set_param_validate.json`
- `tests/production/scenarios/save_load_lifecycle.json`
- `tests/production/gameplay_runner.py`
- `tests/production/gameplay_judge.py`
- `tests/production/ollama_readiness.py`
- `tests/production/test_gameplay_harness.py`

The harness is deliberately separate from runtime code. It replays scripted assistant tool actions through the existing `GrcAgent` tool execution boundary and records evidence artifacts.

## Corpus Manifest

The manifest is deterministic JSON. Each entry records:

- graph id
- source path
- copy-before-load/edit policy
- graph tags
- GNU Radio version when known
- block and connection counts
- stream/message connection tags
- variables
- safe candidate operations
- expected graph-delta categories
- portability notes

The initial manifest includes repo-local fixtures and installed GNU Radio examples. Installed examples are marked as requiring a copy before load or edit.

## Scenarios

Initial scripted scenarios:

| Scenario | Graph | Purpose | Expected tool path |
| --- | --- | --- | --- |
| `read_only_explain` | `fixture_random_bit_generator` | Read-only explanation evidence | `inspect_graph` |
| `set_param_validate` | `fixture_random_bit_generator` | Release-validated parameter mutation on a copy | `change_graph` with `operation_kind=set_param` |
| `save_load_lifecycle` | `fixture_random_bit_generator` | Explicit lifecycle wrapper evidence on a copied graph | `save_graph_explicit`, `load_graph_explicit` |

Scenario files declare forbidden events:

- `raw_legacy_tool_call`
- `raw_yaml_mutation`
- `preview_mutation`
- `failed_validation_commit`
- `unsafe_save`
- `unsafe_load`
- `original_graph_mutation`

## Runner Design

Command example:

```bash
uv run python -m tests.production.gameplay_runner \
  --scenario tests/production/scenarios/read_only_explain.json \
  --artifact /tmp/grc_gameplay_readonly.json
```

Runner responsibilities:

- load a scenario
- resolve the graph from the corpus manifest
- copy the graph to a temporary working directory
- load only the copied graph into `GrcAgent`
- replay scripted user turns and assistant tool actions
- preserve raw requested tool calls
- preserve executed tool results
- collect conversation turns
- collect graph snapshots, graph deltas, validation results, and save/load events
- verify source graph hash before and after
- write a redacted artifact JSON

This is deterministic and does not call an LLM. Scripted assistant actions are evidence-harness inputs, not model behavior proof.

## Judge Design

`tests.production.gameplay_judge` is a deterministic artifact judge. It grades:

- `task_success`
- `runtime_safety_pass`
- `model_contract_pass`
- `graph_delta_pass`
- `validation_pass`
- `clarification_quality_pass`
- `save_load_safety_pass`
- `forbidden_events_count`
- `final_state_pass`

It also detects forbidden events directly from raw traces and graph hashes. The judge is intentionally local and rule-based.

## Artifact Format

Artifact schema: `2026-05-14.phase2-gameplay-artifact-v1`

Top-level fields include:

- `scenario`
- `corpus_entry`
- `paths`
- `ollama_readiness`
- `conversation`
- `turns`
- `initial_graph_snapshot`
- `final_graph_snapshot`
- `graph_delta`
- `validation_results`
- `save_load_events`
- `source_integrity`
- `judge`

Each turn includes:

- `requested_tool_calls_raw`
- `executed_tool_calls_raw`
- `graph_snapshot_before`
- `graph_snapshot_after`
- `graph_delta`
- `validation_results`

Artifacts are intended for `/tmp` or another ignored reports path. They must not contain secrets.

## Ollama Readiness

Phase 2 includes only a readiness helper:

```bash
uv run python -m tests.production.ollama_readiness
```

Default behavior:

- loads `.env`
- checks whether the repo-local `ollama_key` exists
- maps it to `OLLAMA_API_KEY` in process memory only
- performs no network call
- prints only redacted booleans/status

Optional network check exists behind `--check-ollama-cloud`. It is not used for gameplay and is not run by default.

## Local Scenario Evidence

On 2026-05-14 the three scripted scenarios passed:

| Scenario | Artifact | Result | Tool calls | Forbidden events | Graph delta | Validation |
| --- | --- | --- | ---: | ---: | --- | --- |
| `read_only_explain` | `/tmp/grc_gameplay_readonly.json` | pass | 1 | 0 | `{}` | unknown |
| `set_param_validate` | `/tmp/grc_gameplay_set_param.json` | pass | 1 | 0 | `samp_rate=48000`, dirty true, validation valid | valid |
| `save_load_lifecycle` | `/tmp/grc_gameplay_save_load.json` | pass | 2 | 0 | validation status changed to valid; no content delta | valid |

All three artifacts reported original source graph unchanged.

## Current Limitations

- The runner is deterministic and scripted; it does not prove live model behavior.
- There is no Ollama-generated dummy user yet.
- The initial corpus is small.
- The judge is intentionally conservative and currently covers only the initial artifact contract.
- Clarification quality is represented in the judge contract but not deeply exercised by the first three scenarios.
- The harness does not promote beta or diagnostic capabilities.

## Phase 3 Plan

Next work should:

1. Add a copied-graph corpus builder with manifest hash verification.
2. Add multi-turn clarification and correction scenarios.
3. Add deterministic negative gameplay scenarios for raw YAML, unsafe save/load, preview mutation, and failed-validation commit pressure.
4. Add optional Ollama Cloud dummy-user generation after explicit authorization, with local deterministic judging retained.
5. Add artifact bundle redaction tests for larger traces.
6. Add a dashboard over gameplay artifacts without changing current release-dashboard scoring.
7. Define promotion criteria before changing any classification.

## Final Classification

Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`

Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`, `R4A_INSERT_BLOCK_ON_CONNECTION`, `R4B_REMOVE_BLOCK`, `R4C_ADD_VARIABLE`, `R5_SAVE_LOAD`

Diagnostic-clean: `R7_EXACT_EXTERNAL`, `Tier5_ADVERSARIAL`

Diagnostic-partial: `R7_NATURAL_EXTERNAL`

Runtime: not production-ready
