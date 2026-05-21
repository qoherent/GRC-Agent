# Phase 21: CPU End-to-End Runtime Burn-In

Date: 2026-05-21

Scope: evidence harness and runtime environment burn-in only. This phase does
not change graph mutation behavior, tool schemas, eval scoring, or model
behavior features.

## Verdict

The CPU-only llama.cpp runtime path is burn-in validated across three clean
end-to-end runs.

Overall product production readiness is still not claimed.

| Field | Status | Evidence |
| --- | --- | --- |
| CPU end-to-end path | burn-in validated | 3/3 runs passed |
| `package_ready` | true | install smoke in every run |
| `gnu_radio_ready` | true | GNU Radio `3.10.9.2` imported |
| `grcc_ready` | true | `/usr/bin/grcc` |
| `retrieval_catalog_ready` | true | `/usr/share/gnuradio/grc/blocks` |
| `vector_index_ready` | true | fresh smoke built/verified vector index |
| `llama_ready` | true | health status `ok` in every run |
| `context_verified` | true | actual context `120064` >= desired `120000` |
| `end_to_end_ready` | true | install smoke passed with vector and llama required |
| Vulkan | rejected/experimental | real prompts previously crashed with `vk::DeviceLostError` |
| `production_ready` | false | burn-in validates one runtime path, not the full product checklist |

Artifacts were written under `/tmp/grc_agent_phase21_burnin/`.

## CPU Bring-Up Command

The accepted burn-in profile is CPU-only:

```bash
llama-server \
  -hf unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL \
  --alias unsloth/gemma-4-E2B-it-GGUF \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 120000 \
  --device none \
  --gpu-layers 0 \
  --threads 12 \
  --threads-batch 12 \
  --jinja \
  --no-mmproj
```

The Phase 21 burn-in runner starts one server per run, waits for `/props`,
verifies context, runs readiness/eval smoke checks, writes artifacts, scans for
secrets, and stops the server before the next run:

```bash
uv run python -m tests.production.cpu_runtime_burnin \
  --runs 3 \
  --artifact-dir /tmp/grc_agent_phase21_burnin \
  --startup-timeout-seconds 300
```

## Burn-In Results

Aggregate result:

- `all_passed=true`
- `passed_runs=3`
- `total_runs=3`
- artifact root: `/tmp/grc_agent_phase21_burnin`
- schema: `2026-05-21.phase21-cpu-burnin-v1`

| Run | Startup | PID | Context | Install smoke | Vector regression | R0 n=3 | R1 set_param n=3 | Gameplay | Debug bundle | Secrets |
| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 4.033s | 189138 | 120064/120000 verified | pass, `end_to_end_ready=true` | pass, 276/290 vector hits, 290/290 safety/provenance | 42/42 PASS, 0 restarts | 6/6 PASS, 0 restarts | pass | pass | 0 hits |
| 2 | 4.002s | 278374 | 120064/120000 verified | pass, `end_to_end_ready=true` | pass, 276/290 vector hits, 290/290 safety/provenance | 42/42 PASS, 0 restarts | 6/6 PASS, 0 restarts | pass | pass | 0 hits |
| 3 | 4.003s | 376280 | 120064/120000 verified | pass, `end_to_end_ready=true` | pass, 276/290 vector hits, 290/290 safety/provenance | 42/42 PASS, 0 restarts | 6/6 PASS, 0 restarts | pass | pass | 0 hits |

No run required a backend restart. No run produced forbidden secret markers in
the scanned artifacts.

## Readiness Checks

Each run executed:

```bash
uv run grc-agent doctor
uv run grc-agent vector stats --json
uv run grc-agent health
uv run grc-agent release-manifest
uv run python -m tests.production.install_smoke \
  --mode system-site-venv \
  --build-vector-index \
  --require-vector-index \
  --require-llama \
  --timeout-seconds 900 \
  --output <run-dir>/install_smoke_end_to_end.json
uv run python -m tests.retrieval_eval.vector_regression
uv run python -m tests.llama_eval.run_r0_release \
  --n-runs 3 \
  --max-tokens 512 \
  --results-path <run-dir>/r0_store.json
uv run python -m tests.llama_eval.release_dashboard \
  --scope r0 \
  --results-path <run-dir>/r0_store.json \
  --min-runs-per-case 3 \
  --stability-threshold 1.0
uv run python -m tests.llama_eval.run_r1_release \
  --n-runs 3 \
  --max-tokens 512 \
  --results-path <run-dir>/r1_set_param_store.json
uv run python -m tests.llama_eval.release_dashboard \
  --scope r1 \
  --results-path <run-dir>/r1_set_param_store.json \
  --min-runs-per-case 3 \
  --stability-threshold 1.0
uv run python -m tests.production.gameplay_runner \
  --scenario tests/production/scenarios/read_only_explain.json \
  --artifact <run-dir>/gameplay_read_only.json
uv run grc-agent debug-bundle --output <run-dir>/debug_bundle.json
```

The burn-in runner also verifies that `release-manifest` reports
`git.dirty=false` during each run. The runner itself was committed before the
burn-in started so the manifest check could be evaluated against a clean source
tree.

## Gameplay Smoke Note

The scripted `read_only_explain` gameplay scenario passed in every run with:

- deterministic judge: pass
- forbidden events: `0`
- mutation count: `0`
- runtime safety: pass

The artifact includes a safe rejection of an invalid model-facing
`inspect_graph(operation="summary")` argument in the read-only scenario. That is
not hidden: the invalid tool result is preserved in the artifact and the judge
still passed the scenario because no mutation occurred, the tool surface
contract remained enforced, and the read-only task stayed safe.

## Debug Bundle And Secret Scan

Each run generated a debug bundle. The bundle reported:

- `ok=true`
- `health.status=ok`
- `llama.context_verified=true`
- `vector_index.ok=true`
- GNU Radio import ready
- `grcc` ready
- six model-facing MVP wrappers

Scanned markers:

- `ollama_key`
- `OLLAMA_API_KEY`
- `Authorization`
- `Bearer`
- `sk-...`
- generic API-key/token/secret assignments

Result: no hits in the scanned burn-in artifacts.

## Vulkan Status

Vulkan is not accepted for readiness.

The Vulkan-backed path reached `/props` during Phase 20 but crashed on real GRC
Agent prompt traffic with `vk::DeviceLostError`. Phase 21 therefore treats
Vulkan as rejected/experimental. The proven runtime path is CPU-only.

## Remaining Production Blockers

Phase 21 proves that the CPU-only runtime environment can be brought up and
survive repeated end-to-end readiness/eval smoke checks. It does not close the
broader production checklist:

- beta capabilities still need broader release-grade validation before being
  promoted
- natural multi-turn workflows still have known ergonomics limits outside the
  guided/evidence cases
- runtime operations still need wider real-project soak and support evidence
- GPU/Vulkan acceleration remains unproven and explicitly rejected for now
- production packaging/container story is not yet finished

## Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`
- Docs QA: threshold-met deterministic baseline
- CPU end-to-end path: burn-in validated
- Vulkan: rejected/experimental
- Runtime: not production-ready
