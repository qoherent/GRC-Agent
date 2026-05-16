# Phase 10 Production-Readiness Evidence: Rewire/Insert Structured-Call Usability

Date: 2026-05-15

Status: diagnosis only. Runtime behavior, tool schemas, aliases, retry policy,
and eval scoring were not changed. Runtime remains not production-ready.

## Scope

Phase 10 diagnoses why Phase 9 natural rewire and insert gameplay failed while
R3/R4A dashboards remain beta-clean.

Questions tested:

- Is the failure caused by Ollama dummy-user drift?
- Can GRC Agent act on deterministic exact natural prompt text?
- Is graph context missing?
- Are the structured fields too complex for semi-natural wording?
- Are existing R3/R4A dashboard prompts materially different from gameplay
  natural prompts?

## Direct Fixed-Prompt Probes

Direct probes use deterministic user text, not Ollama, through the real bounded
GRC Agent model loop. They are not scripted tool calls.

Command:

```text
uv run python -m tests.production.gameplay_runner \
  --config tests/production/direct_gameplay_phase10_config.json \
  --scenario-dir tests/production/scenarios_direct_phase10 \
  --artifact-dir /tmp/grc_agent_phase10_direct
```

Results:

| Scenario | Runs | Result | Tool behavior |
| --- | ---: | ---: | --- |
| `direct_rewire_exact` | 3 | 3/3 | `change_graph`, `operation_kind=rewire`, exact old edge and new endpoint fields; valid graph delta. |
| `direct_rewire_semi_exact` | 3 | 0/3 | No tool call; turn plan classified `uncertain_mutation`; safe clarification/no mutation. |
| `direct_insert_exact` | 3 | 3/3 | `change_graph`, `operation_kind=insert_block`, exact connection/candidate/instance/params; valid graph delta. |
| `direct_insert_semi_exact` | 3 | 3/3 | No content change expected; GRC Agent attempted insert but omitted `instance_name`, then safely clarified/refused without mutation. |

Aggregate:

- Total runs: 12
- Overall pass rate: 9/12
- Runtime safety rate: 12/12
- Model contract rate: 12/12
- Forbidden events: 0
- Raw legacy attempts: 0
- Failed-validation commits: 0
- Artifacts: `/tmp/grc_agent_phase10_direct`

## Guided Ollama Probes

Guided Ollama probes constrain the dummy user to preserve exact identifiers and
ask in one sentence. The first attempted wording included the model-facing tool
name and was correctly marked invalid by the natural-user quality gate. The
final guided wording removed tool names but kept exact identifiers.

Command:

```text
uv run python -m tests.production.gameplay_runner \
  --config tests/production/ollama_gameplay_phase10_config.json \
  --scenario-dir tests/production/scenarios_ollama_phase10 \
  --artifact-dir /tmp/grc_agent_phase10_ollama \
  --enable-ollama-network
```

Results:

| Scenario | Runs | Result | Evidence |
| --- | ---: | ---: | --- |
| `rewire_exact_guided` | 5 | 5/5 | Dummy user preserved exact rewire identifiers; GRC Agent called `change_graph` and committed valid rewire deltas. |
| `insert_exact_guided` | 5 | 5/5 | Dummy user preserved exact insert identifiers; GRC Agent called `change_graph` and committed valid insert deltas. |

Aggregate:

- Total runs: 10
- Overall pass rate: 10/10
- Runtime safety rate: 10/10
- Model contract rate: 10/10
- Forbidden events: 0
- Raw legacy attempts: 0
- Failed-validation commits: 0
- Artifacts: `/tmp/grc_agent_phase10_ollama`

Secret scans over both artifact directories found no `ollama_key`,
`OLLAMA_API_KEY`, authorization header, bearer token, or API key markers.

## Comparison To R3/R4A Dashboard Prompts

Existing dashboard prompts in `tests/llama_eval/r3_rewire.py` and
`tests/llama_eval/r4a_insert.py` are exact structured prompts, not broad natural
requests.

