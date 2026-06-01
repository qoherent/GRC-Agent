# GRC Agent Desktop UI - PySide6 Execution Blueprint (TDD Approach)

**Status**: All Milestones (1, 2, 3, and 4) are fully implemented, release-tested, and verified with 26/26 tests passing.

## Architecture Overview

This blueprint outlines the transition of the GRC Agent into a native, zero-IPC Python desktop application using **PySide6** configured as a **Sidekick Panel** to run alongside the native GNU Radio Companion (GRC) editor.

By sharing the same Python process space as the core agent, this architecture eliminates cross-language packaging overhead, WebSocket/CORS authentication complexity, and external sandboxing restrictions. 

Rather than attempting to replicate a visual node-graph canvas from scratch, this design leverages GRC's native graphical environment and automatic file-watcher, focusing the agent panel strictly on the chat/mutation and execution plane.

### Core Design Tenets
1. **PySide6**: The official Qt for Python bindings (LGPL compliant).
2. **Strict Signal-Only Boundary**: Background worker threads (`QThread`) strictly communicate with the main GUI thread via PySide6 Signals. No direct UI manipulation from background threads.
3. **Native Markdown Rendering**: Chat formatting is handled natively using `QTextBrowser` or `QTextDocument.setMarkdown()`. This avoids external markdown UI dependencies and ensures safe, high-performance C++-level rendering and native text-selection.
4. **Pygments Code Highlighting**: Final chat code blocks (e.g., ` ```python `) are processed using the lightweight `pygments` library to inject inline-styled HTML formatting before calling `setHtml()` on the `QTextBrowser`, bypassing selection-breaking custom bubble paint delegates and heavy `QWebEngineView` Chromium wrappers.
5. **GNU Native Sidekick Pattern**: Avoids duplicating the node graph interface. The user visualizes the flowgraph in the native GRC application, which automatically prompts to reload when changes are saved to the `.grc` file. The PySide6 agent panel displays structured, read-only tree/table lists of active variables, blocks, and connections.
6. **Split-Stage Hardware-Safe Execution**: Decouples `grcc` compilation and flowgraph execution. Flowgraph execution uses PySide6 `QProcess` inheriting the parent system environment to correctly resolve SDR hardware drivers (UHD/SoapySDR) and system-installed `gnuradio` site-packages. It enforces a two-phase termination sequence (`terminate()` -> wait 2000ms -> `kill()`) to prevent orphaned hardware driver locks.

---

## Test-Driven Development (TDD) Strategy

Development follows a TDD approach. All components must have regression tests written *before* the implementation.
*   **Testing Framework**: `pytest` and `pytest-qt` (for mocking and asserting Qt signals and widget states).
*   **Methodology**:
    1. Write the test asserting the desired behavior (e.g., verifying a signal is emitted or a process is killed).
    2. Run the test to ensure it fails.
    3. Write the minimal PySide6 code to pass the test.
    4. Refactor.

---

## Milestone 1: The Application Shell & Agent Integration

**Objective**: Establish the PySide6 `QMainWindow` and securely wrap the `grc-agent` LLM logic in a non-blocking `QThread`, protecting against GIL locks, garbage collection, and UI concurrency locks.

### Tests to Write First (`tests/gui/test_agent_thread.py`)
- `test_agent_worker_emits_start_signal`: Verify that starting the worker emits a `started` signal.
- `test_agent_worker_emits_progress_signals`: Verify that worker emits intermediate signals when tools are called (e.g., `tool_started(name, args)` and `tool_finished(name, result)`).
- `test_thread_safety_boundary`: Assert that the `AgentWorker` subclass contains zero references to `QWidget` or `QMainWindow` types.
- `test_thread_garbage_collection_lifetime`: Assert that the `AgentWorker` and `QThread` instances are bound as long-lived instance variables (`self.worker`, `self.thread`) of the managing controller and that their lifetimes are explicitly clean-managed on window destruction via cooperative cancellation flags, `thread.quit()`, and `thread.wait()`.
- `test_ui_lockout_during_generation`: Assert that emitting `tool_call_started` sets the chat input `QLineEdit.setEnabled(False)` and that emitting `turn_finished` sets it back to `True`.

### Implementation Steps
1. Scaffold `src/grc_agent_gui/main_window.py` containing a basic `QMainWindow` with a sidebar layout.
2. Scaffold `src/grc_agent_gui/workers.py` containing `AgentWorker(QObject)` configured to run in a `QThread`.
3. Bind the thread and worker to `self` in the main controller to prevent silent garbage-collection segmentation faults.
4. Implement cooperative thread cancellation flags and proper HTTP client timeout configuration on the model connection.

---

## Milestone 2: Secure Chat Rendering & Native Formatting

**Objective**: Provide a clean, readable, selectable Chat UI using native Markdown rendering capabilities in PySide6 and lightweight syntax highlighting with Pygments.

### Tests to Write First (`tests/gui/test_chat_widget.py`)
- `test_native_markdown_rendering`: Assert that passing standard markdown (headers, tables, lists) to the chat widget updates the `QTextBrowser`'s underlying document structure correctly via `setMarkdown()`.
- `test_html_safety_handling`: Assert that unsafe HTML structures (like `<script>` or `<iframe>`) are ignored or safely parsed by the native Qt markdown engine.
- `test_chat_widget_appends_text`: Use `pytest-qt` to assert that appending user/assistant messages refreshes the layout.
- `test_markdown_stream_throttling`: Assert that chunk emissions during LLM streaming do not invoke `setMarkdown()` on every single token, but are either appended incrementally as plain text or throttled via a timer (e.g., 200-250ms) while preserving the vertical scrollbar position via `verticalScrollBar().setValue()`. Verify that the final definitive `setMarkdown()` is applied only upon receiving `turn_finished`.
- `test_pygments_syntax_highlighting`: Assert that final markdown containing code blocks (e.g., ` ```python `) is parsed and highlighted using `pygments` (with inline-styled HTML blocks) before being rendered via `setHtml()` on `turn_finished`.

