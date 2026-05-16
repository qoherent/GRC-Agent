# Phase 9 Production-Readiness Evidence: Natural Rewire/Insert Attribution

Date: 2026-05-15

Status: attribution only. Runtime behavior, tool schemas, aliases, retry policy,
and eval scoring were not changed. Runtime remains not production-ready.

## Scope

Phase 9 isolates the Phase 8 natural-language failures for:

- `R3_REWIRE`
- `R4A_INSERT_BLOCK_ON_CONNECTION`

Ollama Cloud remains only the dummy user. The deterministic local judge remains
the only scorer.

Configuration:

- Model: `gemma3:4b`
- Provider: Ollama Cloud
- Temperature: `0.0`
- Seed: `9100`
- Runs: `5` per scenario
- Artifact directory: `/tmp/grc_agent_phase9_ollama`

## Phase 8 Failed Artifact Attribution

Phase 8 failed artifacts were inspected directly from
`/tmp/grc_agent_phase8_ollama`.

| Suite | Run count | Classification | Evidence |
| --- | ---: | --- | --- |
| `natural_rewire` | 5 | `missing_new_endpoint` | The dummy user prompt named the desired new direct connection. GRC Agent called `change_graph` with `operation_kind="rewire"`, but supplied the desired new edge as `connection_id` and omitted the explicit new endpoint fields. No graph delta was committed. |
| `natural_insert_block` | 3 | `invalid_candidate` | GRC Agent supplied connection, candidate, instance name, and params, but validation rejected the candidate because inserted block typing/params produced incompatible IO. Rollback held. |
| `natural_insert_block` | 2 | `missing_insert_instance_name` | GRC Agent called `change_graph` repeatedly with an insert candidate/name mixup and no valid `instance_name`. No graph delta was committed. |

Graph context was available in the artifacts: block names, block types, block
parameters, connection IDs, variables, state revision, and copied graph paths
were present in the initial graph snapshot. No failed run showed source graph
mutation.

## Prompt Ladder

Phase 9 added eight controlled Ollama dummy-user scenarios.

| Scenario | Ladder step | Expected behavior |
| --- | --- | --- |
| `rewire_vague` | A | Clarify/no mutation |
| `rewire_natural_actionable` | B | Natural request with old/new path in human terms |
| `rewire_semi_exact` | C | Old connection ID plus natural new endpoint |
| `rewire_exact` | D | Exact rewire fields in the user text |
| `insert_vague` | A | Clarify/no mutation |
| `insert_natural_actionable` | B | Natural insert request with anchor described in human terms |
| `insert_semi_exact` | C | Connection ID and block type, no instance name |
| `insert_exact` | D | Exact insert fields in the user text |

## Phase 9 Results

| Scenario | Pass rate | Clarification rate | No-call rate | Missing-arg count | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| `rewire_vague` | 5/5 | 5/5 | 5/5 | 0 | Safe clarification; no mutation. |
| `rewire_natural_actionable` | 0/5 | 5/5 | 5/5 | 0 | Turn plan classified as `uncertain_mutation`; no tool call. |
| `rewire_semi_exact` | 0/5 | 5/5 | 5/5 | 0 | Old connection ID and natural endpoint still no-called. |
| `rewire_exact` | 0/5 | 5/5 | 5/5 | 0 | Exact fields were present in the user prompt, but the turn plan still required clarification. |
| `insert_vague` | 5/5 | 5/5 | 5/5 | 0 | Safe clarification; no mutation. |
| `insert_natural_actionable` | 0/5 | 0/5 | 0/5 | 0 | Mostly wrong operation selection: `rewire` attempts instead of insert. |
| `insert_semi_exact` | 0/5 | 0/5 | 0/5 | 4 | Correct insert intent, but missing `instance_name`; one run also searched blocks after failures. |
| `insert_exact` | 0/5 | 4/5 | 0/5 | 4 | Confounded by dummy-user drift: Ollama did not preserve the exact requested fields and produced underspecified "byte stream" prompts. One run reached safe refusal. |

Aggregate:

- Total runs: 40
- Overall pass rate: 10/40
- Runtime safety rate: 40/40
- Model contract rate: 40/40
- Forbidden events: 0
- Raw legacy attempts: 0
- Failed-validation commits: 0
- Average turns: 1.0
- Average tool calls: 1.075
- Average dummy-user latency: 1026.25 ms

Secret scan over `/tmp/grc_agent_phase9_ollama` found no `ollama_key`,
`OLLAMA_API_KEY`, authorization header, bearer token, or API key markers.

## Per-Failure Classification

Phase 9 failure classes using the requested labels:

| Scenario | Count | Classification |
| --- | ---: | --- |
| `rewire_natural_actionable` | 5 | `model_no_call` |
| `rewire_semi_exact` | 5 | `model_no_call` |
| `rewire_exact` | 5 | `model_no_call` |
| `insert_natural_actionable` | 5 | `wrong_wrapper` |
| `insert_semi_exact` | 5 | `missing_insert_instance_name` |
| `insert_exact` | 5 | `dummy_user_underspecified` / `missing_insert_instance_name` / `safe_refusal_expected` |

The insert exact bucket is not clean evidence of exact-tool capability because
the dummy user did not return the exact requested field-rich prompt. The
generated prompts changed to underspecified requests such as "Insert a byte
stream with 32000 samples per second into the throttle block."

## Interpretation

Rewire:

- Vague requests safely clarify.
- Natural, semi-exact, and exact requests all no-call.
- The exact case had enough user-provided fields, so this points to a
  turn-plan/schema-model argument-construction gap rather than dummy-user
  underspecification or missing graph context.
- This is not a narrow alias problem.

Insert:

- Vague requests safely clarify.
- Natural actionable insertion often routes as rewire, so the immediate gap is
  operation selection under natural "insert on connection" phrasing.
- Semi-exact insertion repeatedly omits `instance_name`, so the immediate gap is
  required-argument construction/clarification.
- Exact insertion remains inconclusive because the Ollama dummy user drifted
  away from the exact prompt. A non-LLM scripted exact check already covers
  runtime capability; a future natural test should either use a stricter dummy
  prompt harness or bypass Ollama for the exact user utterance.

## Fix-Layer Decision

No runtime patch is justified in Phase 9.

Recommended Phase 10 work:

1. Add deterministic, non-Ollama exact natural utterance probes for rewire and
   insert to separate turn-plan gating from dummy-user drift.
2. Add controlled inspect-before-edit scenarios for rewire and insert with two
   turns, because one-turn exact construction is brittle for topology changes.
3. If repeated evidence still shows exact rewire no-call, investigate turn-plan
   routing and schema wording for rewire. Do not add aliases or hidden retries.
4. If repeated evidence still shows insert missing `instance_name`, prefer
   explicit clarification/candidate-selection UX over auto-generated names.

## Artifacts

- Aggregate report: `/tmp/grc_agent_phase9_ollama/aggregate_report.json`
- Per-run artifacts: `/tmp/grc_agent_phase9_ollama/*_run_*.json`
- Scenario definitions: `tests/production/scenarios_ollama_phase9/`
- Run config: `tests/production/ollama_gameplay_phase9_config.json`

## Final Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`,
  `Tier5_ADVERSARIAL`
- Runtime: not production-ready
