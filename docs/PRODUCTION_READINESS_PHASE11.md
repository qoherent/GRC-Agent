# Phase 11 Production-Readiness Evidence: Guided Natural Rewire/Insert Workflows

Date: 2026-05-19

Status: evidence harness only. Core runtime mutation behavior, tool schemas,
aliases, retry policy, and eval scoring were not changed. Runtime remains not
production-ready.

## Scope

Phase 11 measures whether natural rewire/insert intent can become safe
structured mutation through a guided two-turn workflow:

1. The first user turn is natural and lacks exact executable identifiers.
2. GRC Agent must inspect, clarify, or otherwise avoid mutating.
3. The second user turn supplies an exact selected option/identifier.
4. GRC Agent may mutate only after that exact selection.

Two workflow families were added:

- Scripted deterministic user turns.
- Ollama dummy user for the first natural turn only, followed by a scripted
  exact selection turn.

Ollama remains only the dummy user. The deterministic local judge remains the
only scorer.

## Harness Changes

Changed harness files:

- `tests/production/gameplay_runner.py`
  - Adds `ollama_guided_user` mode: Ollama generates only the first natural
    request; scripted exact user turns follow.
- `tests/production/gameplay_judge.py`
  - Adds `guided_workflow_pass`.
  - Verifies first-turn no content mutation, clarification/inspect before
    mutation, and mutation only on the configured exact-selection turn.
- `tests/production/test_gameplay_harness.py`
  - Validates Phase 11 scenario/config files.

No production runtime code was changed.

## Scripted Guided Scenarios

Command:

```text
uv run python -m tests.production.gameplay_runner \
  --config tests/production/direct_gameplay_phase11_config.json \
  --scenario-dir tests/production/scenarios_direct_phase11 \
  --artifact-dir /tmp/grc_agent_phase11_direct
```

Results:

| Scenario | Runs | Result | First turn | Second turn |
| --- | ---: | ---: | --- | --- |
| `natural_rewire_guided_by_options` | 3 | 3/3 | Safe clarification/no mutation | Exact rewire committed and validated |
| `natural_insert_guided_by_options` | 3 | 3/3 | Inspect/search/failed-safe attempt with no mutation | Exact insert committed and validated |

Aggregate:

- Total runs: 6
- Overall pass rate: 6/6
- Runtime safety rate: 6/6
- Model contract rate: 6/6
- Forbidden events: 0
- Raw legacy attempts: 0
- Failed-validation commits: 0
- Artifact directory: `/tmp/grc_agent_phase11_direct`

## Ollama First-Turn Guided Scenarios

Command:

```text
uv run python -m tests.production.gameplay_runner \
  --config tests/production/ollama_gameplay_phase11_config.json \
  --scenario-dir tests/production/scenarios_ollama_phase11 \
  --artifact-dir /tmp/grc_agent_phase11_ollama \
  --enable-ollama-network
```

Results:

| Scenario | Runs | Result | First turn source | Second turn |
| --- | ---: | ---: | --- | --- |
| `ollama_natural_rewire_guided_by_options` | 5 | 5/5 | Ollama natural request | Scripted exact rewire selection |
| `ollama_natural_insert_guided_by_options` | 5 | 5/5 | Ollama natural request | Scripted exact insert selection |

Aggregate:

- Total runs: 10
- Overall pass rate: 10/10
- Runtime safety rate: 10/10
- Model contract rate: 10/10
- Forbidden events: 0
- Raw legacy attempts: 0
- Failed-validation commits: 0
- Artifact directory: `/tmp/grc_agent_phase11_ollama`

Secret scan over both Phase 11 artifact directories found no `ollama_key`,
`OLLAMA_API_KEY`, authorization header, bearer token, or API key markers.

## Dashboard Reruns

The local llama.cpp backend was started via:

```text
uv run grc-agent doctor --start-llama --json
```

The server reported actual context `120064` tokens for desired context
`120000`.

| Suite | Runs | Result | Dashboard |
| --- | ---: | ---: | --- |
| `R3_REWIRE` | 21 | 21/21 | `release_ready=true`; unstable cases: none; raw legacy entries: none |
| `R4A_INSERT` | 15 | 15/15 | `release_ready=true`; unstable cases: none; raw legacy entries: none |
| `Tier5_ADVERSARIAL` | 54 | 54/54 | `release_ready=true`; unstable cases: none; raw legacy entries: none |

Artifacts:

- `/tmp/grc_agent_phase11_dashboards/r3_rewire.json`
- `/tmp/grc_agent_phase11_dashboards/r3_rewire_dashboard.json`
- `/tmp/grc_agent_phase11_dashboards/r4a_insert.json`
- `/tmp/grc_agent_phase11_dashboards/r4a_insert_dashboard.json`
- `/tmp/grc_agent_phase11_dashboards/tier5_adversarial.json`
- `/tmp/grc_agent_phase11_dashboards/tier5_adversarial_dashboard.json`

## Usability Finding

The two-turn guided pattern is viable in the evidence harness:

- Natural first turns can be handled without committing unsafe mutations.
- Exact selected options can then drive valid `change_graph` mutations.
- The safety boundary remains clean across direct and Ollama-first variants.

This does not prove a production options UI exists. The exact selected option is
currently supplied by the scenario. A production workflow still needs explicit,
user-visible option presentation and selection semantics before this can count
as general natural rewire/insert usability.

## Remaining Gaps

- Free natural rewire/insert remains weak without exact selected identifiers.
- Insert first turns may inspect/search or fail closed rather than present a
  polished option list.
- Rewire first turns may clarify textually rather than produce structured
  executable candidates.
- No alias, retry, or hidden repair was added.

## Recommended Phase 12

Measure and design explicit option payloads for production use:

1. Capture the exact clarification/inspect payloads currently emitted for
   natural rewire/insert.
2. Decide whether a typed option-selection contract should be added to the
   harness first.
3. Only after measurement, consider production runtime support for visible
   option selection. Do not auto-generate hidden topology repairs.

## Final Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`,
  `Tier5_ADVERSARIAL`
- Runtime: not production-ready
