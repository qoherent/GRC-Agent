# AGENTS.md

Rules and architectural commandments for AI coding agents working on this codebase. Direct, data-driven, zero fluff.

---

## 1. Core Philosophy & Architectural Commandments

- **Simplify First**: Lean towards simplifying, not complicating. If a feature, branch, or approach is ad-hoc, hardcoded, or not essential, **remove it**.
- **No Brittle Reinventions / Always Use Standard Libraries**: Reject complex manual implementations or from-scratch logic when reliable, standard libraries can replace them.
  - **PydanticAI** owns the agentic loop, tool dispatch, and message history.
  - **PyGObject's `gi.events`** (or `gbulb` on older PyGObject) owns the asyncio + GLib event loop unification.
  - **GNU Radio Companion's Python API** owns flowgraph parsing, block parameter evaluation, and graph validation.
  - Never reimplement or wrap these in custom hand-rolled loops or shadow abstractions.
- **Zero Ad-Hoc Heuristics & Zero Folklore**: Never use magic string branches, per-scenario regex routing, hardcoded command lists, or prompt folklore. If logic is required, it must be one uniform mathematical or algorithmic rule applied identically to all cases (e.g. `keep_param` in `adapter/graph.py`).
- **No Backward Compatibility Shims**: Delete dead code completely. Do not write shims, dual-format persistence layers, or legacy bridges. Keep changes clean and direct.
- **Fix at the Source**: Correctness lives in the tool or handler that produces data, not in a downstream post-processing filter.
- **No Assumed Reasoning Failures**: Do not assume task failures are solely due to LLM reasoning. Audit the execution harness for context flooding, poor prompt construction, hidden ad-hoc logic, or silent error message clipping.

---

## 2. Scientific Experimentation & Verification Persona

- **Assume Nothing, Verify Everything**: Base every decision on grounded, empirical observations (code inspection, live data flow, runtime execution), never on assumptions, intent, or memory.
- **Evidence Before Assertions**: Every claim must cite verified runtime observations. A green unit test or successful exit code is necessary but not sufficient — verify the underlying data flow, payload shapes, and state transitions.
- **Never Trust Memory for APIs — Always Use Context7 & Skills**:
  - When working with external libraries, frameworks, SDKs, or tools (even well-known ones like Pydantic, PyGObject, GNU Radio, FastAPI, AnyIO), unconditionally query current documentation via `context7` MCP or specialized agent skills.
  - Always check and use the specialized project skills in `/home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/.agents` (`building-pydantic-ai-agents`, `pydantic-ai-harness`, `hermes-subagent`).
  - Never rely on internal training memory, which may be outdated or hallucinated.
- **Empirical & Collaborative Decisions**: Stop and ask for clarification when requirements are ambiguous or when a major decision (e.g. library selection, backend architecture, destructive migration) needs to be made.

---

## 3. General Best Coding Practices

- **Git Workflow: Single-Branch Main Only**: Always work directly on `main` — never create feature branches, worktrees, or any alternative branch topology. Assume every change in the working tree (committed or uncommitted) is the user's own work: never stash, revert, exclude, or path-limit around it, and always include all current changes in any push. Commits stay conventionally scoped in message only, never in file ownership.
- **No Silent Transformations or Hidden Truncation**: Any filtering, truncation, or omission in model-facing output must be explicit, truthful, and honest (`output_truncated`, explicit counts). Never silently drop data.
- **Maximizing Context Honestly**: Do not enforce arbitrary context limits beyond what the backend actually supports. Never clip inputs or outputs using raw string slicing that breaks structured context (JSON/AST).
- **Atomic Operations & Concurrency Safety**:
  - All file writes must be atomic (temp file → fsync → `os.replace`).
  - Thread safety must be explicit: use `threading.Lock` for cross-worker process spawning (e.g. `ensure_server()`) and `asyncio.Lock` for async token refreshes.
  - Flowgraph disk access is locked via `fcntl.flock` on `.grc_agent/<name>.lock`.
- **Uniform Error Reporting — `ModelRetry` vs `ToolFailed`**: Every model-facing domain tool reports failure through one of two framework exceptions, chosen by what the model should do next.
  - `ModelRetry` — the model can fix this by trying again with different arguments or a different approach (bad param, invalid connection, validation failure). Carries actionable compiler/runtime feedback and consumes the tool's retry budget.
  - `ToolFailed` — terminal: no retry can fix it (an unwired run monitor, missing execution or save capability). The model sees a failed result and adapts. It consumes no retry budget, so repeated terminal failures are bounded at the run level instead: `StopGracefully.max_repeated_failures` ends the run after a tool fails terminally 3 times in a row.
  - Never instruct the model not to retry in prose. That was a workaround for reporting terminal faults as retries; the exception type now carries it.

---

## 4. Project-Specific GRC Invariants & Rules

- **GUI-Only Native Desktop App (No CLI)**:
  - `grc-agent` is a single-entry-point native desktop application.
  - `pyproject.toml`'s `[project.scripts]` must always point directly at `grc_agent.desktop_app:main`.
  - No CLI subcommands, no `argparse`, and no diagnostic flags (`--check`/`--doctor`/`--version`).
  - All diagnostics, errors, and status notifications belong inside the native GUI itself (e.g. status bar, `Gtk.MessageDialog`).
