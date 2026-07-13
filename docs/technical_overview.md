# GRC-Agent: Technical Overview

GRC-Agent is an agentic companion designed for digital signal processing (DSP) and software-defined radio (SDR) design, bridging natural language interaction with visual GNU Radio Companion (.grc) flowgraphs. 

This document details the system's architecture, including its model-facing tools, RAG search setup, transactional mutation engine, and integration benchmark results.

---

## System Architecture

GRC-Agent is organized into a modular pipeline connecting the visual representation layer with the LLM reasoning core:

```mermaid
flowchart LR
    User([User]) <--> WebApp[Starlette Web App]
    WebApp <--> BroadwayCanvas[Broadway Canvas Subprocess]
    WebApp <--> Agent[PydanticAI Agent]
    Agent <--> Flowgraph[GNU Radio Flowgraph API]
    BroadwayCanvas <--> Flowgraph
```

- **Web Application**: Starlette server that manages interactive web chat, settings preferences, and coordinates the GTK Broadway display canvas.
- **Virtualized GTK Canvas**: GTK application runner that loads the flowgraph, strips unnecessary desktop chrome, and exposes the visual drawing area to the browser via Broadway on a dedicated display port.
- **Flowgraph Proxy**: Thread-safe state synchronization layer that coordinates file locking during load, edit, or reload operations.
- **Agent Reasoning Core**: PydanticAI Agent that registers system prompts, capability plugins (such as `StopGracefully`), and manages the model-facing tools.

---

## Web Dashboard & Chat Integration

The GRC-Agent web dashboard provides a unified UI combining the virtualized GRC canvas and a custom conversational assistant.

### 1. Starlette Backend & Streaming
- **Agent Server**: Wires the PydanticAI agent to a Starlette server using `agent.to_web()`, exposing SSE streams under `/api/*` (compliant with the Vercel AI SDK UI Message Stream protocol).
- **Stateless History**: The backend maintains no conversation state. The frontend client manages the active session and full chat history, transmitting the entire conversation thread as context for each new inference.
- **Coexisting Routes**: Custom API endpoints for GRC state management (e.g., `/grc/open`, `/grc/undo`, `/grc/status`, `/grc/settings`) are prepended to the Starlette router, coexisting cleanly with PydanticAI's native routes.

### 2. Client-Side SSE & Markdown Rendering
- **Event Consumer**: A native, lightweight Event Source / SSE reader in `panel.js` consumes stream frames (`text-delta`, `reasoning-delta`, `tool-input-start`, `tool-output-available`) to display reasoning and tool arguments in real time.
- **Safe Markdown**: Outputs are passed through a custom Markdown-to-HTML parser that escapes inputs to prevent arbitrary code injection, rendering code blocks, bold text, lists, and headings cleanly.

### 3. GRC Panel & Layout Maximization
- **Window Hiding**: GRC runs under Broadway inside the dashboard's iframe. To optimize workspace visibility, window decorations (`decorated = False`), the top menu bar, and the toolbar are hidden programmatically at startup.
- **Panel Visibility**: The three GRC panels (Block Library, Console Panel, Variable Editor) are hidden by default on launch via their corresponding GRC action controllers:
  * Block Library: `Actions.TOGGLE_BLOCKS_WINDOW` (Shortcut: `Ctrl+B`)
  * Console Panel: `Actions.TOGGLE_CONSOLE_WINDOW` (Shortcut: `Ctrl+R`)
  * Variable Editor: `Actions.TOGGLE_FLOW_GRAPH_VAR_EDITOR` (Shortcut: `Ctrl+E`)

---

## Genius Tool Design

The agent interacts with the flowgraph through three highly specialized tools:

### 1. Context-Efficient Graph Inspection (`inspect_graph`)

To preserve context window limits and optimize reasoning tokens, visual and schema metadata is pruned using a two-stage process:

- **Stage A (Visual & Structural Layout Pruning)**: Excludes layout-specific variables (e.g. GUI hints, coordinates) and non-DSP nodes (such as imports, snippets).
- **Stage B (Parameter Visibility Pruning)**: Omits default configuration values, advanced parameters, and unconnected optional ports. The LLM receives a clean, semantic JSON representation of the active DSP topology.

### 2. Local SQLite Vector RAG (`query_knowledge`)

Knowledge grounding is enforced through a local SQLite vector database (`sqlite-vec`) built lazily upon first use. The database splits search queries into two separate domains:

