# Phase 20: End-to-End Runtime Readiness Bring-Up

Date: 2026-05-21

Scope: environment/runtime bring-up only. This phase does not change graph
mutation behavior, tool schemas, eval scoring, or capability classification.

## Verdict

End-to-end runtime environment readiness is proven only for the CPU-only
llama.cpp server profile documented below.

Overall product production readiness is still not claimed.

| Field | Status | Evidence |
| --- | --- | --- |
| `package_ready` | true | system-site install smoke |
| `gnu_radio_ready` | true | GNU Radio `3.10.9.2` imported |
| `grcc_ready` | true | `/usr/bin/grcc` |
| `retrieval_catalog_ready` | true | `/usr/share/gnuradio/grc/blocks` |
| `vector_index_ready` | true | fresh smoke built index; main stats ready |
| `llama_ready` | true | `grc-agent health` status `ok` |
| `context_verified` | true | `/props` actual context `120064` >= desired `120000` |
| `end_to_end_ready` | true | install smoke with `--build-vector-index --require-vector-index --require-llama` |
| `production_ready` | false | Phase 20 proves environment readiness, not full product readiness |

## Llama Bring-Up

The configured runtime target is:

- Server URL: `http://127.0.0.1:8080`
- Model alias: `unsloth/gemma-4-E2B-it-GGUF`
- HF model: `unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL`
- Desired context: `120000`

Official llama.cpp documentation for the `ggml-org/llama.cpp` server confirms
that `llama-server` exposes an OpenAI-compatible HTTP server and supports
server host/port, Hugging Face model loading, aliases, Jinja chat templates,
and context-size configuration.

The first Vulkan-backed server profile reached `/props` and reported
`n_ctx=120064`, but real GRC Agent chat/eval prompts crashed the backend:

- Error: `vk::DeviceLostError`
- Failing path: `/v1/chat/completions`
- Symptom: `RemoteDisconnected` / `server_disconnect`
- Result: not acceptable for end-to-end readiness

The stable Phase 20 profile is CPU-only:

```bash
setsid llama-server \
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
  --no-mmproj \
  > /tmp/grc_agent_phase20_llama_server_cpu.log 2>&1 < /dev/null &
```

Evidence:

- PID: `67704`
- Log path: `/tmp/grc_agent_phase20_llama_server_cpu.log`
- `/props`: responded
- Actual context: `120064`
- Context verified: true
- `/v1/models`: model alias `unsloth/gemma-4-E2B-it-GGUF`

## Health And Manifest

`uv run grc-agent health`:

- `status=ok`
- `llama_model_ready=true`
- `llama_context_verified=true`
- `llama_actual_context_tokens=120064`
- `llama_desired_context_tokens=120000`
- `retrieval_ready=true`
- model-facing tools remain exactly six:
  - `inspect_graph`
  - `search_blocks`
  - `ask_grc_docs`
  - `change_graph`
  - `save_graph_explicit`
  - `load_graph_explicit`

`uv run grc-agent release-manifest` was run before commit and correctly
reported dirty source after the install-smoke CLI change. Final manifest must
be rerun after commit and must report `dirty=false`.

## Vector Proof

Main workspace:

- `uv run grc-agent vector stats --json`: `ok=true`
- Active collection: `grc_agent_retrieval_v1_staging_20260506161536_01bb3a838ac2`
- Records/points: `1605`
- Embedding model: `BAAI/bge-small-en-v1.5`
- Source types:
  - `catalog_block`: 564
  - `manual_chunk`: 882
  - `tutorial_chunk`: 159

`uv run python -m tests.retrieval_eval.vector_regression`:

- First concurrent-adjacent run: `275 < 276` vector top-k threshold
- Clean rerun: passed
- Final passing counts:
  - total cases: 290
  - vector top-k hits: 276
  - provenance passes: 290
  - safety passes: 290
  - exact/source-type/false-positive misses: 0

Fresh system-site smoke:

