# Plan: Re-host the GRC tools on PydanticAI (Python-native agent harness)

> Status: **plan only — not yet implemented.**
> This is an experiment to measure whether a standard Python agent harness
> (PydanticAI) can drive the existing GRC graph-editing tools with **minimal
> hand-written runtime code**, replacing the hand-rolled `ToolAgents` harness.

---

## 1. Goal

Drive the GRC graph-editing agent through **PydanticAI** as the harness, letting
PydanticAI own the tool-call loop, conversation history, retries, native
thinking, and context compaction — while keeping the **5 model-facing tools,
their JSON schemas, and the system prompt completely unchanged**. Score it
against a representative subset of the existing scenario suite and compare to
the `ToolAgents` baseline tracked in `docs/MODEL_EVAL.md`.

### Why PydanticAI (and not the original pi.dev idea)

The first idea was [pi.dev](https://pi.dev) (`@earendil-works/pi-coding-agent`).
That was rejected because pi is **JavaScript/TypeScript** and has **no MCP
support** (by design). The 5 GRC tools are gnuradio-backed **Python**, so any
pi-based approach forces a JS↔Python **bridge** (a small HTTP server or
per-call CLI shim plus a TypeScript extension with Typebox schemas). That bridge
is extra, brittle infrastructure the project wants to avoid.

**PydanticAI is the Python-native equivalent** and eliminates the bridge:

| Need | PydanticAI |
|------|-----------|
| Same ecosystem as the codebase | ✅ Pydantic V2 (already a dependency) |
| Reuse existing OpenAI JSON schemas unchanged | ✅ `Tool.from_schema(json_schema=…)` takes the `_MVP_SCHEMAS` dicts verbatim |
| OpenAI-compatible backend (Ollama) | ✅ `OllamaProvider(base_url=…)` |
| Owns the tool-call loop | ✅ `agent.run_sync(…)` |
| Owns message history | ✅ `result.all_messages()` / `message_history=` |
| Context compaction | ✅ provider-agnostic (`ProcessHistory` built-in; `summarization-pydantic-ai` extension) |
| Native reasoning/thinking | ✅ Ollama model profile (`openai_chat_thinking_field='reasoning'`) |

---

## 2. Locked decisions

| Decision | Choice |
|----------|--------|
| Harness | **PydanticAI** (latest stable, pin exact version) |
| Backend | **Ollama**, `OllamaProvider(base_url="http://localhost:11434/v1")` |
| Chat model | **`qwen3.6:35b-a3b-q4_K_M`** (must be pulled) |
| Compaction | **On from the start**, default config (`ContextManagerCapability`) |
| Scenarios | Representative subset: **01, 11, 06, 09, 14, 21** |
| Folder | Top-level **`PydanticAI_experiment/`** |
| Test-suite reuse | **None** — clean, minimal, self-contained code in this folder; imports only public `grc_agent` symbols |

---

## 3. Verified PydanticAI features used (context7-verified)

- **`Tool.from_schema(function, name, description, json_schema, takes_ctx)`** —
  accepts a raw JSON schema object verbatim. Setting `takes_ctx=True` is natively supported
  and correctly injects `RunContext` as the first argument. Standard parameter validation is skipped
  by the framework, passing arguments as keyword arguments directly to the handler, which perfectly
  preserves GRC's internal validation.
- **Capabilities API** — `capabilities=[…]` is a first-class feature that bundles tools, instructions,
  and hooks. We bundle our 5 tools and system prompt into a custom `Capability(id="grc_core", ...)`
  to reduce Agent initialization boilerplate.
- **Context compaction (provider-agnostic, works with Ollama):**
  - We use the built-in, native **`ProcessHistory(prune_history)`** capability class (from `pydantic_ai.capabilities`)
    to implement a boundary-safe sliding-window history processor in Python.
  - This completely avoids adding external third-party dependencies like `summarization-pydantic-ai`.
  - The custom pruner keeps the initial prompt/system prompt intact, keeping only the most recent N messages.
    It guarantees boundary safety (never splitting tool-call/result pairs) by ensuring any truncated slice
    always starts on a `ModelRequest` that is a user prompt (containing a `UserPromptPart`), never a tool response.
- **Ollama provider** — `OllamaModel(name, provider=OllamaProvider(base_url=…))` is configured correctly.
  Because the unified `thinking` setting in `ModelSettings` (and the `Thinking` capability) translates to the
  OpenAI-specific `reasoning_effort` parameter which local Ollama models do not support, we use the explicit,
  proven **`ModelSettings(extra_body={"think": True})`** configuration to request reasoning content from local Ollama.
  OllamaProvider automatically configures `openai_chat_thinking_field='reasoning'` by default to parse and map
  reasoning blocks.
- **Dependency injection** — `deps_type=GrcAgent` + `RunContext[GrcAgent]` gives tools type-safe access
  to the live session via `ctx.deps`.
- **History / run** — `agent.run_sync(prompt, deps=…)`, `result.all_messages()` (which is a method returning a list).

---

## 4. Architecture / data flow

```
For each scenario in the subset:
  fresh_agent(fixture)                         # temp .grc copy + live GrcAgent
        │
        ▼
  PydanticAI Agent(
      OllamaModel(qwen3.6, provider=OllamaProvider(http://localhost:11434/v1)),
      deps_type = GrcAgent,
      capabilities = [
          get_grc_capability(),                # bundles 5 tools + instructions
          ProcessHistory(prune_history)        # boundary-safe compaction
      ],
      model_settings = ModelSettings(extra_body={"think": True})   # native thinking
  )
        │  agent.run_sync(sc.prompt, deps=grc)   ← PydanticAI owns loop/history/retries/thinking/compaction
        ▼
  check_expect(fixture_path, sc.expect)          # fresh reload + validate from disk truth
        │
        ▼
  record (pass/fail + reasons)  →  METRICS.md
```

---

## 5. What we DROP (all the hand-written runtime)

- `ToolAgentsRunner._run_turn_events` and the manual step loop.
- Stuck-loop detection and degenerate-response retries
  (`_evaluate_loop_state`, `_LOOP_*`, `_MAX_PROVIDER_RETRIES`).
- `GrcResponseConverter` (thinking-token extraction — now handled natively by the Ollama model profile).
- `render_model_messages` / `_prune_completed_episodes` (context assembly — now handled natively by `ProcessHistory(prune_history)`).
- `ToolAgentsRegistryBuilder` / `ToolAgentsToolDelegate` / `_function_tool_from_openai_tool`.
- The `ToolAgents` dependency from this experiment's path entirely.

**PydanticAI owns 100% of:** the model-call loop, parallel/sequential tool
execution, retries, message history, native thinking, and compaction.

---

## 6. What we KEEP unchanged

- **5 tools + their engines** (`src/grc_agent/runtime/{inspect_graph,search_blocks,doc_answer,web_search,change_graph}.py`) — untouched.
- **Schemas verbatim** — `build_tool_schemas()` from `tool_schemas.py`, fed to
  `Tool.from_schema(json_schema=fn["parameters"], name=…, description=…)`. No rewrite.
- **System prompt** — `build_system_prompt(chat_session_id)` from `model_context.py`,
  wrapped inside `Capability` instructions.
- **Fixtures** — `tests/data/{dial_tone,empty,resampler_demo,fm_rx,broken_unconnected_sink}.grc` (the subset uses these).
- **Tool execution path** — still `agent.execute_tool(name, kwargs, model_tool_call=True)`
  (`agent.py:235`), which preserves schema validation, `"auto"` type resolution, and the `change_graph` atomic
  transaction/rollback (essential for `force` and valid-batch semantics).
- **Scoring philosophy** — reload the mutated `.grc` from disk and run a fresh
  `validate()` (evidence from the resulting topology, never a tool's `ok` flag).

---

## 7. Deliverables — folder layout (2 files, minimal & self-contained)

```
PydanticAI_experiment/
├── PLAN.md      ← this document
└── run.py       ← the whole harness + 6 scenarios, single file (~180 lines)
```

**No imports from `tests/`.** Only public `grc_agent` symbols + PydanticAI.

### Public import surface (the entire dependency surface)

```python
from grc_agent import GrcAgent, FlowgraphSession                 # public __all__
from grc_agent.config import default_app_config                  # config loader
from grc_agent.runtime.tool_schemas import build_tool_schemas    # public accessor
from grc_agent.runtime.model_context import build_system_prompt  # SSOT system prompt
from grc_agent.grc_native_adapter import load_flow_graph, render_flow_graph, validate  # scoring
from pydantic_ai import Agent, RunContext, Tool, ModelSettings, ModelMessage, ModelRequest
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.capabilities import Capability, ProcessHistory  # native capabilities & pruner
```

### `run.py` skeleton

```python
import dataclasses
import json
import os
import shutil
import tempfile
from pathlib import Path
from grc_agent import GrcAgent, FlowgraphSession
from grc_agent.config import default_app_config
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.model_context import build_system_prompt
from grc_agent.grc_native_adapter import load_flow_graph, render_flow_graph, validate
from pydantic_ai import Agent, RunContext, Tool, ModelSettings, ModelMessage, ModelRequest
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.capabilities import Capability, ProcessHistory

MODEL = "qwen3.6:35b-a3b-q4_K_M"
OLLAMA_V1 = "http://localhost:11434/v1"

# 1) 6 scenarios inline (prompt, fixture, expect) — verbatim from tests/agent_flow/run_agent_flow.py
SCENARIOS = [
    {
        "name": "01_add_throttle",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Take a look at the flowgraph, then add a throttle block in the"
            " path between the 350 Hz tone and the adder that mixes the tones"
            " together. Call it `mid_throttle`, set its type to float, and"
            " have it use the samp_rate variable for its rate. Make sure the"
            " wiring is rerouted so it actually sits inline. Then inspect the"
            " result to confirm."
        ),
        "expect": {"blocks_present": ["mid_throttle"], "valid": True},
    },
    {
        "name": "11_scoped_inspect_and_update",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "This flowgraph has several blocks in it. Using inspect_graph's"
            " targets option, look at just the sample rate variable and the"
            " 350 Hz tone source — don't pull the whole overview. Then"
            " change the sample rate to 96000. Check just those same two"
            " blocks again to confirm."
        ),
        "expect": {
            "params": {"samp_rate": {"value": "96000"}},
            "valid": True,
        },
    },
    {
        "name": "06_query_knowledge_multiply",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph. I want to multiply the two sine wave"
            " tones together instead of adding them. Look up the right GNU"
            " Radio block for a signal multiplier using query_knowledge"
            " (catalog domain) — don't guess the block id. Add it, call it"
            " `multiplier`, set its type to float, wire both tone sources"
            " into it, and remove the adder that's currently combining"
            " them. Inspect the result to confirm."
        ),
        "expect": {
            "blocks_present": ["multiplier"],
            "blocks_absent": ["blocks_add_xx"],
            "valid": True,
        },
    },
    {
        "name": "09_docs_stream_tags_concept",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "I'm learning GNU Radio. Use `query_knowledge` with the **docs**"
            " domain to explain what a 'stream tag' is and how tags move"
            " through a flowgraph. Summarize what the documentation says."
            " Don't change the graph."
        ),
        "expect": {"mode": "read"},
    },
    {
        "name": "14_build_chain_from_scratch",
        "fixture": "tests/data/empty.grc",
        "prompt": (
            "Inspect the flowgraph — right now it's empty except for the"
            " samp_rate variable. Build a minimal signal chain: a signal"
            " source called `sig` (type float, freq 1000, amp 0.5, using"
            " samp_rate), a throttle called `throttle` (type float,"
            " samples_per_second using samp_rate), and a null sink called"
            " `sink` (type float). Wire the source into the throttle, and"
            " the throttle into the sink. Inspect to confirm the chain is"
            " valid."
        ),
        "expect": {
            "blocks_present": [["sig", "sig_source"], "throttle", "sink"],
            "valid": True,
        },
    },
    {
        "name": "21_type_conversion_and_conjugate",
        "fixture": "tests/data/resampler_demo.grc",
        "prompt": (
            "Inspect the flowgraph. I want to make some changes:\n"
            "1. Search the catalog for a block that converts a float stream"
            " into a complex stream, and also for a block that computes the"
            " complex conjugate of a complex signal.\n"
            "2. The FM modulator in this chain isn't needed anymore —"
            " replace it entirely with the float-to-complex converter you"
            " found. Call the converter `float_to_complex_converter`.\n"
            "3. Wire the throttle's output into the converter's real-part"
            " input.\n"
            "4. Search the catalog for a constant source block. Add one,"
            " call it `zero_imag`, type float, constant value 0.0, and wire"
            " it into the converter's imaginary-part input so the converter"
            " has a valid complex input.\n"
            "5. Connect the converter's output to both the resampler and"
            " the original spectrum display that the FM modulator used to"
            " feed.\n"
            "6. Add the complex conjugate block, call it `signal_conjugate`,"
            " and insert it right after the resampler, before the resampled"
            " spectrum display — so the resampler's output goes through the"
            " conjugate block before reaching that display.\n"
            "7. Remove the old FM modulator block entirely, make sure the"
            " flowgraph is valid, and inspect it to confirm."
        ),
        "expect": {
            "blocks_present": ["float_to_complex_converter", "zero_imag", "signal_conjugate"],
            "blocks_absent": ["analog_frequency_modulator_fc_0"],
            "valid": True,
        },
    },
]


# 2) Build a fresh GrcAgent from a temp copy of the fixture (mirrors _fresh_agent, ~8 lines).
def fresh_agent(fixture):
    tmp = Path(tempfile.mkdtemp()) / Path(fixture).name
    shutil.copy2(fixture, tmp)
    s = FlowgraphSession(); s.load(str(tmp))
    cfg = dataclasses.replace(default_app_config().llama, model=MODEL)
    agent = GrcAgent(session=s, llama_config=cfg)
    agent.warmup_vector_index() # Safe, idempotent warmup for sqlite-vec indexes
    return agent, tmp


# 3) The 5 tools, schemas verbatim via Tool.from_schema, forwarding to execute_tool (~15 lines).
def grc_tools() -> list[Tool[GrcAgent]]:
    out = []
    for sc in build_tool_schemas():
        f = sc["function"]

        def make(nm):
            async def run(ctx: RunContext[GrcAgent], **kw):
                return json.dumps(ctx.deps.execute_tool(nm, kw, model_tool_call=True))
            return run

        out.append(Tool.from_schema(
            function=make(f["name"]), name=f["name"],
            description=f["description"], json_schema=f["parameters"], takes_ctx=True,
        ))
    return out


# 4) Bundle tools and system prompt into a native Capability (~10 lines).
def get_grc_capability() -> Capability[GrcAgent]:
    return Capability(
        id="grc_core",
        instructions=lambda ctx: build_system_prompt(ctx.deps.chat_session_id),
        tools=grc_tools()
    )


# 5) Boundary-safe sliding window history processor (~12 lines).
def prune_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    if len(messages) <= 12:
        return messages
    target = len(messages) - 10
    for i in range(target, 0, -1):
        msg = messages[i]
        if isinstance(msg, ModelRequest):
            if any(isinstance(p, UserPromptPart) for p in msg.parts):
                return [messages[0]] + messages[i:]
    return messages


# 6) Per scenario: PydanticAI runs the loop (~15 lines).
for sc in SCENARIOS:
    grc, fixture_path = fresh_agent(sc["fixture"])
    agent = Agent(
        OllamaModel(MODEL, provider=OllamaProvider(base_url=OLLAMA_V1)),
        deps_type=GrcAgent,
        capabilities=[
            get_grc_capability(),
            ProcessHistory(prune_history)
        ],
        model_settings=ModelSettings(extra_body={"think": True}),
    )
    res = agent.run_sync(sc["prompt"], deps=grc)
    verdict = check_expect(fixture_path, sc["expect"], run_result=res)
    print(sc["name"], verdict)


# 7) Verbatim expect check from tests/agent_flow/run_agent_flow.py (~35 lines).
def check_expect(fixture_path, expect, run_result=None):
    fg = load_flow_graph(fixture_path)
    valid = bool(validate(fg).native_ok)
    snap = render_flow_graph(fg, mode="overview")
    names = {b.instance_name for b in snap.blocks}
    params = {b.instance_name: dict(b.params) for b in snap.blocks}
    states = {b.instance_name: b.state for b in snap.blocks}
    
    fail_reasons = []
    mode = expect.get("mode", "edit")
    
    if mode == "read":
        # Read-only task: success = a read/answer tool was used + a non-empty answer.
        has_read_tool = False
        if run_result:
            from pydantic_ai.messages import ToolCallPart
            for msg in run_result.all_messages():
                if hasattr(msg, 'parts'):
                    if any(isinstance(p, ToolCallPart) and p.tool_name in ("query_knowledge", "inspect_graph") for p in msg.parts):
                        has_read_tool = True
        if not has_read_tool:
            fail_reasons.append("no read tool used")
        if not run_result or not run_result.output:
            fail_reasons.append("empty answer")
    else:
        for blk in expect.get("blocks_present") or []:
            if isinstance(blk, (list, tuple)):
                if not any(alt in names for alt in blk):
                    fail_reasons.append(f"missing block (one of {blk})")
            else:
                if blk not in names:
                    fail_reasons.append(f"missing block {blk}")
        for blk in expect.get("blocks_absent") or []:
            if blk in names:
                fail_reasons.append(f"block {blk} still present")
        if "valid" in expect and valid != bool(expect["valid"]):
            fail_reasons.append(f"graph valid={valid} expected {expect['valid']}")
        for inst, st in (expect.get("states") or {}).items():
            if str(states.get(inst, "")) != str(st):
                fail_reasons.append(f"state {inst}={states.get(inst)!r} expected {st!r}")
        for inst, pv in (expect.get("params") or {}).items():
            actual = params.get(inst, {})
            for k, v in pv.items():
                actual_val = str(actual.get(k, "")).replace(" ", "")
                expected_val = str(v).replace(" ", "")
                if actual_val == expected_val:
                    continue
                try:
                    numeric_match = float(actual_val) == float(expected_val)
                except ValueError:
                    numeric_match = False
                if not numeric_match:
                    fail_reasons.append(f"param {inst}.{k}={actual.get(k)!r} expected {v!r}")
                    
    return {"pass": not fail_reasons, "reasons": fail_reasons, "valid": valid}
```

Output: per-scenario pass/fail + reasons printed, plus a small `METRICS.md`.

---

## 8. Setup & run

```bash
uv add pydantic-ai                  # pin exact version
ollama list                         # confirm qwen3.6:35b-a3b-q4_K_M is pulled
uv run python PydanticAI_experiment/run.py
```

Optional env overrides (read inside `run.py` if desired):
```bash
GRC_AGENT_PAI_MODEL=qwen3.6:35b-a3b-q4_K_M \
GRC_AGENT_PAI_SCENARIOS=01,06,21 \
uv run python PydanticAI_experiment/run.py
```

---

## 9. Implementation steps

1. Write `PydanticAI_experiment/run.py` (clean, minimal, single file).
2. `uv add pydantic-ai` (pin exact version).
3. Verify backend: `ollama list` shows the model; `curl -s http://localhost:11434/v1/models`
   responds. Ensure `src/grc_agent/vectors` contains the prebuilt databases (`catalog_ollama.db` and `docs_ollama.db`).
4. Smoke-test **scenario 01** alone: confirm `inspect_graph` + `change_graph`
   round-trip through PydanticAI and the `.grc` mutates on disk.
5. Run the subset (01, 11, 06, 09, 14, 21) → capture `METRICS.md`.
6. Verify scored topology by re-opening each mutated `.grc` (evidence from disk truth).
7. Compare to the `ToolAgents` baseline in `docs/MODEL_EVAL.md` for these scenarios.

---

## 10. Caveats (flagged, resolved during implementation)

- **`Tool.from_schema(takes_ctx=True)`** — Setting `takes_ctx=True` is fully supported
  by PydanticAI's native `Tool.from_schema`. Standard argument validation is skipped by
  the framework, and arguments are passed directly as keyword arguments. Custom validation
  can be added via `args_validator` if needed, but is not required here because GRC's
  `execute_tool` already handles all required validation.
- **`extra_body={'num_ctx': …}`** — the codebase previously found Ollama's `/v1`
  endpoint ignores per-request `num_ctx`. The sliding window pruner via `ProcessHistory`
  makes context compaction robust. If a larger fixed context is required, bake `num_ctx`
  into a custom Ollama Modelfile.
- **Stuck-loop safety ceiling** — PydanticAI has no built-in stuck-loop ceiling. As per
  the "no hand-written runtime handling" goal, we rely on the model converging or hitting
  PydanticAI's default retry bounds.
- **`web_search` / `web_fetch`** — Kept registered for surface fidelity, but not executed
  by this local Ollama experiment (which uses `query_knowledge` instead).

---

## 11. Out of scope

- GUI integration.
- Replacing the production `ToolAgents` path.
- New tool schemas or schema fields.
- OpenRouter (stays Ollama-only for a clean comparison).
- Full 21-scenario sweep (expand after the subset validates the approach).
- `pydantic-evals` package for verification (not a good fit since correctness requires verifying on-disk graph topology).