- **Catalog Domain**: Queries GNU Radio block metadata, block IDs, category mappings, parameter options, and port structures.
- **Docs Domain**: Queries wiki pages, tutorials, and conceptual documentation parsed and heading-chunked.
- **Embedding Provider Fallback**: Embeddings are generated using local Ollama (`embeddinggemma:latest`) or OpenRouter (`perplexity/pplx-embed-v1-0.6b`) backends, checking for model or dimensionality changes on startup.

### 3. Transactional Mutation Engine (`change_graph`)

Graph editing executes a batch of updates in a strict 7-phase transactional sequence, guaranteeing that the flowgraph is not left in a partially mutated or corrupted state:

1. **`remove_connections`**: Drops specified connections.
2. **`remove_blocks`**: Deletes block instances from the graph.
3. **`add_blocks`**: Instantiates new blocks, placing them using a grid-spaced spiral collision-avoidance search algorithm.
4. **`update_params`**: Updates block parameters (e.g. sample rates, thresholds).
5. **`auto_resolve_types`**: Dynamically propagates type selections (`dtype`) for parameters set to `"auto"` based on neighboring ports.
6. **`update_states`**: Configures block execution states (enabled, disabled, or bypass).
7. **`add_connections`**: Wires ports together to re-establish the DSP signal chain.

#### Grid-Spaced Spiral Coordinate Resolution
Since the LLM lacks spatial awareness, block positioning is resolved programmatically. Coordinates are snapped to grid boundaries (`BLOCK_FOOTPRINT_W=300`, `BLOCK_FOOTPRINT_H=220`, and `BLOCK_SPACING=60`), searching outward in concentric Chebyshev rings near connected neighbors to prevent overlaps on the visual canvas.

#### Self-Correction & Native Validation
At the end of a transaction, GNU Radio's native validation compiles and validates the new state. If validation fails, changes are rolled back, the prior state is restored, and a `ModelRetry` exception containing the exact compiler feedback is raised, enabling self-correction for up to 3 attempts.

---

## Agent Lifecycle

The diagram below tracks the execution lifecycle of a single user prompt:

```mermaid
flowchart TD
    Idle([Idle: Awaiting Prompt]) --> Input[User Prompt Received]
    Input --> Inspect[inspect_graph]
    Inspect --> DecisionRAG{Missing Block ID or docs?}
    DecisionRAG -- Yes --> RAG[query_knowledge]
    RAG --> Plan[Plan Mutations]
    DecisionRAG -- No --> Plan
    Plan --> Mutate[change_graph transaction]
    Mutate --> Validate{flow_graph.validate}
    Validate -- Fail --> Retry[ModelRetry up to 3x]
    Retry --> Plan
    Validate -- Pass --> Commit[Commit transaction & atomic save]
    Commit --> UndoSnapshot[Push Undo/Redo Snapshot]
    UndoSnapshot --> Sync[Broadway Canvas Sync /reload]
    Sync --> Explained[Explain changes to user]
    Explained --> Idle
```

---

## Integration Scenarios Benchmark

The integration test suite executes 11 distinct scenarios mapping real-world editing workflows. All scenarios pass successfully across both local and cloud LLM backends:

| Scenario Name | qwen3.6:35b (Ollama Local) | deepseek-v4-flash (Ollama Cloud) | Verification Objective |
| :--- | :---: | :---: | :--- |
| `01_add_throttle` | Pass | Pass | Inserts a throttle block inline inside the dial tone mixer path. |
| `02_update_sample_rate` | Pass | Pass | Modifies the `samp_rate` variable parameter value to 48000. |
| `03_disable_and_enable` | Pass | Pass | Disables then re-enables a noise source block. |
| `04_add_and_remove_variable` | Pass | Pass | Adds `gain_value` variable and references it in a tone source's amplitude. |
| `05_full_rewire` | Pass | Pass | Deletes a noise block and connects a new DC offset block to the adder. |
| `06_query_knowledge_multiply` | Pass | Pass | Replaces an adder block with a multiplier block located via catalog search. |
| `09_docs_stream_tags_concept` | Pass | Pass | Queries documentation domain regarding stream tags concepts without mutations. |
| `10_bypass_source_block` | Pass | Pass | Transitions a signal source block into bypass state. |
| `11_scoped_inspect_and_update` | Pass | Pass | Inspects specific target blocks and modifies sample rate. |
| `14_build_chain_from_scratch` | Pass | Pass | Constructs a signal source -> throttle -> sink chain on an empty flowgraph. |
| `22_fm_rx_filter_squelch` | Pass | Pass | Inserts a low-pass filter and simple squelch block inline inside an FM receiver chain. |