```bash
uv run python -m tests.production.install_smoke \
  --mode system-site-venv \
  --build-vector-index \
  --require-vector-index \
  --require-llama \
  --timeout-seconds 900 \
  --output /tmp/grc_agent_install_smoke_end_to_end.json
```

Result:

- `ok=true`
- `package_ready=true`
- `gnu_radio_ready=true`
- `grcc_ready=true`
- `retrieval_catalog_ready=true`
- `vector_index_ready=true`
- `llama_ready=true`
- `context_verified=true`
- `end_to_end_ready=true`
- `overall_environment_classification=runtime_ready`

The fresh build step took about 147 seconds for vector index construction.

## Runtime Gameplay Smoke

Vulkan-backed smoke failed and is not accepted:

- Direct `grc-agent chat tests/data/random_bit_generator.grc --message "What does this flowgraph do?"`
  failed with `RemoteDisconnected`.
- The llama.cpp log showed `vk::DeviceLostError`.

CPU-only smoke passed:

- Direct `grc-agent chat tests/data/random_bit_generator.grc --message "What does this flowgraph do?"`
  exited 0 and called `inspect_graph`.
- R0 read-only n=3 with `--max-tokens 512`:
  - artifact: `/tmp/grc_agent_phase20_r0_cpu.json`
  - runs: 42
  - pass: 42
  - infra failures: 0
  - runtime safety: 42/42
  - model contract: 42/42
- R1 set_param n=3 with `--max-tokens 512`:
  - artifact: `/tmp/grc_agent_phase20_r1_param_cpu.json`
  - runs: 6
  - pass: 6
  - infra failures: 0
  - runtime safety: 6/6
  - model contract: 6/6
- Scripted gameplay scenario:
  - command: `uv run python -m tests.production.gameplay_runner --scenario tests/production/scenarios/read_only_explain.json --artifact /tmp/grc_agent_phase20_gameplay_read_only.json`
  - passed: true
  - forbidden events: 0
  - mutation count: 0

## Debug Bundle

Command:

```bash
uv run grc-agent debug-bundle --output /tmp/grc_agent_debug_bundle_end_to_end.json
```

Result:

- `ok=true`
- `health_status=ok`
- `health_status_reasons=[]`
- `vector_index_ok=true`
- `secrets_redacted=true`

Secret scan targets:

- `/tmp/grc_agent_debug_bundle_end_to_end.json`
- `/tmp/grc_agent_install_smoke_end_to_end.json`
- `/tmp/grc_agent_phase20_r0_cpu.json`
- `/tmp/grc_agent_phase20_r1_param_cpu.json`
- `/tmp/grc_agent_phase20_gameplay_read_only.json`
- `/tmp/grc_agent_phase20_llama_server_cpu.log`

The final report should record the scan result after running it.

## Repeatable Setup Path

Fresh user setup:

```bash
uv sync --locked
uv run grc-agent doctor
uv run grc-agent vector build
uv run grc-agent vector stats --json
```

Start stable CPU-only llama.cpp:

```bash
setsid llama-server \
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
  --no-mmproj \
  > /tmp/grc_agent_llama_server.log 2>&1 < /dev/null &
```

Verify strict readiness:

```bash
uv run grc-agent health
uv run grc-agent release-manifest
uv run python -m tests.production.install_smoke \
  --mode system-site-venv \
  --build-vector-index \
  --require-vector-index \
  --require-llama \
  --timeout-seconds 900 \
  --output /tmp/grc_agent_install_smoke_end_to_end.json
```

## Remaining Blockers

- The default Vulkan backend on this host is not runtime-stable for real GRC
  Agent prompts at the 120k context target. It can pass `/props` and health,
  then crash on chat completion.
- The CPU-only profile is stable but slow. It is acceptable as a reproducible
  readiness path, not as a polished production UX.
- This phase does not promote beta capabilities, broaden natural-language
  support, change mutation safety, or certify production readiness.

Final classification remains:

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `Tier5_ADVERSARIAL`, `R7_NATURAL_EXTERNAL`
- Runtime environment: end-to-end ready only under the CPU-only profile above
- Product runtime: not production-ready
