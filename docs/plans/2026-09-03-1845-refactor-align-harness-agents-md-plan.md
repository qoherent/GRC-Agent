---
title: "refactor: Align Planning Harness and Context Management with AGENTS.md"
date: "2026-09-03"
artifact_contract: "ce-unified-plan/v1"
artifact_readiness: "implementation-ready"
product_contract_source: "ce-plan-bootstrap"
execution: "code"
---

# refactor: Align Planning Harness and Context Management with AGENTS.md

## Product Contract

### Summary
Refactor the GRC Agent planning harness and context management to achieve strict compliance with `AGENTS.md` architectural commandments. Eliminate downstream regex text scraping, remove ad-hoc synonym lists, eliminate silent message history transformations, and ground all tool validation and context compaction in standard library mechanisms from `pydantic_ai` and `pydantic-ai-harness`.

### Problem Frame
During an audit of recent planning improvements against `AGENTS.md`, two implementations were identified that violate core architectural commandments:
1. `extract_plan_from_text` introduced downstream regex parsing (`### Step \d+ [-—–:]`) to scrape structured plans from chat prose when the model failed to invoke `write_plan`, violating §1 (*Zero Ad-Hoc Heuristics & Zero Folklore* and *Fix at the Source*).
2. `_sanitize_history_for_executor` introduced silent in-memory filtering that dropped tool calls and retry prompts during mode handoff, violating §1 (*No Brittle Reinventions / Always Use Standard Libraries*) and §3 (*No Silent Transformations or Hidden Truncation*).
3. `coerce_plan_items` included hardcoded synonym lists (`"doing"`, `"finished"`, `"task"`, `"step"`), violating §1 (*Zero Ad-Hoc Heuristics*).

By fixing the root cause at the tool ingestion boundary (proper JSON string coercion and explicit `ModelRetry` compiler feedback), the model successfully invokes `write_plan` without retry storms or plan stranding, making downstream scrapers and silent context stripping completely unnecessary.

### Requirements
- **R1**: Retain uniform JSON string decoding (`if isinstance(v, str): json.loads(v)`) via Pydantic v2 `BeforeValidator` on `write_plan` input.
- **R2**: Enforce standard `PlanItem` schema validation without hardcoded English synonym chains (`"doing"`, `"finished"`, `"todo"`). Coerce numeric IDs to strings (`item_id = str(...)`) to prevent Pydantic string validation rejections on numeric IDs.
- **R3**: When `write_plan` input fails validation, raise `pydantic_ai.ModelRetry` with a clear, actionable JSON schema template so the model can correct its arguments using its retry budget.
- **R4**: Completely delete `extract_plan_from_text` and `_recover_plan_from_last_message` downstream regex scrapers. The "Implement the Plan" action button must reflect whether a durable plan was saved to the session database at the source.
- **R5**: Completely delete `_sanitize_history_for_executor`. Do not silently strip or mutate message history between Planner and Executor. Context management remains owned by Pydantic AI Harness's native `TieredCompaction` capability (`ClearToolResults` with truthful placeholders and `SlidingWindowCompaction`).
- **R6**: Retain `archive_transcript` in `_implement_durable_plan` to persist pre-handoff transcripts durably in SQLite.
- **R7**: Retain the 120.0s uniform HTTP read timeout in `agent_factory.py`.

### Success Criteria
- Zero ad-hoc regex scrapers or magic string branches exist in `src/grc_agent/chat/`.
- Zero silent message-filtering functions exist in `src/grc_agent/chat/`.
- All tool failures report either `ModelRetry` or `ToolFailed` per `AGENTS.md` §3.
- All unit tests in `tests/test_durable_planning.py`, `tests/test_chat_history.py`, and `tests/test_separate_planner.py` pass hermetically.
- Full test gate (`uv run pytest tests/...`) passes with 0 failures and `uv run ruff check` passes cleanly.

### Scope Boundaries
- **In scope**: Refactoring `src/grc_agent/agent_factory.py`, `src/grc_agent/chat/history.py`, `src/grc_agent/chat/session.py`, `src/grc_agent/chat/turn_driver.py`, and their corresponding unit tests.
- **Out of scope**: Changing GRC flowgraph mutation tools (`change_graph`, `inspect_graph`), altering GTK UI widgets, or touching compaction tier thresholds outside `agent_factory.py`.

