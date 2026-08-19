# GRC-Agent Product Backlog

Active feature requests, architectural improvements, and planned capabilities. Completed milestones are documented in [CHANGELOG.md](../CHANGELOG.md).

---

## 📋 Active Backlog

### 1. Out-of-Tree (OOT) Module Development Support
* **Status**: 📥 Proposed / Future Work
* **Scope**: Full lifecycle management of custom GNU Radio OOT modules:
  1. **Scaffolding**: Automating directory setup and boilerplate generation using `gr-modtool`.
  2. **Implementation**: Writing and editing block processing logic in Python or C++.
  3. **Block Descriptors**: Creating and updating companion block YAML configuration files (`.yml`).
  4. **Build & Install**: Orchestrating `cmake`, `make`, and installation workflows so GRC discovers the new block.
* **Current State**: Confined to flowgraph layout/configuration and native hier-block library export via [`save_block`](../src/grc_agent/adapter/block_library.py). Direct compiler, shell, and `gr-modtool` orchestration is not yet supported — but the filesystem tools ([`fs_tools.py`](../src/grc_agent/fs_tools.py)) already let the agent scaffold and edit OOT source/config files (`.py`/`.yml`/CMake/C++ all pass the write-suffix allowlist) inside the flowgraph's folder; building/installing remains the user's job.

---

### 2. Durable Planner → Executor Handoff (`SqlitePlanStore`)
* **Status**: ⏳ Approved in concept
* **Scope**: Make the harness `Planning` capability durable across user turns within the same chat session.
* **Key Design Decisions**:
  - Use `SqlitePlanStore` co-located on the chat SQLite database (matching `SqliteStepStore`), keyed by `conversation_id = 'session-{id}'`.
  - Plans persist across turn boundaries, enabling the agent to create a structured multi-step plan in turn 1 and incrementally execute/update tasks in subsequent turns.
  - Follows standard harness `Planning` capabilities without introducing custom sub-agent layers.

---

## 🛠️ Contributing to the Backlog
When recording new requests or design decisions:
1. Document the requirement, user context, and target scope.
2. Outline specific architecture constraints, safety boundaries, and affected modules.
3. Keep entries concise, actionable, and grounded in the current codebase state.
