# Programmatic Demo Video Workflow

This demo uses real GRC Agent turns against a copied GNU Radio Companion graph. It does not mock tool calls, graph deltas, validation results, save/load events, or debug bundle output.

## Preconditions

Use the CUDA llama.cpp path on NVIDIA machines. The demo expects `llama-server --list-devices` to show `CUDA0`.

```bash
llama-server --list-devices

llama-server \
  -hf unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL \
  --alias unsloth/gemma-4-E2B-it-GGUF \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 120000 \
  --device CUDA0 \
  --gpu-layers 999 \
  --jinja \
  --no-mmproj
```

Verify runtime readiness before recording evidence:

```bash
uv run grc-agent health
uv run grc-agent vector stats --json
```

`health` must be `ok`, and `llama_context_verified` must be `true`.

## Run The Demo

The runner copies the installed graph to `/tmp/grc_agent_demo/dial_tone_demo.grc` and never mutates the original installed example.

```bash
uv run python scripts/demo/run_grc_agent_demo.py \
  --graph /usr/share/gnuradio/examples/audio/dial_tone.grc \
  --workdir /tmp/grc_agent_demo
```

It writes:

- `/tmp/grc_agent_demo/dial_tone_demo.grc`
- `/tmp/grc_agent_demo/demo_artifact.json`
- `/tmp/grc_agent_demo/debug_bundle.json`

Optional screenshot hooks:

```bash
uv run python scripts/demo/run_grc_agent_demo.py \
  --graph /usr/share/gnuradio/examples/audio/dial_tone.grc \
  --workdir /tmp/grc_agent_demo \
  --before-screenshot /tmp/grc_agent_demo/before.png \
  --after-screenshot /tmp/grc_agent_demo/after.png
```

Screenshots are optional; missing files do not block the demo.

## Export Timeline

```bash
uv run python scripts/demo/export_demo_timeline.py \
  --artifact /tmp/grc_agent_demo/demo_artifact.json \
  --output /tmp/grc_agent_demo/demo_timeline.json
```

The timeline is a compact video input derived from the real artifact. It includes prompt text, wrapper names, `operation_kind`, graph deltas, validation status, mutation yes/no, graph paths, and optional screenshots.

## Render Video

The Remotion template lives in `demo/remotion/` and reads `/tmp/grc_agent_demo/demo_timeline.json` via the Remotion `--props` JSON file path.

```bash
cd demo/remotion
npm install
npm run build
```

The default output is:

```text
/tmp/grc_agent_demo/grc_agent_demo.mp4
```

For a quick layout check:

```bash
cd demo/remotion
npm run still
```

## Expected Demo Flow

1. Inspect the copied graph.
2. Change the sample rate to `48000`.
3. Add variable `demo_gain=0.25`.
4. Ask for a guided throttle insertion, then provide the exact selected connection and block parameters.
5. Save the copied graph explicitly.
6. Load the saved graph and inspect final state.
7. Generate a debug bundle.

## Safety Evidence

The artifact must show:

- original graph hash unchanged
- copied graph path under `/tmp/grc_agent_demo/`
- validation succeeded
- explicit `save_graph_explicit` event
- `raw_legacy_attempts=0`
- `failed_validation_commits=0`
- debug bundle generated
- no `ollama_key`, `OLLAMA_API_KEY`, `Authorization`, `Bearer`, or API-key-like assignments in artifacts

## Troubleshooting

- If the runner refuses with `health status is not ok`, start the CPU `llama-server` command above and rerun `uv run grc-agent health`.
- If context is not verified, confirm `/props` reports an actual context at least the configured desired context.
- If the source graph is missing, install GNU Radio examples or provide another copied-safe source graph.
- If Remotion render fails, first run `npm install` inside `demo/remotion/`, then verify `/tmp/grc_agent_demo/demo_timeline.json` exists.

## Status

This demo supports the current evidence classification only: release-validated subset plus beta-validated graph operations. It is not a production-ready claim.