---

## Planning Contract

### Key Technical Decisions
- **KTD1: Uniform Tool Input Normalization at Source**: `coerce_plan_items` will apply only two uniform rules: (1) string inputs are decoded with `json.loads`; (2) dict items stringify `id` if present and ensure `content` is populated (falling back to standard field name `name` if present). Any invalid structure raises `ModelRetry` with an explicit example.
- **KTD2: Removal of Post-Processing Regex Scrapers**: Eliminate `extract_plan_from_text` and `_recover_plan_from_last_message`. Correctness lives exclusively in `write_plan`. If no plan is written, `load_plan_items(session_id)` returns empty, and the UI does not show the handoff button.
- **KTD3: Truthful History Preservation without Silent Mutations**: Eliminate `_sanitize_history_for_executor`. When the user clicks "Implement the Plan", the session history is preserved in-place. Pydantic AI Harness `TieredCompaction` handles any oversized tool outputs via standard `ClearToolResults`, leaving explicit, truthful placeholders rather than silently dropping turns.

### High-Level Technical Design

```mermaid
flowchart TD
    subgraph Tool Boundary [Tool Input Ingestion at Source (agent_factory.py)]
        LLM[LLM Tool Call: write_plan] --> BV[Pydantic BeforeValidator: coerce_plan_items]
        BV -->|JSON String| JL[json.loads]
        BV -->|Array of Dicts| NV[Normalize: id->str, name->content]
        JL --> NV
        NV -->|Valid PlanItem| PS[SqlitePlanStore / set_items]
        NV -->|Invalid Schema| MR[Raise ModelRetry with Schema Guidance]
        MR -->|Retry Budget| LLM
    end

    subgraph Mode Handoff [Handoff Boundary (session.py)]
        PS --> DB[(chat_sessions.db: plan_items)]
        DB -->|load_plan_items| HB{Has Durable Plan?}
        HB -->|Yes| IPB[Show Implement Plan Button]
        HB -->|No| NOP[No Handoff Button]
        IPB -->|User Clicks| ARCH[archive_transcript to SqliteStepStore]
        ARCH --> EX[Select Executor & Send Implementation Message]
        EX --> TC[Native TieredCompaction / ClearToolResults]
    end
```

---

## Implementation Units

### U1. Clean Tool Ingestion in `agent_factory.py`
- **Goal**: Refactor `coerce_plan_items` to eliminate ad-hoc status synonym mappings and English folklore while preserving uniform JSON string parsing and `ModelRetry` compiler feedback.
- **Requirements**: R1, R2, R3
- **Dependencies**: None
- **Files**:
  - `src/grc_agent/agent_factory.py`
  - `tests/test_durable_planning.py`
- **Approach**:
  1. In `coerce_plan_items(v)`:
     - If `isinstance(v, str)`: attempt `json.loads(v)`. On JSONDecodeError, raise `ModelRetry` with clear JSON step format.
     - If `isinstance(v, list)`: for each item, if string convert to `{"content": item}`; if dict, stringify `id` if present, and map `name` to `content` if `content` is missing. Validate via standard `PlanItem` schema.
     - On any validation failure, raise `ModelRetry` with actionable feedback.
     - Remove all magic status string checks (`"todo"`, `"doing"`, `"done"`, `"finished"`).
- **Patterns to follow**: `AGENTS.md` §1 ("Uniform mathematical or algorithmic rule"), `AGENTS.md` §3 ("Uniform Error Reporting").
- **Test scenarios**:
  - `test_coerce_plan_items_json_string`: JSON string containing list of dicts is correctly decoded and validated.
  - `test_coerce_plan_items_plain_strings`: List of plain strings is normalized to `PlanItem(content=...)`.
  - `test_coerce_plan_items_int_id_coerced`: Integer `id` (e.g. `1`) is stringified to `"1"`.
  - `test_coerce_plan_items_invalid_raises_model_retry`: Invalid JSON or invalid dict types raise `ModelRetry` containing the schema instructions.
