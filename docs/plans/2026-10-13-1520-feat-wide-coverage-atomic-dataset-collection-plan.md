---
title: Wide-Coverage Atomic Dataset Collection Harness - Plan
type: feat
date: 2026-10-14
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Wide-Coverage Atomic Dataset Collection Harness - Plan

## Goal Capsule

- **Objective:** Any engineer or automated script can run a high-throughput, reproducible dataset collection command that drives single-turn user prompts ("one-shot" tasks) through the real GRC Agent harness (`build_agents_from_cfg(cfg).executor`) against a fast local model endpoint, faithfully capturing all intermediate grounding tool calls (`inspect_graph`, `query_knowledge`), mutations (`change_graph`), guardrail errors/retries (`RetryPromptPart`, `ToolFailed`, `validate_flowgraph_state`), reasoning traces (`ThinkingPart` / `reasoning_content`), and verified headless GRC compiler outputs in Unsloth-ready OpenAI `messages` + `tools` JSONL format.
- **Means:** A staged, non-ad-hoc harness-driven pipeline (`experiments/atomic_dataset/`) combining deterministic GRC catalog taxonomy seeds, real Pydantic AI executor harness runs with `RecordingOpenAIChatModel`, native `OpenAIChatModel._map_messages` / `ModelMessagesTypeAdapter` serialization, and headless in-memory GRC validation. (KTD1–KTD5)
- **Authority hierarchy:** `AGENTS.md` invariants > this plan's KTDs > unit approaches. Core test gate (`AGENTS.md` §6) applies unchanged.
- **Stop conditions:** All implementation units pass hermetic tests; 5 live scenarios against `http://192.168.0.234:9000/v1` (`qwen3.8-27b`) are executed and personally inspected to verify faithful capture of intermediate tool calls, reasoning traces, and guardrail retries; exported JSONL validates cleanly for Unsloth SFT.
- **Execution profile:** Modular build under `experiments/atomic_dataset/`, hermetic unit tests, and live verification against the local endpoint.
- **Tail ownership:** All new code lives strictly under `experiments/atomic_dataset/`. No production code in `src/grc_agent/` is modified.

---

## Product Contract

### Summary

Build a faithful, harness-driven one-shot trajectory collection pipeline that traverses the GNU Radio Companion catalog (~584 blocks, parameter types, port types) and agent tool surface (`inspect_graph`, `query_knowledge`, `change_graph`, `generate_python`, `run_flowgraph`, `get_run_log`, `save_graph`, `save_block`, filesystem, and shell tools). Each scenario issues a single realistic user prompt (e.g., *"Add a cosine signal source block with frequency X and amplitude Y..."* or *"Change parameter X for block D and set it to Y"*) to the real GRC Agent executor harness. Within that single user turn, the agent naturally performs intermediate grounding calls (`inspect_graph`, `query_knowledge`), reasoning (`reasoning_content`), graph edits (`change_graph`), and self-correction when harness guardrails fire (`ModelRetry` / `RetryPromptPart`, `validate_flowgraph_state`, `ToolFailed`). Every intermediate step, reasoning trace, tool output, and guardrail error is recorded losslessly using Pydantic AI's native serialization.

### Problem Frame

Two opposite failure modes existed in prior collection attempts:
1. **Legacy Multi-Turn Simulator Campaigns (`experiments/dataset_collection/`)**: Required 20–30 conversational turns with an LLM user simulator driving the full GTK GUI under `xvfb-run`, costing minutes per task and exercising only ~15–20 blocks. Moreover, in multi-turn conversations, Qwen3's chat template strips `<think>` blocks from all turns prior to the last user message.
2. **Ad-Hoc "Single-Tool" Shortcuts (`run_oneshot_scenario.py` / raw API synthesis)**: Attempting to bypass the harness with plain API calls or injecting ad-hoc prompt rules (`"Do NOT call inspect_graph or query_knowledge before acting"`) violates `AGENTS.md` §1 (Zero Ad-Hoc Heuristics) and contradicts `build_system_prompt()`, training the model to guess block schemas without grounding and dropping `RetryPromptPart` guardrail feedback.