| Suite | Passing dashboard prompt shape | Phase 9 failing prompt shape | Phase 10 result |
| --- | --- | --- | --- |
| `R3_REWIRE` | Includes `connection_id`, `operation_kind`, `dry_run`, `state_revision`, `new_src_block`, `new_src_port`, `new_dst_block`, and `new_dst_port`. | Natural/semi-natural prompts named the intended topology but did not preserve every typed field. | Exact direct and guided prompts pass. Semi-exact direct prompt still no-calls. |
| `R4A_INSERT` | Includes `operation_kind`, `dry_run`, `connection_id`, `block_id`, `instance_name`, and compatible `insert_params`. | Natural prompts drifted to wrong operation or omitted required insert args, especially `instance_name`. | Exact direct and guided prompts pass. Semi-exact direct prompt safely refuses without mutation. |

This explains why R3/R4A dashboards can be beta-clean while broader natural
gameplay remains weak: the current runtime and schema can execute exact typed
requests, but natural utterances need to preserve enough structured fields.

## Prompt/Schema Usability Table

| Operation | Required fields for committed path | Fields usually omitted in failed natural runs | Visible in prompt? | Visible in graph context? | Usability diagnosis |
| --- | --- | --- | --- | --- | --- |
| Rewire | `operation_kind`, `dry_run`, `connection_id`, `state_revision`, `new_src_block`, `new_src_port`, `new_dst_block`, `new_dst_port` | New endpoint fields, sometimes all tool args because turn plan no-called | Natural prompts often implied endpoints; exact prompts included fields | Graph snapshot includes block names, connection IDs, state revision, and ports indirectly through connection IDs | Exact path works. Semi-natural endpoint language is too weak for current turn-plan/model routing. |
| Insert block | `operation_kind`, `dry_run`, `connection_id`, candidate via `block_id`/`candidate_id`/`insert_block`, `instance_name`; compatible `insert_params` for typed throttle | `instance_name`, candidate identity, or operation selection | Natural prompts often did not include instance name; guided exact prompts did | Graph snapshot includes existing connection and block IDs, but not a user-approved new instance name | Exact path works. Natural insertion needs explicit candidate/instance-name clarification or guided candidate selection. |

Schema names are precise for exact prompts but not ergonomic for broad natural
requests. Examples help when they are present in the user prompt or generated
dummy-user prompt. They do not solve semi-natural topology wording by
themselves.

## Fix-Layer Classification

| Finding | Classification |
| --- | --- |
| Phase 9 exact Ollama insert failed because dummy user paraphrased exact fields into underspecified "byte stream" prompts. | Dummy-user drift |
| Phase 10 guided exact Ollama rewire/insert passed 10/10 once identifiers were preserved. | Scenario wording issue / dummy-user drift |
| Direct exact rewire and insert passed 3/3. | Not a runtime bug |
| Direct semi-exact rewire no-called 3/3 despite old connection ID and natural new endpoint wording. | GRC Agent prompt/schema usability issue and model limitation for semi-natural topology mapping |
| Direct semi-exact insert safely refused because `instance_name` was missing. | Schema complexity / acceptable safe clarification need |
| No forbidden events, raw legacy attempts, or failed-validation commits. | Safety boundary held |

## Recommended Next Fix Layer

No Phase 10 runtime patch is justified.

Recommended Phase 11 options:

1. Add deterministic two-turn clarification gameplay for rewire and insert:
   first turn natural/semi-exact, second turn supplies exact missing fields.
2. Improve scenario wording and dummy-user controls before using Ollama
   outcomes as usability evidence.
3. If changing runtime later, prefer explicit clarification and typed candidate
   selection over aliases, hidden retries, or auto-generated topology repairs.
4. For rewire, investigate turn-plan wording only after measuring whether
   two-turn clarification resolves the semi-exact case.
5. For insert, treat `instance_name` as user-provided unless a future design
   explicitly introduces visible candidate/instance naming UI.

## Artifacts

- Direct aggregate: `/tmp/grc_agent_phase10_direct/aggregate_report.json`
- Direct per-run artifacts: `/tmp/grc_agent_phase10_direct/*_run_*.json`
- Guided Ollama aggregate: `/tmp/grc_agent_phase10_ollama/aggregate_report.json`
- Guided Ollama per-run artifacts: `/tmp/grc_agent_phase10_ollama/*_run_*.json`
- Direct scenario definitions: `tests/production/scenarios_direct_phase10/`
- Guided scenario definitions: `tests/production/scenarios_ollama_phase10/`

## Final Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`,
  `Tier5_ADVERSARIAL`
- Runtime: not production-ready
