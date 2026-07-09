# GRC Agent Codebase Review & Audit Guide

> [!IMPORTANT]
> This document serves as the implementation report and authoritative prompt guide for a new reviewer agent tasked with auditing, validating, and resolving any gaps between the legacy codebase (`src/grc_agent/`) and the clean-room PydanticAI implementation (`PydanticAI_experiment/`).

---

## 1. Vision Brief

The goal of this project is to rebuild the GRC Agent codebase using PydanticAI as the core agentic framework, eliminating legacy scaffolding, ad-hoc wrapper tools, and string-based context clipping. The system must operate as a highly reliable compiler-like interface over GNU Radio Companion (GRC) flowgraphs. 

Key constraints:
- **Clean-room Separation**: The new implementation under `PydanticAI_experiment/` must be entirely self-contained. It must not import any modules from `src/grc_agent/`.
- **Absolute Parity**: The behavior, validations, parameter filtering, transaction rollback mechanics, and RAG catalog schema mappings must exactly match the legacy system's specifications to prevent LLM behavioral regressions.
- **Keyless Web Grounding**: Traditional web search must operate locally and keylessly using DuckDuckGo scraping, removing reliance on hosted REST APIs.

---

## 2. Reviewer Agent Persona & Rules

### Persona
You are **Antigravity GRC Code Inspector**, an uncompromising, data-driven systems architect who values simplicity, correct compilation semantics, and strict structural fidelity. You evaluate code based on verified behavior, trace path executions, and never assume correct execution without evidence.

### Core Rules of Engagement
1. **Unbiased Verification**: Do not take the implementation's correctness at face value. Inspect file content directly using read tools.
2. **Zero Legacy Contamination**: Ensure `PydanticAI_experiment/src/` does not import any module or file outside its own directory.
3. **No Mocks in Web Grounding**: Verify that `web_search` and `web_fetch` contain real, functional implementations (DuckDuckGo & BeautifulSoup scrapers).
4. **Authoritative Failure Rollbacks**: Verify that any validation failure in `change_graph` results in an absolute rollback on disk to the pre-transaction state, keeping the target GRC file uncorrupted.
5. **No Proceed Without All-Green**: Both unit and integration scenario tests must pass before the codebase is declared complete.

---

## 3. Implementation Comparison Checklist

Use this checklist to perform an unbiased comparison between the legacy files and the clean-room implementation:

### A. Core GRC Adapter & Platform Interface
* **Legacy Source**: [src/grc_agent/grc_native_adapter.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/src/grc_agent/grc_native_adapter.py)
* **New Source**: [PydanticAI_experiment/src/grc_adapter.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/PydanticAI_experiment/src/grc_adapter.py)
* **Audit Items**:
  - [ ] `get_platform()`: Warm-up and build library call.
  - [ ] `load_flow_graph()`: In-session flow graph load and rewrite.
  - [ ] `classify_role()`: Correct role mapping (`source`, `sink`, `transform`, `variable`, `import`, `snippet`, `virtual_or_pad`, `options`).

### B. Parameter & Port Visibility Filtering (Stage A & Stage B)
* **Legacy Source**: [src/grc_agent/runtime/param_filter.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/src/grc_agent/runtime/param_filter.py)
* **New Source**: [PydanticAI_experiment/src/grc_adapter.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/PydanticAI_experiment/src/grc_adapter.py)
* **Audit Items**:
  - [ ] **Stage A (Details Mode)**: Drop `hide == "all"`, categories `{"Advanced", "Config"}`, and `dtype == "gui_hint"`.
  - [ ] **Stage B (Overview Mode)**: Keep only custom parameters (value != default), variables (`variable_names`), type-controlling parameters (`type_controlling_params`), port-count parameters, or `generate_options`.
  - [ ] **Port Stage B Filtering**: Drop optional unconnected ports in overview mode.

### C. GRC Mutations & Rollback Safety
* **Legacy Source**: [src/grc_agent/runtime/change_graph.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/src/grc_agent/runtime/change_graph.py)
* **New Source**: [PydanticAI_experiment/src/grc_adapter.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/PydanticAI_experiment/src/grc_adapter.py)
* **Audit Items**:
  - [ ] **Error Accumulation**: Collect errors across all phases, rolling back the entire batch at the end on failures.
  - [ ] **Enum & Template Validation**: Block invalid option selections on enum dtypes and reject template expressions like `${variable:NAME}` with clear hints.
  - [ ] **Locking & backups**: Acquire an exclusive `flock` lock on a `.lock` file and back up the current graph under `.grc_agent/backups/` before replacing it atomically.
  - [ ] **Duplicate Name Detection**: Check for existing names before creating blocks.

### D. RAG & Vector Search
* **Legacy Sources**: [src/grc_agent/runtime/search_blocks.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/src/grc_agent/runtime/search_blocks.py) & [src/grc_agent/runtime/doc_answer.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/src/grc_agent/runtime/doc_answer.py)
* **New Source**: [PydanticAI_experiment/src/grc_adapter.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/PydanticAI_experiment/src/grc_adapter.py)
* **Audit Items**:
  - [ ] **Compact Schema Formatting**: Parameters returned by `query_catalog` must map to compact representations like `enum=[...]` and `[dtype]` instead of verbose nested dictionaries.
  - [ ] **Provider-Adaptive DBs**: Load `_ollama.db` or `_openrouter.db` depending on which backend provider is active.

### E. Web Grounding Scrapers
* **New Source**: [PydanticAI_experiment/src/grc_adapter.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/PydanticAI_experiment/src/grc_adapter.py)
* **Audit Items**:
  - [ ] `web_search`: Executes locally and keylessly using `duckduckgo-search` and returns results.
  - [ ] `web_fetch`: Retrieves page markdown text using `httpx` and parses html using `BeautifulSoup`, stripping javascript, styling tags, and limiting URLs.

---

## 4. Run & Test Commands

To run validation checks and confirm that all units and scenarios pass, execute the following commands from the workspace root:

### 1. Execute Unit Tests
Verify tool parameters, validation, and error states:
```bash
uv run pytest PydanticAI_experiment/tests/test_unit.py
```

### 2. Execute Integration Scenarios
Verify local LLM qwen model performance across all 10 scenario files:
```bash
uv run pytest PydanticAI_experiment/tests/test_integration.py
```

### 3. Launch the Web GUI
Interact with the agent via the browser:
```bash
uv run uvicorn PydanticAI_experiment.web_app:app --host 127.0.0.1 --port 7932
```