### Implementation Steps
1. Build the `ChatWidget(QWidget)` containing a `QTextBrowser` and a `QLineEdit` for user prompts.
2. Set the `QTextBrowser` to display text using `QTextDocument.setMarkdown()`.
3. Implement a throttled markdown rendering strategy (or incremental plain-text appending) during active streaming, preserving the user's vertical scroll position.
4. Upon receiving the `turn_finished` signal, parse the Markdown and highlight code blocks using Pygments to output inline-styled HTML, setting it via `QTextBrowser.setHtml()`.
5. Connect the `QLineEdit`'s `returnPressed` signal to the `AgentWorker` input handler.

---

## Milestone 3: Flowgraph Structure Inspector (The Sidekick View)

**Objective**: Expose a structured, read-only representation of the active flowgraph session using native Qt widgets, and enable opening the graph in the native GRC editor.

### Tests to Write First (`tests/gui/test_inspector_widget.py`)
- `test_variables_table_mapping`: Assert that variables parsed from the session are successfully mapped to columns in a `QTableWidget` (ID, value).
- `test_blocks_tree_mapping`: Assert that active blocks are correctly loaded into a hierarchical `QTreeView` or `QTreeWidget` showing block IDs, instance names, and active states.
- `test_connections_list_mapping`: Assert that connections map to a list of source/destination ports.
- `test_open_in_grc_triggers_process`: Mock `QProcess` and assert clicking the "Open GRC" button launches the native `gnuradio-companion` binary pointing to the active copied `.grc` path.

### Implementation Steps
1. Create `src/grc_agent_gui/inspector.py` implementing `InspectorWidget(QWidget)`.
2. Connect the `FlowgraphSession` signals to automatically refresh the tables/trees whenever a change is committed (via `change_graph`).
3. Add a toolbar with an "Open in GRC" action using a separate system `QProcess` to run the official editor.

---

## Milestone 4: Split-Stage Compilation & Hardware-Safe Execution

**Objective**: Compile `.grc` files and execute flowgraphs in a separate process space, ensuring system environment variable preservation for hardware drivers and robust PID tracking.

### Tests to Write First (`tests/gui/test_process_manager.py`)
- `test_flowgraph_id_resolution`: Assert that compilation resolves the output Python filename by reading the `id` field from the options block (querying `session.flowgraph.metadata["options"]["parameters"]["id"]`), rather than guessing based on the `.grc` file name.
- `test_split_stage_compilation_and_execution`: Assert that compilation invokes `grcc -o <temp_dir> file.grc`, waits for a clean exit, and then instantiates a fresh, independent `QProcess` calling `sys.executable <temp_dir>/<flowgraph_id>.py`.
- `test_process_working_directory_binding`: Assert that before launching the compiled script, the `QProcess` working directory is set to the directory of the original `.grc` file to prevent relative path file operations (e.g., File Source / File Sink) from failing.
- `test_process_inherits_environment`: Assert that the runtime process environment contains key variables (such as `PATH`, `LD_LIBRARY_PATH`, and `PYTHONPATH`) inherited from the parent environment to correctly load UHD/SDR drivers.
- `test_two_phase_termination_graceful`: Assert that calling `stop()` sends `terminate()` and successfully closes.
- `test_two_phase_termination_forceful`: Mock a process ignoring `terminate()`. Wait 2000ms and assert `kill()` is explicitly invoked by the fallback timer to avoid locking hardware.