Driving a **single user prompt** ("one-shot task") through the **real GRC Agent harness** solves both problems at once:
- Because there is only one `role: "user"` prompt at the start of the trajectory, 100% of intermediate `assistant` steps (`inspect_graph` -> `query_knowledge` -> `change_graph` -> guardrail recovery -> `final_result`) retain their `reasoning_content` (`<think>...</think>`) under Qwen3/Unsloth chat templates.
- Every tool call runs headlessly in memory against GNU Radio's Python API (`load_flow_graph`, `change_graph`, `inspect_graph`, `query_catalog`, `query_docs`) in milliseconds without X11/GTK.
- Every harness guardrail (`JsonRepairCapability`, Pydantic schema validation, `change_graph` transactional rollback + `ModelRetry`, `validate_flowgraph_state` end-of-turn validation, `ToolFailed` terminal errors, `StopGracefully`) operates identically to production.

### Key Decisions

- **Harness-Driven Execution Only (Zero Raw API Calls, Zero Ad-Hoc Prompt Overrides)**: Every scenario runs through `build_agents_from_cfg(cfg).executor` with the unmodified production system prompt (`build_system_prompt`) and dynamic instructions (`add_active_flowgraph_context`). Intermediate grounding calls (`inspect_graph`, `query_knowledge`) and post-edit verification calls (`inspect_graph`, `generate_python`) occur naturally. Governs R1, R2, R3.
- **Pydantic AI Native Wire & Canonical Serialization**: Rather than hand-rolling message conversion loops that silently drop `RetryPromptPart` or `ToolFailed`, the collector uses `RecordingOpenAIChatModel` (`OpenAIChatModel._map_messages` and `_get_tool_choice`) for wire-exact OpenAI `messages` and `tools`, alongside `ModelMessagesTypeAdapter.dump_json` (`serialize_messages`) for canonical Pydantic AI message persistence. Governs R4, R5, R6.
- **First-Class Guardrail & Error-Recovery Capture**: Guardrail interventions (`RetryPromptPart` from schema validation, tool `ModelRetry`, or `validate_flowgraph_state`, and `ToolReturnPart(outcome='failed')` from `ToolFailed`) are preserved both on the wire (`role: "tool"`) and in structured `guardrail_events` metadata. Governs R4, R7.

### Requirements

- **R1 (Real Harness Execution)**: Drive every scenario through `build_agents_from_cfg(cfg).executor` with isolated `GRC_AGENT_ENV` and `set_active_graph_providers`, sharing a single `RunUsage` across `DeferredToolRequests` approval resumes.
- **R2 (Natural Multi-Step Tool Trajectories)**: Support arbitrary intermediate tool calls within a single user turn (`inspect_graph`, `query_knowledge`, `change_graph`, `generate_python`, `run_flowgraph`, `get_run_log`, `save_graph`, `save_block`, filesystem, and shell tools) without ad-hoc prompt restrictions.
- **R3 (Realistic Scenario Coverage)**: Support realistic prompts spanning block addition with parameter configuration and wiring, parameter retuning on existing blocks, catalog discovery before block insertion, guardrail error recovery (`ModelRetry` / `ToolFailed` / `validate_flowgraph_state`), and multi-step rewiring + Python code generation preview.
- **R4 (Faithful Guardrail & Intermediate Trace Capture)**: Record all intermediate `ThinkingPart` (`reasoning_content`), `TextPart`, `ToolCallPart`, `ToolReturnPart` (including `outcome='failed'`), and `RetryPromptPart` (schema validation errors, domain `ModelRetry`, output validator `ModelRetry`).
- **R5 (Wire-Exact OpenAI Export + Frozen Tool Schemas)**: Export Unsloth-ready JSONL where each record contains `messages` (mapped via `OpenAIChatModel._map_messages`, ending on the final `assistant` turn), `tools` (the exact 23 tool definitions mapped via `OpenAIChatModel._get_tool_choice`), `system`, `usage`, `guardrail_events`, and `flowgraph_validation`.
- **R6 (Canonical Lossless Persistence)**: Persist the canonical Pydantic AI `ModelMessage` list via `ModelMessagesTypeAdapter.dump_json` (and `StepPersistence` in the isolated SQLite DB) so every trace can be losslessly round-tripped back into Pydantic AI `ModelMessage` objects.
- **R7 (Completeness & Structural Verification)**: Verify every collected trace for structural invariants: non-empty system instructions, every intermediate `tool_call` answered by a matching `role: "tool"` return or retry prompt, `reasoning_content` captured on reasoning steps, final message has `role == "assistant"`, and headless GRC `inspect_graph(fg)` validation status recorded.

