---
artifact_contract: "ce-handoff/v1"
created_at: "2026-10-14T10:30:00Z"
title: "Wide-Coverage Atomic One-Shot Dataset Collection Harness"
summary: "Systematic generation and headless in-memory verification of single-user-prompt (one-shot) harness-driven tool-use trajectories across GNU Radio Companion blocks, intermediate grounding calls, and guardrail error recoveries."
keywords: ["atomic-dataset", "dataset-collection", "gnuradio", "pydantic-ai", "one-shot-tool-use", "harness-driven", "guardrail-traces"]
cwd: "/home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent"
resume_focus: "Drive one-shot user prompts through the real GRC Agent harness, faithfully capturing all intermediate tool calls, reasoning traces, and guardrail retries."
repository: "GRC_Agent"
repo_root_sha: "0a5f2d0ae0253ec6b754c02079b6350900fef572"
branch: "main"
head: "7f987a5"
---

# Wide-Coverage Atomic One-Shot Dataset Collection Harness

## 1. Executive Context & User Intent

The goal of this initiative is to build a high-breadth, high-throughput, reproducible dataset collection harness under `experiments/atomic_dataset/`. The target deliverable is a verified, Unsloth-ready fine-tuning corpus covering the GNU Radio Companion catalog (~584 blocks, parameter types, port signatures, and agent tool methods).

### Critical User Direction: Realistic Harness-Driven One-Shot Prompts
- **Definition of "One-Shot"**: Each scenario consists of **one realistic user prompt** (a single user turn, e.g., *"Add a cosine signal source block with frequency X and amplitude Y..."* or *"Change parameter X for block D and set it to Y"*), **NOT** a multi-turn user-simulator conversation, and **NOT** a raw API call without the harness or an ad-hoc prompt hack forbidding intermediate tool calls.
- **Real Harness Execution**: The model must be driven through the production GRC Agent harness (`build_agents_from_cfg(cfg).executor` in `src/grc_agent/agent_factory.py`) with the unmodified production system prompt (`build_system_prompt`) and dynamic instructions (`add_active_flowgraph_context`).
- **Complete Multi-Step Trajectory Within One Turn**:
  1. **User Prompt**: Natural, specific DSP/flowgraph instruction.
  2. **Intermediate Grounding & Inspection**: The model naturally emits reasoning (`ThinkingPart` / `reasoning_content`), calls `inspect_graph` to observe live blocks/ports/parameters, and calls `query_knowledge` to look up block schemas and parameter keys before mutating.
  3. **Mutation & Approval Loop**: The model calls `change_graph` (or other tools), and the harness resolves `DeferredToolRequests` via `ToolApproved()` while sharing a single `RunUsage` across approval resumes.
  4. **Guardrail Errors & Self-Correction**: Any schema validation errors, domain `ModelRetry` exceptions (e.g., invalid port, unknown parameter, unconnected ports), output validator `validate_flowgraph_state` retries (`RetryPromptPart`), or terminal `ToolFailed` results (`ToolReturnPart(outcome='failed')`) are faithfully captured as part of the trajectory.
  5. **Verification & Final Response**: Post-edit verification (`inspect_graph`, `generate_python`) followed by the final assistant response (`GrcAgentResponse` via `final_result` or `str`).
  6. **Export Format**: Wire-accurate OpenAI `messages` + `tools` JSONL mapped via Pydantic AI's own `OpenAIChatModel._map_messages` and `OpenAIChatModel._get_tool_choice`, paired with lossless canonical `ModelMessagesTypeAdapter` serialization.

---

## 2. Infrastructure & Local Endpoint Specifications

The harness connects to a fast local model server on the local network:
- **Base URL**: `http://192.168.0.234:9000/v1` (OpenAI-compatible)
- **Model ID**: `qwen3.8-27b`
- **Authentication**: None required (pass dummy `api_key="not-needed"`).
- **Performance**: ~250 tok/s decode, 2.0-4.6k tok/s prefill, 262,144 context window, concurrency 2.
- **Reasoning Architecture**: Returns native `reasoning_content` inside the assistant message payload, mapped cleanly to Pydantic AI's `ThinkingPart(id='reasoning_content', provider_name='openai')`.
- **Token Budget**: Set `max_tokens` to at least `8192` so multi-step reasoning and tool calls never clip.
- **Critical NInfer Provider Compatibility**:
  - NInfer returns HTTP 400 (`strict_tools_not_supported`) if `'strict': True` is passed in function definitions (`final_result` sets `strict=True` by default).
  - **Verified Fix**: Subclass/initialize `OpenAIChatModel` with `profile=ModelProfile(openai_supports_strict_tool_definition=False)` and apply it to the production executor agent via `executor.override(model=model, model_settings=model_settings)`.

---

## 3. Faithful Collection Architecture

1. **Zero Ad-Hoc Prompting or Serialization**:
   - No `ONESHOT_INSTRUCTION` prompt overrides. The model runs under the exact production `build_system_prompt()` and `add_active_flowgraph_context` instructions.
   - No hand-rolled `_convert_to_openai_messages` loops that drop `RetryPromptPart` or `ToolFailed`.
2. **Wire-Accurate OpenAI Mapping via `RecordingOpenAIChatModel`**:
   - Captures the exact `ModelRequestParameters` and `ModelSettings` prepared by Pydantic AI during the run.
   - Extracts `tools` via `model._get_tool_choice(settings, params)` (all 23 tools: 8 GRC domain tools, 8 filesystem tools, 4 shell tools, 2 web tools, plus `final_result`).
   - Extracts `messages` via `await model._map_messages(wire_messages, params, model_settings=settings)`, preserving:
     - `role: "system"` (full rendered system + capability + active flowgraph instructions)
     - `role: "user"` (the prompt)
     - `role: "assistant"` (`reasoning_content`, `content`, and `tool_calls` across every intermediate and final step)
     - `role: "tool"` (both `ToolReturnPart` outputs including `ToolFailed` `{"error": ...}` payloads and `RetryPromptPart` guardrail error feedback)
3. **Canonical History & Guardrail Telemetry**:
   - Full `ModelMessagesTypeAdapter.dump_json(res.all_messages())` preserved alongside explicit `guardrail_events`, `tool_trajectory`, `usage`, and headless GRC validation (`inspect_graph(fg)`).

---

## 4. Architectural Commandments & Non-Negotiable Invariants (`AGENTS.md`)

- **Isolated Code Changes**: ALL new code lives strictly under `experiments/atomic_dataset/`. Do NOT modify production application code in `src/grc_agent/`.
- **Git Single-Branch Only**: Always work on `main`. Never create feature branches or worktrees.
- **No GUI / xvfb Requirement**: Execution testing runs purely in-memory via GNU Radio Companion's Python API (`get_platform()`, `load_flow_graph()`, `change_graph()`).
- **Python Interpreter Pinning**: Always run using the system-bridged virtual environment:
  ```bash
  uv run --python .venv/bin/python python ...
  ```
- **Lint & Test Gates**: `uv run ruff check` must remain completely green. Fast unit tests (`uv run pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py`) must pass before finalizing changes.