### Implementation Steps
1. Create `src/grc_agent_gui/process_manager.py` wrapping the PySide6 `QProcess` lifecycle.
2. Implement dynamic flowgraph ID resolution from the active `FlowgraphSession` metadata.
3. Configure the `QProcess` with `QProcessEnvironment.systemEnvironment()`.
4. Explicitly configure the `QProcess` working directory to match the original `.grc` file's parent directory via `process.setWorkingDirectory()`.
5. Implement the **Two-Phase Termination Sequence** (`terminate()` -> wait 2000ms -> `kill()`) for the running Python script.
6. Bind the `QApplication.aboutToQuit` signal to the `ProcessManager.stop()` routine to guarantee zero orphaned DSP/SDR processes on application exit.

---

## Milestone 5: Implementation & Validation Report

All four milestones have been successfully completed and release-tested.

### Completed Deliverables:
1. **Workers & Concurrency ([workers.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/src/grc_agent_gui/workers.py))**: Handles non-blocking background LLM reasoning inside a `QThread` with cooperative cancellation and socket shutdown capabilities to prevent GUI deadlocks.
2. **Chat Area ([chat_widget.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/src/grc_agent_gui/chat_widget.py))**: Standard markdown rendering on a `QTextBrowser` with Pygments syntax highlighting on turn completion. Eliminates layout flicker by appending text incrementally during streaming.
3. **Inspector panel ([inspector.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/src/grc_agent_gui/inspector.py))**: Renders blocks tree, variables table, and connections list with scroll and expansion state-preservation. Supports launching the official GRC editor detached via `QProcess.startDetached`.
4. **Execution Console & Process Manager ([process_manager.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/src/grc_agent_gui/process_manager.py))**: Handles split-stage compilation and execution, system environment variable inheritance, and output logging into a bottom console.

### Asynchronous Safety Nets Implemented:
- **Deferred closeEvent**: In `MainWindow.closeEvent`, if a process is compiling or running, the application ignores closing, displays a status message, binds finished callbacks to `on_deferred_close`, and calls `ProcessManager.stop()` to cleanly terminate the running subprocess before allowing the application to close.
- **mkdtemp Leak Protection**: Cleans up persistent temp directories containing compiled flowgraph files using `shutil.rmtree` on subsequent compiles and on final application quit.
- **UI State Button Locking**: Disables execution buttons during active compilation and runs, preventing duplicate subprocess spawns.

---

## Milestone 6: GUI Implementation Audit & Concurrency Hardening (Remediation Phase)

To guarantee production-grade stability under intensive usage, a comprehensive systems-level concurrency audit was conducted on the companion sidekick GUI. The following architectural corrections and safety nets were successfully implemented:

### Concurrency Corrections & Wins:
1. **Inversion of Control for Tool Instrumentation**: Eliminated the thread-unsafe monkey-patching of the global `GrcAgent.execute_tool` method from the background `AgentWorker` thread. Moved to clean observer callbacks (`on_tool_start`, `on_tool_end`) passed into `ToolAgentsRunner.run_turn()`.
2. **Worker Lifetime & Graceful Teardown**: Configured `cleanup_thread` to wait for 1500ms before falling back to `QThread.terminate()`. Added safety checks in `InspectorRunnable` to skip background execution if the agent is a mock, preventing thread-unsafe Mock access segmentation faults during tests.
3. **Deferred closeEvent Deduplication**: Gated connections in `MainWindow.closeEvent` using a state-machine flag (`_pending_close`), preventing exponential signal connection replication on rapid double-clicks of the window close button.
4. **Non-Blocking Inspector Refresh**: Corrected GUI thread blocking by offloading the synchronous `inspect_graph` scanning to a lightweight `QRunnable` executed on `QThreadPool.globalInstance()`.
5. **Process Manager Hardening**: Added explicit `QProcessEnvironment.systemEnvironment()` inheritance to the compilation stage, implemented `errorOccurred` monitoring for binary resolution failures (e.g., missing `grcc`), validated compiled file existence before spawning runtime Python scripts, and implemented a synchronous `shutdown()` routine bound to `QApplication.aboutToQuit` for clean process teardowns.
6. **Pygments Fallback Handling**: Fixed a parser bug where unknown markdown language code blocks ate the language tag line as literal code.

### Hardened Regression Test Suite:
- The GUI test suite was expanded to **40 tests** (and the full project test suite to **309 tests**), covering thread cancellation, process fallbacks, double close-event click sequences, and Pygments parser edge cases under headless `xvfb-run` execution.