- **Verification**: `uv run pytest tests/test_durable_planning.py` passes.

---

### U2. Delete Downstream Regex Scrapers
- **Goal**: Remove `extract_plan_from_text` and `_recover_plan_from_last_message`, adhering to "Fix at the Source" and "Zero Ad-Hoc Heuristics".
- **Requirements**: R4
- **Dependencies**: U1
- **Files**:
  - `src/grc_agent/chat/history.py`
  - `src/grc_agent/chat/session.py`
  - `tests/test_chat_history.py`
  - `tests/test_durable_planning.py`
- **Approach**:
  1. In `src/grc_agent/chat/history.py`: Delete `extract_plan_from_text`.
  2. In `src/grc_agent/chat/session.py`:
     - Delete `_recover_plan_from_last_message`.
     - In `_show_implement_plan_if_ready`: check `items = await load_plan_items(session_id)`. If items exist, render the button; if not, do nothing.
  3. In `tests/test_chat_history.py`: Remove `test_extract_plan_from_text_variants`.
  4. In `tests/test_durable_planning.py`: Remove `test_text_plan_fallback_recovery_in_session`.
- **Patterns to follow**: `AGENTS.md` §1 ("Fix at the Source: Correctness lives in the tool or handler that produces data, not in a downstream post-processing filter").
- **Test scenarios**:
  - Verify `_show_implement_plan_if_ready` only renders when `load_plan_items(session_id)` is non-empty.
  - Verify planner tests (`tests/test_separate_planner.py`) pass with source-level `write_plan` tool execution.
- **Verification**: `uv run pytest tests/test_separate_planner.py tests/test_durable_planning.py` passes.

---

### U3. Delete Silent History Stripping in Mode Handoff
- **Goal**: Remove `_sanitize_history_for_executor` to prevent silent message dropping, relying on standard `archive_transcript` and native `TieredCompaction`.
- **Requirements**: R5, R6
- **Dependencies**: U1
- **Files**:
  - `src/grc_agent/chat/history.py`
  - `src/grc_agent/chat/session.py`
  - `tests/test_chat_history.py`
- **Approach**:
  1. In `src/grc_agent/chat/history.py`: Delete `_sanitize_history_for_executor`.
  2. In `src/grc_agent/chat/session.py`:
     - In `_implement_durable_plan`: retain `await archive_transcript(...)`, remove `self._message_history = _sanitize_history_for_executor(...)`.
  3. In `tests/test_chat_history.py`: Delete `test_sanitize_history_for_executor_prunes_tool_debris_and_merges`.
- **Patterns to follow**: `AGENTS.md` §3 ("No Silent Transformations or Hidden Truncation"), `AGENTS.md` §1 ("PydanticAI owns the agentic loop, tool dispatch, and message history").
- **Test scenarios**:
  - Verify that `_implement_durable_plan` archives the transcript and transitions to executor without mutating message objects.
  - Verify chat history tests in `tests/test_chat_history.py` pass.
- **Verification**: `uv run pytest tests/test_chat_history.py tests/test_chat_sidebar.py` passes.

---

### U4. Test Gate and Lint Verification
- **Goal**: Verify the entire test suite and ruff linter pass cleanly with zero regressions.
- **Requirements**: R1 through R7
- **Dependencies**: U1, U2, U3
- **Files**:
  - All modified files
- **Approach**:
  1. Run `uv run ruff check` and fix any unformatted imports.
  2. Run `uv run pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py`.
  3. Verify all 600+ tests pass hermetically.
- **Verification**: Full fast test gate passes 100% clean.

---

## Verification Contract
- **Automated Tests**:
  - `uv run pytest tests/test_durable_planning.py tests/test_separate_planner.py tests/test_chat_history.py`
  - `uv run pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py`
- **Linter**:
  - `uv run ruff check`

## Definition of Done
- Zero ad-hoc regex scrapers exist in `src/grc_agent/`.
- Zero silent history-dropping functions exist in `src/grc_agent/`.
- Tool validation uniformly relies on Pydantic v2 and `ModelRetry`.
- Test suite passes with 0 failures and 0 linter errors.
- Changes committed directly to `main` with conventional message.
