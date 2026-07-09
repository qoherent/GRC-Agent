# PydanticAI Experiment Testing Plan

This document outlines the testing strategy for the `PydanticAI_experiment` directory. The goal is to verify correctness with a minimal set of highly effective tests, keeping unit and integration tests strictly separated.

---

## 1. Unit Tests (Deterministic Core Logic)
These tests execute code in `grc_adapter.py` directly. They do not invoke the LLM or run agent loops. They verify file operations, core GRC platform interactions, sqlite-vec query retrieval, and transaction integrity.

All unit tests will live in `PydanticAI_experiment/test_unit.py`.

### Test Cases
1. **`test_load_and_inspect_graph`**
   - **Target**: `load_flow_graph()` and `inspect_graph()`
   - **Verification**: Loads `dial_tone.grc`, verifies that metadata matches (e.g. sample rate, title, number of block instances), and checks that parameter Stage A/B filtering is applied correctly.
   - **Targeted Inspection**: Verifies that passing a `targets` list of block instance names to `inspect_graph()` scopes the result to only those blocks and their connections.

2. **`test_change_graph_success`**
   - **Target**: `change_graph()` (mutation application)
   - **Verification**: Applies a batch update to add a block, edit its parameter, and connect it. Checks that `is_valid()` returns true and that changes persist to the file.

3. **`test_change_graph_atomic_rollback`**
   - **Target**: `change_graph()` (transaction safety)
   - **Verification**: Submits a batch mutation that fails validation (e.g. adding a block without connecting its required ports). Verifies that the tool returns `ok=False`, GRC validation errors are caught, and the active flowgraph is cleanly rolled back to its exact pre-mutation state on disk.

4. **`test_query_catalog_retrieval`**
   - **Target**: `query_catalog()` (sqlite-vec + GRC description)
   - **Verification**: Searches for "sine source" or "multiply". Verifies that KNN similarity matching fetches the expected block IDs (`analog_sig_source_x`, `blocks_multiply_xx`), and that port/parameter schemas are generated dynamically.

5. **`test_web_tools_fail_soft`**
   - **Target**: `web_search()` and `web_fetch()`
   - **Verification**: Verifies that when `OLLAMA_API_KEY` is not present in the environment, the functions fail soft returning `ok=False` and `missing_api_key` without raising Python exceptions.

---

## 2. Integration / Scenario Tests (End-to-End Agent Loops)
These tests evaluate the PydanticAI agent harness, model intelligence, tool usage, history compaction, and expect criteria. They make live LLM calls to the local `qwen3.6:35b-a3b-q4_K_M` model.

All integration tests will live in `PydanticAI_experiment/test_integration.py`.

### Test Cases
1. **`test_scenario_01_add_throttle`**
   - **Target**: Full agent loop modifying `dial_tone.grc`.
   - **Prompt**: Add a throttle block inline between the 350 Hz source and the adder.
   - **Assertion**: Verifies that `check_expect()` passes (throttle block is present and inline, validation is valid) and the run markdown log is saved to `PydanticAI_experiment/output/01_add_throttle.md`.

2. **`test_scenario_11_scoped_inspect_and_update`**
   - **Target**: Full agent loop with scoped view and parameter edit.
   - **Prompt**: Inspect only the sample rate block and tone source, then update sample rate to 96000.
   - **Assertion**: Verifies that `check_expect()` passes (`samp_rate` is 96000, validation is valid) and the run markdown log is saved to `PydanticAI_experiment/output/11_scoped_inspect_and_update.md`.

---

## 3. Test Runner
We will run both suites using `pytest`:

```bash
# Run unit tests only
uv run pytest PydanticAI_experiment/test_unit.py

# Run integration tests only
uv run pytest PydanticAI_experiment/test_integration.py
```
