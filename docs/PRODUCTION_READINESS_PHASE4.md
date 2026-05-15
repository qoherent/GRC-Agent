# Production Readiness Phase 4: Ollama Dummy-User Gameplay

Date: 2026-05-15

Status: research evidence only. This phase does not change the runtime
classification and does not claim production readiness.

## Scope

Phase 4 adds a limited Ollama-powered dummy-user layer on top of the deterministic
Phase 3 gameplay harness.

Implemented:

- `tests/production/ollama_user_client.py`
- `tests/production/scenarios_ollama/`
- `ollama_user` mode in `tests/production/gameplay_runner.py`
- deterministic judge handling for natural-user text and infra failures
- secret-redaction tests and disabled-network default tests

Not changed:

- GRC Agent runtime behavior
- model-facing tool schemas
- live eval scoring
- mutation safety policy
- release/beta capability classification

## Key Handling

The repository root `.env` contains `ollama_key`. The harness maps that value to
the official `OLLAMA_API_KEY` name in process only. The key is never printed,
stored in artifacts, committed, or used as mutation authority.

Artifact and test checks reject these markers:

- `ollama_key`
- `OLLAMA_API_KEY`
- `Authorization`
- `Bearer`

Network calls are disabled by default. Ollama calls require an explicit runner
flag.

## Ollama Readiness

Commands:

```bash
uv run python -m tests.production.ollama_readiness
uv run python -m tests.production.ollama_readiness --check-cloud
```

Observed result:

- key present: true
- default network check: false
- cloud smoke: reachable
- endpoint: `https://ollama.com/api/tags`
- model count: 39
- gameplay model used: `gemma3:4b`

No prompt content was sent during readiness. The optional cloud smoke used only a
model-list endpoint.

## Scenario List

Ollama scenarios live under `tests/production/scenarios_ollama/`.

| Scenario | Purpose | Expected Behavior |
| --- | --- | --- |
| `natural_read_only_explain` | Free-text explanation request | Inspect/read-only, no mutation |
| `natural_set_param` | Free-text parameter edit | Set `samp_rate` to `48000` and validate |
| `natural_save_load` | Free-text lifecycle request | Save copied graph, then load saved copy |

Dummy user sees only:

- scenario goal
- compact graph summary
- allowed user behavior
- forbidden user behavior
- short prior conversation

Dummy user does not see:

- hidden expected final state
- judge rules
- internal tool names
- API key
- mutation internals

## Artifact Format

Ollama artifacts include:

- scenario id
- dummy user provider and model
- whether cloud was used
- redacted client config
- all user turns and GRC Agent responses
- raw requested tool calls
- executed tools
- graph deltas
- validation results
- save/load events
- deterministic judge result
- forbidden events
- usage/latency fields when Ollama returns them

Artifacts are written outside git by default. Phase 4 run artifacts were written
to:

```text
/tmp/grc_agent_phase4_ollama/
```

## Observed Gameplay Results

Command shape:

```bash
uv run python -m tests.production.gameplay_runner \
  --scenario tests/production/scenarios_ollama/natural_read_only_explain.json \
  --artifact /tmp/grc_agent_phase4_ollama/natural_read_only_explain.json \
  --enable-ollama-network \
  --ollama-model gemma3:4b
```

Results:

| Scenario | Judge | Tools | Mutation Count | Validation | Forbidden Events |
| --- | --- | --- | ---: | --- | ---: |
| `natural_read_only_explain` | pass | `inspect_graph` | 0 | unknown | 0 |
| `natural_set_param` | pass | `change_graph` | 1 | valid | 0 |
| `natural_save_load` | pass | `save_graph_explicit`, `load_graph_explicit` | 0 | valid | 0 |

Secret scan:

```text
no secret markers in Ollama artifacts
```

## Limitations

- This is `n=1` research evidence only.
- Ollama is used only as a dummy user, never as judge or mutation authority.
- Structured outputs are intentionally not required.
- Only three scenarios are covered.
- The deterministic judge remains local and rule-based.
- Results are not release-gating.
- Runtime remains not production-ready.

## Phase 5 Recommendation

Phase 5 should broaden natural gameplay only after the Phase 4 artifact format
stays stable:

- run repeated Ollama dummy-user trials with fixed model/config
- add diagnostic-only natural disconnect/rewire scenarios
- compare local Ollama and Cloud dummy-user variance
- add transcript review reports for failed/ambiguous natural user turns
- keep deterministic judge as the only scorer
- keep all generated graphs and transcripts outside git

## Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `Tier5_ADVERSARIAL`
- Diagnostic-partial: `R7_NATURAL_EXTERNAL`
- Runtime: not production-ready