- **Single-Process, Single-Thread Event Loop**:
  - `event_loop.install()` unifies asyncio and the GLib main loop on GTK's default `GMainContext` exactly once before any UI initialization.
  - The agent, canvas, and all tool calls run on one thread with zero cross-thread marshaling (`GLib.idle_add`).
- **Shared In-Memory `FlowGraph`**:
  - `NativeFlowgraphProxy` directly forwards attribute access to `window.current_page.flow_graph`.
  - The canvas and agent share the exact same `FlowGraph` instance in-place; canvas updates immediately without reloading from disk.
- **Use Native GNU Radio Companion APIs**:
  - Use GRC's Python API (`param.hide`, `param.category`, `Block.is_variable`, `flow_graph.is_valid()`, etc.).
  - Never parse, write, or regex-script raw `.grc` XML directly.
- **Never Enumerate Tools in Prompts**:
  - Pydantic AI automatically inspects and transmits JSON schemas for all available tools dynamically.
  - System prompts must only express unobservable harness contracts, execution invariants, and GRC platform quirks.
- **Default to Standard C++ Catalog Blocks**:
  - Ground signal processing in standard GNU Radio catalog blocks (compiled C++ with VOLK SIMD vectorization).
  - Use Embedded Python Blocks (`epy_block`) only for custom logic/state machines where no catalog block exists.
  - When writing custom logic in an `epy_block`, mandate vectorized NumPy/SciPy array slice operations in `work()` to maintain streaming throughput.
- **Flowgraph Execution via Native GRC**:
  - Flowgraphs are started and stopped exclusively through `run_flowgraph`, which triggers GRC's native Execute/Stop actions (`Actions.FLOW_GRAPH_EXEC`/`KILL`).
  - Never execute generated flowgraph scripts or top-block Python files via shell tools.
- **Sandboxed Project Directory & Human-in-the-Loop Consent**:
  - Filesystem (`fs_tools.py`) and shell (`shell_tools.py`) tools operate strictly within the resolved project directory sandbox.
  - Flowgraph mutations (`change_graph`) and foreground/background shell commands (`run_command`/`start_command`) require user approval (`requires_approval=True`) via `ApprovalCard` widgets.
- **Version Number Invariant**:
  - Never bump the project version number in `pyproject.toml`, `CITATION.cff`, or `CHANGELOG.md` without explicit user instruction.

---

## 5. Tool Surface Overview

Argument bounds and formats live in the JSON schema, not in prose: `k` carries `minimum`/`maximum`, connection strings carry a `pattern`, and nothing is silently clamped. Optional list arguments are plain arrays rather than nullable unions.

| Tool | Direction | Engine / Underlying Implementation |
| :--- | :---: | :--- |
| `inspect_graph` | Read | `adapter.inspect_graph()` — Semantic JSON view with Stage A/B parameter and layout pruning. Omission counters and empty port lists are emitted only when non-empty. |
| `query_knowledge` | Read | `adapter.query_catalog()` / `query_docs()` — Hybrid Reciprocal Rank Fusion (RRF $k=60$) vector + FTS5 search with embedded C++ SWIG docstrings. |
| `generate_python` | Read | `adapter.preview_flowgraph_py()` — In-memory preview of generated Python source via GRC's `Generator`. |
| `change_graph` | Write | `adapter.change_graph()` — 7-phase transactional mutation engine with automatic rollback, Sugiyama relayout, and native validation. Approval-gated. Connection strings are pattern-validated in the schema. |
| `run_flowgraph` | Write (Side Effect) | `NativeFlowgraphProxy.run_flowgraph()` — Native GRC Execute/Stop with optional bounded runtime (`stop_after_seconds`). Start is approval-gated via the tool's `args_validator`. |
| `get_run_log` | Read | `exec_monitor.get_last_run_log()` — Retains console stdout/stderr from the last completed run. |
| `save_block` | Write | `adapter.block_library.save_block_to_library()` — Exports `epy_block` source into GNU Radio's hier-block library (`~/.grc_gnuradio`). |
| `read_file` · `write_file` · `edit_file` · `list_directory` · `search_files` · `find_files` · `create_directory` · `file_info` | Read / Write | `GrcFileSystemToolset` — Sandboxed to the project directory; `.grc` reads route to inspection; `.grc` writes denied. |
| `run_command` · `start_command` · `check_command` · `stop_command` | Write / Read | `GrcShellToolset` — Project-directory rooted shell execution with denylist safety, API key scrubbing, and approval gating on execution. |

---

## 6. Test Gate & Verification Standards

Run the test suite and linter before concluding any change:

```bash
# Fast unit tests (no LLM, no external network dependencies)
uv run pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py

# Codebase formatting & linting
uv run ruff check

# Display-dependent GTK UI tests (run under xvfb if headless)
xvfb-run -a uv run pytest tests/test_chat_sidebar.py tests/test_chat_sidebar_golden.py tests/test_native_canvas.py tests/test_desktop_app.py tests/test_session_persistence_advanced.py tests/test_context_compaction.py
```

- The fast gate is hermetic: no test in it makes an LLM call or reaches an external network endpoint. Live-backend tests carry `@pytest.mark.integration` and are deselected by `addopts`.
- All non-integration unit tests and linter checks must pass with zero errors.
- Never disable or skip tests without explicit rationale and evidence.
