# GRC-Agent Product Backlog & Client Notes

This document tracks feature requests, feedback, and prospective capabilities sourced from clients, users, and community interactions.

---

## 📋 Backlog Items

### 1. Out-of-Tree (OOT) Module Development Support
* **Source/Client**: External client (DoD-affiliated fellowship, academic Ph.D. candidate)
* **Date Added**: July 17, 2026
* **Status**: 📥 Proposed / Future Work

#### Description
Users want the agent to handle the entire lifecycle of custom Out-of-Tree (OOT) modules rather than just integrating already-installed blocks. This includes:
1. **Scaffolding**: Automating directory setup and boilerplate generation using `gr-modtool`.
2. **Code Generation/Editing**: Implementing block processing logic in Python or C++.
3. **Descriptor Creation**: Automatically writing or updating the companion YAML configuration files (`.yml`) defining block signatures, parameters, and ports.
4. **Compilation & Installation**: Orchestrating building (`cmake`, `make`) and installation workflows so GRC can discover the new block.

#### Current Workaround / State
* **Integration is supported**: Any block that is already installed locally is successfully discovered via GNU Radio's block catalog and can be added/configured/wired using [`change_graph`](../src/grc_agent/agent.py#L450).
* **Development is not supported**: GRC-Agent is currently confined to the flowgraph layout and configuration layer. It lacks tools to interact with codebases outside the flowgraph or run compiler/system commands.

---

## 🛠️ How to Add to This Backlog
When a client or community member raises a query or requests a feature:
1. Document the request, the source, and the date.
2. Outline the gap between the current state and the proposed feature.
3. Reference relevant code segments (e.g. tools or adapters) that would be affected.