---

## Planning Contract

### Key Technical Decisions

- **KTD1. Production Executor Reuse via `build_agents_from_cfg` + `executor.override()`**: We construct the production `AgentBundle` via `build_agents_from_cfg(cfg)` under an isolated `GRC_AGENT_ENV` directory and override the model instance via `executor.override(model=recording_model, model_settings=ModelSettings(max_tokens=max_tokens))`. This guarantees the exact production tool surface, capabilities (`JsonRepairCapability`, `StopGracefully`, `StepPersistence`, `GrcFileSystem`, `GrcShell`, `PromptInjectionDefender`), instructions, and output validator (`validate_flowgraph_state`).
- **KTD2. `RecordingOpenAIChatModel` for Wire-Accurate Schema and Message Mapping**: A 10-line subclass of `OpenAIChatModel` records `last_request_parameters` and `last_model_settings` in `prepare_request()`. After the turn completes, `model._get_tool_choice()` and `await model._map_messages()` produce the exact OpenAI `tools` and `messages` payloads without custom AST/message walking.
- **KTD3. Trimming Only the Post-Run Mock `final_result` ToolReturnPart on the Wire**: When the agent concludes via `GrcAgentResponse` (`final_result` output tool), Pydantic AI appends a trailing mock `ModelRequest(parts=[ToolReturnPart(tool_name="final_result", content="Final result processed.")])` after the final LLM call for multi-turn continuation. In `canonical_messages`, the full `res.all_messages()` list is preserved; in `messages` (the OpenAI SFT trajectory), that post-run mock `ToolReturnPart` is excluded so the training trajectory ends on the final `role: "assistant"` completion while keeping any mid-turn failed `final_result` `RetryPromptPart` intact.
- **KTD4. Deterministic Catalog Taxonomy & Context Scaffolding**: `taxonomy.py` introspects all ~584 blocks via `get_platform()` to generate seed specs across categories, parameter types, and scaffolding graphs (`dial_tone.grc`, empty canvas, source-present chains) so prompts can be scaled across the full GNU Radio catalog.

### Output Structure

```
experiments/atomic_dataset/
├── __init__.py
├── harness_driver.py       # RecordingOpenAIChatModel, real executor harness runner, trace & guardrail extractor
├── scenarios.py            # Realistic scenario definitions & seed builders (including the 5 benchmark scenarios)
├── taxonomy.py             # Full ~584-block GRC catalog introspection & taxonomy seed generator
├── verify_traces.py        # Completeness & structural invariant verifier for collected traces
├── run_scenarios.py        # CLI runner for executing scenarios and exporting Unsloth JSONL + inspection reports
└── tests/
    ├── __init__.py
    └── test_harness_driver.py  # Hermetic unit tests (intermediate calls, ModelRetry, ToolFailed, validate_flowgraph_state, roundtrip)
```

---

## Verification Contract

```bash
# 1. Hermetic unit tests for the atomic dataset harness (mock model exercising intermediate calls + all guardrails)
uv run --python .venv/bin/python pytest experiments/atomic_dataset/tests/ -q

# 2. Run the 5 live realistic scenarios against the local endpoint (http://192.168.0.234:9000/v1, qwen3.8-27b)
uv run --python .venv/bin/python python -m experiments.atomic_dataset.run_scenarios

# 3. Verify collected traces against all structural & SFT invariants
uv run --python .venv/bin/python python -m experiments.atomic_dataset.verify_traces experiments/atomic_dataset/output/sft_trajectories.jsonl

# 4. Repo-wide linter and fast unit test gate
uv run ruff check
uv run --python .venv/bin/python pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py -q
```
