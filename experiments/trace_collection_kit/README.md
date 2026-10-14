# GNU Radio Agent - Trace Collection Guide

This kit allows you to automatically generate, verify, and package high-quality agent interaction traces (prompts, internal reasoning, tool calls, and compiler validation) against a local or remote OpenAI-compatible model endpoint.

Every action is executed **headlessly in-memory** against GNU Radio's native Python engine (`get_platform()`, `FlowGraph`, `change_graph`), verifying syntactic validity and compiler health in real time.

---

## 1. Quickstart (One Command)

From the project root:

```bash
uv run --python .venv/bin/python experiments/trace_collection_kit/collect_traces.py
```

Or from inside the kit directory:

```bash
./run.sh
```

This will run the 6 built-in preset scenarios against the default local endpoint (`http://192.168.0.234:9000/v1` with `qwen3.8-27b`), execute the tool calls against GNU Radio, validate the graphs in-memory, and save the traces into `output/`.

---

## 2. Common Usage Recipes

### A. Run a Single Preset Scenario
To test a specific scenario (e.g. parameter update or block insertion):

```bash
./run.sh --scenario 01_update_param
./run.sh --scenario 02_add_throttle
```

Available presets:
- `01_update_param` — Updates variable/block parameters (e.g. changing frequency).
- `02_add_throttle` — Inserts a rate-limiting throttle block.
- `03_add_filter` — Adds a DSP Low Pass Filter with cutoff and decimation.
- `04_query_catalog` — Queries GNU Radio block catalog documentation.
- `05_inspect_targets` — Scoped inspection of specific flowgraph blocks.
- `06_rewire_connection` — Reroutes stream/message connections.

### B. Run a Custom Prompt
You can pass any arbitrary user instruction:

```bash
./run.sh --prompt "Add an analog noise source named white_noise with Gaussian noise and amplitude 0.05."
```

### C. Connect to a Different Endpoint / Model
Change the base URL or model name:

```bash
./run.sh --endpoint "http://192.168.0.234:9000/v1" --model "qwen3.8-27b"
```

### D. Multi-Turn Interactive Mode
By default, the collector operates in **one-shot mode** (the model immediately outputs reasoning + the tool call). If you want full exploratory multi-turn dialogue (inspect $\rightarrow$ search $\rightarrow$ edit $\rightarrow$ re-inspect):

```bash
./run.sh --multiturn
```

---

## 3. Command-Line Reference

| Flag | Default | Description |
|---|---|---|
| `--endpoint` | `http://192.168.0.234:9000/v1` | OpenAI-compatible base URL. |
| `--model` | `qwen3.8-27b` | Model ID served by the endpoint. |
| `--max-tokens` | `4096` | Max token budget (reasoning + output). |
| `--fixture` | `dial_tone.grc` | Base flowgraph fixture file. |
| `--out-dir` | `output` | Directory where outputs are written. |
| `--scenario` | `all` | Specific scenario ID or `all`. |
| `--prompt` | `None` | Custom prompt string (overrides presets). |
| `--multiturn` | `False` | Enables interactive multi-turn loop. |

---

## 4. Understanding the Outputs

All artifacts land in the `--out-dir` directory (`output/` by default):

1. **`sft_traces.jsonl`** *(Unsloth / Fine-Tuning Ready)*
   - Merged JSON Lines file formatted in standard OpenAI `messages` schema:
     - `role: "system"` — Active GRC harness instructions.
     - `role: "user"` — User prompt.
     - `role: "assistant"` — `reasoning_content` + `tool_calls`.
     - `role: "tool"` — Execution result from headless GNU Radio.
     - `role: "assistant"` — Confirmation text.
   - Ready for immediate ingestion into Unsloth, Axolotl, or Hugging Face SFT trainers.

2. **`<scenario_id>.json`** *(Raw Telemetry)*
   - Complete raw execution record including latency (`elapsed_seconds`), tools called, GRC compiler validity (`flowgraph_valid`), and validation error diagnostics.

3. **`<scenario_id>.md`** *(Human-Readable Report)*
   - Clean Markdown transcript displaying model thinking blocks, tool call JSON payloads, and execution returns.

---

## 5. Technical Highlights & Safety Guardrails

- **Headless In-Memory Execution**: No GUI, X11 display, or `xvfb` required. Operations run directly against GNU Radio Companion's Python API in ~10–20ms per tool call.
- **Strict-Tool Fix for NInfer**: Disables `'strict': True` on function schemas via `ModelProfile(openai_supports_strict_tool_definition=False)` to prevent HTTP 400 rejection.
- **Full Reasoning Capture**: Native extraction of `"reasoning_content"` from the model payload into `ThinkingPart`, preventing reasoning clipping.
- **Auto-Approval Harness**: Automatically resolves Pydantic AI's `DeferredToolRequests` (approval gates) so graph mutations execute seamlessly without human blocking.
