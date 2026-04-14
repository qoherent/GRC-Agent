# Project Blueprint
Link to project: https://riahub.ai/mahmoud/GRC_Agent
## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs.
The project exists to inspect, explain, modify, validate, and eventually drive
safe graph changes without letting the model edit raw YAML directly.

## Current Product Shape

- one `.grc` file per session
- headless CLI
- package-level block description is available as `describe_block(block_id)` over installed GNU metadata
- bounded retrieval is available as a package-level `search_grc(...)` API over GNU catalog and active-session graphs
- CLI startup now runs the bounded retrieval readiness check and binds the active session context
- local validation through `grcc`
- safe mutations owned by `FlowgraphSession`
- structural-edit session surface frozen unless a new experiment pass justifies change
- model-facing runtime deliberately narrower than the session layer
- deterministic `--fake` runtime path for smoke testing
- thin llama.cpp server adapter spike available through the CLI
- supported llama.cpp behavior is live-verified for the current narrow tool contract
- raw model free-form prose is still not trusted outside deterministic runtime finalization for supported flows

## Canonical Working Rules

- raw `.grc` YAML remains the persistence format and must not be edited by the model directly
- all meaningful graph mutations go through `FlowgraphSession`
- session mutations must keep parsed objects and raw YAML in sync
- explicit `validate()` remains the final graph-correctness gate
- model-facing `save_graph` is rejected until the latest dirty state has passed validation
- structural APIs widen only when new experiments justify a narrower or clearly necessary contract
- llama.cpp built-in `/tools` is not part of the supported contract; the server is used only as a model backend through documented chat endpoints

## Evidence-First Planning Policy

- start from the smallest user goal and the narrowest candidate API
- read the relevant GNU Radio documentation before proposing behavior
- reproduce the behavior on a real `.grc` case with `grcc` before trusting it
- record the pass or fail evidence in this blueprint before widening the supported contract
- add or update automated regression coverage after the real GNU result is known
- never infer GNU semantics from YAML shape alone, especially for ports, types, generated states, or block-specific parameters

## Baby-Step Delivery Rule

- change one narrow behavior at a time
- validate each step with the smallest real flowgraph that proves the behavior
- keep failed experiments out of the runtime contract until they have a passing, documented, repeatable path
- keep the runtime narrower than the session layer until repeated real usage proves a wider tool is needed
- keep CI split between a fast health gate and a GNU-backed validation gate so policy drift is visible early

## Architecture Layers

### 1. Raw `.grc` on disk

- YAML on disk remains the persistence format.
- Save and validation operate from the same in-memory raw YAML snapshot.

### 2. Session / thin IR layer

- [src/grc_agent/models.py](../src/grc_agent/models.py) holds the lightweight parsed dataclasses.
- [src/grc_agent/flowgraph_session.py](../src/grc_agent/flowgraph_session.py) owns load, summarize, save, validate, and all mutation primitives.
- The session layer is broader than the model-facing runtime because it is also the regression-tested experimentation surface.

### 3. Catalog / block description layer

- [src/grc_agent/catalog/](../src/grc_agent/catalog/) owns canonical structured block truth over installed GNU metadata.
- `describe_block(block_id)` is the read-only Phase 2 entry point for normalized block identity, category path, parameters, ports, asserts, docs, warnings, and a compact signature.
- The catalog layer uses the same GNU metadata roots as retrieval and keeps hierarchy handling lightweight through warnings rather than over-modeling it.

### 4. Retrieval / search layer

- [src/grc_agent/retrieval/](../src/grc_agent/retrieval/) owns bounded search over installed GNU catalog metadata and the active session graph.
- The system GNU catalog metadata remains the truth source for catalog search, and the active `.grc` session remains the truth source for session search.
- graphify is used only as the graph-construction substrate through a thin adapter; ranking and result shaping stay local and deterministic.
- The current Phase 1 catalog corpus is the system block metadata roots (`/usr/share/gnuradio/grc/blocks` first, `/usr/local/share/gnuradio/grc/blocks` second) and includes `.block.yml`, `.tree.yml`, and `.domain.yml`.
- Retrieval stays package-level for now through `initialize_retrieval(...)` and `search_grc(...)`; it is not part of the model-facing runtime yet, but the CLI startup path already runs the bounded readiness check and binds the active session context.

### 5. Model-facing runtime layer

- [src/grc_agent/agent.py](../src/grc_agent/agent.py) owns the runtime tool registry and turn history.
- The runtime exposes only four tools: `summarize_graph`, `set_variable`, `validate_graph`, and `save_graph`.
- `set_variable` is intentionally narrower than generic `set_param(...)`; it updates only the `value` parameter on `variable` blocks.
- `save_graph` is explicit and gated by successful validation of the latest dirty state.

### 6. CLI boundary

- [src/grc_agent/config.py](../src/grc_agent/config.py) loads repo-backed llama runtime defaults from [grc_agent.toml](../grc_agent.toml).
- [src/grc_agent/cli.py](../src/grc_agent/cli.py) remains a thin entrypoint.
- The `--fake` path exists only to prove the runtime boundary and tool routing without introducing a model backend.
- The llama.cpp CLI defaults are repo-configured rather than duplicated inline.

### 7. Local llama.cpp adapter

- [src/grc_agent/llama_server.py](../src/grc_agent/llama_server.py) owns the thin HTTP adapter to `/health`, `/v1/models`, and `/v1/chat/completions`.
- The adapter calls `GrcAgent`, not `FlowgraphSession` directly.
- The adapter keeps `parallel_tool_calls` off and uses a bounded assistant-turn loop instead of a framework.
- `max_steps` is a tool-round budget; one final non-tool assistant answer is allowed after the last tool round.
- The current supported slice is verified live on one local Gemma GGUF, but the raw model final prose still is not trusted for summarize or supported mutation outcomes.

### 8. Future backend flexibility

- The runtime should stay backend-agnostic enough that a future local backend can still call the same narrow tool layer.
- Avoid orchestration frameworks unless the direct tool-calling shape fails under real use.

## Session Surface vs Runtime Surface

### Session capabilities

The current `FlowgraphSession` implementation supports:

- `load()`
- `summarize()`
- `save()`
- `validate()`
- `set_param(...)`
- `disconnect(...)`
- `connect(...)`
- conservative `remove_block(...)`
- narrow `add_block(...)` for detached `variable` blocks
- `add_and_connect_qtgui_time_sink(...)`
- `add_and_connect_char_to_float_to_qtgui_time_sink(...)`
- `add_and_connect_analog_random_source_to_qtgui_time_sink(...)`

### Model-facing runtime contract

The model-facing contract is intentionally smaller:

- `summarize_graph`
- `set_variable(instance_name, value)`
- `validate_graph`
- `save_graph(path=None)`

This separation is deliberate. The broader session surface exists to preserve the validated mutation work and test coverage, but the runtime contract stays narrow until the local adapter proves a real need for wider tools.

## Runtime Decision

Use a thin local runtime wrapper over `FlowgraphSession` instead of wiring the CLI directly to a model client or adopting a framework.

### Boundary

- `GrcAgent` owns a minimal turn history and tool registry.
- The runtime exposes only session-backed tools.
- The model never edits raw YAML directly.
- The CLI creates the session and invokes the runtime.
- The `--fake` CLI path exists only for deterministic runtime verification.

### Why this stays thin

- The current bottleneck is contract clarity, not orchestration capability.
- A framework would hide logic while the model-facing tool surface is still intentionally narrow.
- One flowgraph and one session are enough for the current verified scope.
- Keeping the runtime backend-agnostic preserves flexibility for the eventual local model choice.

### Consequences

- Future model adapters should call the runtime layer, not `FlowgraphSession` internals.
- Tool result handling should stay explicit and machine-readable rather than depending on ad hoc strings.
- Save remains an explicit action rather than an automatic side effect.

## Session Semantics

- `save()` writes the current in-memory raw YAML back to disk.
- `validate()` writes the current in-memory raw YAML to a temporary `.grc` file and asks `grcc` to compile it.
- `validate()` treats GNU Radio error markers in stdout or stderr as failure even when `grcc` exits with `0`.
- `set_param(...)`, `connect(...)`, `disconnect(...)`, `remove_block(...)`, and the structural add workflows update both the parsed model and the raw YAML.
- Structural add workflows use a copy-validate-commit pattern so failed candidate validation leaves the live session unchanged.

## Retrieval Semantics

- `initialize_retrieval(...)` is the bounded startup seam for Phase 1. It checks graphify availability, resolves the system GNU catalog root, requires the selected root to contain `.block.yml`, `.tree.yml`, and `.domain.yml` metadata, and can optionally warm the cached catalog index.
- `search_grc(...)` supports only `catalog` and `session` scopes in Phase 1, with the public contract kept to `search_grc(query, scope="catalog|session", k=5)`.
- `catalog` scope indexes the system GNU block metadata roots only. Phase 1 intentionally excludes user-local custom blocks and examples.
- `session` scope indexes the active parsed `FlowgraphSession`, not raw YAML text, and may enrich block results from the catalog metadata when the system catalog is available.
- The current CLI startup path runs `initialize_retrieval(...)` and binds the active session context before the fake or llama runtime path continues.
- Results stay structured, bounded, deterministic, provenance-aware, and block-centric by default.
- Parameter and port metadata now boosts parent block ranking instead of appearing as equal top-level results.
- Normalized field text and an inverted token index are precomputed during index build so retrieval no longer rescans every indexed record per query.
- graphify remains a retrieval substrate only. It does not become a truth layer for either GNU metadata or the active `.grc` session.

## Catalog Semantics

- `describe_block(block_id)` is the Phase 2 package-level entry point, with the public contract intentionally kept to one required `block_id` string.
- The response stays structured and read-only: identity fields, one normalized category path, flags, source path, normalized parameters, normalized inputs/outputs, asserts, documentation/doc_url, warnings, and a compact signature string.
- Category paths come from the block-local `category` field when present, otherwise from GNU `.tree.yml` placement.
- If GNU tree metadata places one block in multiple categories, the public payload selects the first sorted path and records the ambiguity in `warnings`.
- Hierarchical wrappers are marked through `warnings`. Generated hier blocks are identified from GNU metadata such as `grc_source`; built-in hierarchical wrappers are marked only when the installed GNU Python target resolves to a `hier_block2` subclass.
- Literal GNU expressions such as `${ type }`, `${ num_inputs }`, and parameter-hide expressions are preserved as strings rather than evaluated.
- The catalog layer shares the same GNU root discovery and raw `.block.yml` loading seam used by retrieval so Phase 2 does not introduce a second catalog traversal path.
- Malformed GNU metadata and unreadable metadata files must fail inside the public `ok: false` envelope rather than surfacing raw YAML parser or file-read exceptions.

## Current Verified State

- `uv run python scripts/check_env.py` is the environment preflight check.
- `uv run ruff check` is the lint gate.
- `uv run python -m unittest` is the regression test command.
- `uv run python -m grc_agent.cli --fake tests/data/random_bit_generator.grc` is the runtime smoke test.
- GNU Radio's YAML GRC documentation confirms that `.block.yml` files carry block IDs, labels, parameters, ports, optional category, optional asserts, optional documentation, and that `.tree.yml` files map block IDs into the block tree categories. Source: <https://wiki.gnuradio.org/index.php/YAML_GRC>
- The installed GNU Radio block schema on this machine includes optional `doc_url`, `grc_source`, and `block_wrapper_path` fields for `.block.yml` files. Source: `gnuradio.grc.core.schema_checker.BLOCK_SCHEME`
- On this machine, the Phase 1 catalog root resolved to `/usr/share/gnuradio/grc/blocks`.
- The resolved Phase 1 catalog corpus on this machine contained 564 `.block.yml` files, 16 `.tree.yml` files, and 9 `.domain.yml` files.
- A real `grcc` hier-block generation pass against `/usr/share/gnuradio/examples/digital/packet/packet_rx.grc` produced a generated `packet_rx.block.yml` with `grc_source` and a hier-specific import template comment (`from packet_rx import packet_rx  # grc-generated hier_block`).
- `gnuradio.filter.pfb.channelizer_hier_ccf` resolves to a `gr.hier_block2` subclass on this machine, which supports Phase 2's lightweight hierarchical-wrapper warning path.
- `describe_block(...)` now returns structured block truth from the real GNU catalog, including normalized tree-derived category paths, literal parameter/port expressions, stable docs/doc_url fields, and hierarchical-wrapper warnings.
- `graphifyy==0.4.11` is installed in the project environment and `graphify.build_from_json()` successfully constructs the retrieval graphs used by Phase 1.
- `search_grc(...)` now returns bounded, deterministic, provenance-aware, block-centric results for both catalog and session scope, and the retrieval tests exercise the real GNU catalog plus the canonical `.grc` fixture.
- The catalog regression tests cover a known block, docs/asserts preservation, doc_url preservation, an unknown block id, and a hierarchical wrapper case against the real installed GNU catalog.
- Malformed `.block.yml` files, missing required block fields, invalid section shapes, and unreadable metadata files now return structured `CatalogLoadError` payloads in catalog tests instead of leaking parser or file-read exceptions.
- The tuned retrieval index currently builds a smaller block-centric catalog graph (643 nodes on this machine) and the first catalog query dropped from multi-second behavior to sub-second behavior in local measurement.
- Retrieval readiness now fails clearly when a selected catalog root is empty or incomplete instead of reporting a false-positive ready state.
- Duplicate retrieval node IDs are no longer silently dropped; compatible duplicates are merged intentionally, while conflicting duplicates raise a retrieval index error.
- `GRC_AGENT_LIVE_LLAMA_URL=... GRC_AGENT_LIVE_LLAMA_MODEL=... uv run python -m unittest tests.test_llama_server_live` is the env-gated live llama.cpp check.
- `GRC_AGENT_LIVE_LLAMA_URL=... GRC_AGENT_LIVE_LLAMA_MODEL=... uv run python scripts/llama_reliability_matrix.py` is the non-gating live reliability matrix.
- `.github/workflows/ci.yml` runs a fast lint job and a GNU-backed validation job on Ubuntu.
- The llama.cpp adapter follows the documented `/health`, `/v1/models`, and `/v1/chat/completions` endpoints.
- Default local llama settings live in [grc_agent.toml](../grc_agent.toml), currently targeting the alias `unsloth/gemma-4-E2B-it-GGUF` at `http://127.0.0.1:8080`.
- The adapter requires `/v1/models` to return exactly one entry and requires that `data[0].id` matches the configured alias before the first chat request.
- The bounded llama.cpp loop treats `max_steps` as a tool-round budget; one final non-tool assistant answer is allowed after the last tool round.
- The adapter regression tests use a scripted local HTTP server while still exercising real `.grc` validation on the canonical fixture.
- Local live evidence on this machine used `llama-server` `8680 (15f786e65)` with cached `unsloth/gemma-4-E2B-it-GGUF:Q4_K_M`.
- The repo default `max_tokens = 12000` is an operational ceiling only. It is not the summarize correctness fix.
- On this machine/build/model, an unshaped summarize request timed out, while bounded requests with `temperature=0.0` and `chat_template_kwargs.enable_thinking=false` produced successful real tool calls and real `grcc` validation.
- Summarize correctness now comes from deterministic finalization of the `summarize_graph.summary` payload because the raw model second response stayed lossy.
- Supported mutation correctness now comes from deterministic finalization of tool results:
  - `set_variable(ok) + validate_graph(ok)` surfaces `Set <instance_name> to <value> and validated the graph successfully.`
  - `set_variable(fail) + validate_graph(ok)` surfaces `Could not set the requested variable: <message>. The graph validated successfully.`
- Raw tool-call-like text such as `summarize_graph{}` is not surfaced as a final answer when no tools actually ran; the runtime returns `I could not complete that request with the available tools.`
- The env-gated live unittest module and the non-gating reliability matrix both passed for the supported cases on this machine.
- Transport timeout handling is covered against a delayed local HTTP server. A llama-server-specific stall reproduction is still not part of the verified contract.
- `validate()` records diagnostics from the most recent top-level validation call.
- Structural candidate validation is proven to roll back cleanly before commit.
- The canonical fixture is [tests/data/random_bit_generator.grc](../tests/data/random_bit_generator.grc).

## Structural-Edit Boundary

### Settled rules

- `remove_block(...)` is conservative: detached and unreferenced blocks only.
- `connect(...)` and `disconnect(...)` are permissive staged edits; callers validate the final graph explicitly.
- `add_block(...)` remains limited to detached `variable` blocks.
- `add_and_connect_qtgui_time_sink(...)` remains a narrow sink add-plus-connect helper.
- `add_and_connect_char_to_float_to_qtgui_time_sink(...)` remains a narrow coordinated transform tap into an existing sink.
- `add_and_connect_analog_random_source_to_qtgui_time_sink(...)` remains the smallest validated source workflow into an existing sink.
- Broader fresh-sink and throttle-inclusive source workflows stay unsupported.

### Early structural constraints

These probes established the first hard rules for block removal, detached adds, and raw YAML validity.

| ID | Mutation | `grcc` result | Derived rule |
| --- | --- | --- | --- |
| A | Remove `blocks_throttle2_0` and leave its connections | Fail | Dangling connections are invalid. |
| B | Remove `blocks_throttle2_0` and its attached wires | Fail | Removing a connected stream block still leaves neighbors invalid. |
| C | Remove `samp_rate` | Fail | Variable-like blocks can still be referenced by expressions. |
| D | Add detached `blocks_throttle2_1` | Fail | Detached stream blocks with required ports are invalid. |
| E | Add block without `states` | Fail | Raw block payload still requires `states`. |
| F | Add block without `parameters` | Fail | Raw block payload still requires `parameters`. |
| G | Add block with duplicate `name` | Fail | Block names must stay unique. |
| H | Add connection from `missing_block` | `grcc` exit `0`, but error output present | `grcc` return code alone is not a reliable validity signal. |
| J | Add detached `analog_random_source_x_1` | Fail | Detached source blocks with required outputs are invalid. |
| K | Add detached `variable` block | Pass | Zero-port variable blocks are valid while unattached. |
| L | Remove the detached `variable` block | Pass | Detached, unreferenced zero-port removal is safe. |

### Variable add follow-up

These probes narrowed the first supported `add_block()` contract to detached `variable` blocks only.

| ID | Mutation | `grcc` result | Derived rule |
| --- | --- | --- | --- |
| AB1 | Add detached `variable` block with full payload | Pass | Detached variable blocks are a safe first target. |
| AB2 | Add detached `blocks_char_to_float` block | Fail | Detached stream transforms remain invalid. |
| AB3 | Add copied `qtgui_time_sink_x` plus required connection | Pass | Some stream blocks only validate when their required ports are satisfied immediately. |
| AB4 | Add `variable` block without `states` | Fail | `states` is still required on disk. |
| AB5 | Add `variable` block without `parameters` | Fail | `parameters` is still required on disk. |
| AB6 | Add `variable` block with duplicate name | Fail | Added block names must stay unique. |
| AB7 | Add `variable` block with undefined expression | Fail | Even zero-port blocks must have semantically valid expressions. |
| AB8 | Add `variable` block with minimal generated `states` | Pass | Minimal generated `states` are sufficient for the first supported block category. |
| AB9 | Add a second detached `variable` block | Pass | The detached-variable contract is repeatable, not a one-off case. |

### Stream add-plus-connect follow-up

These probes established the first two bespoke stream workflows.

| ID | Mutation | `grcc` result | Derived rule |
| --- | --- | --- | --- |
| SW1 | Add copied sink and wire its single required input immediately | Pass | `qtgui_time_sink_x` can be added safely as an atomic add-plus-connect workflow. |
| SW2 | Repeat SW1 with minimal generated `states` | Pass | Minimal generated `states` are sufficient for the tested sink workflow. |
| SW3 | Add copied source into occupied upstream input | Fail | A copied source block is not a safe first generic stream add target. |
| SW4 | Add copied transform into occupied sink input | Fail | A copied transform tap is not safe unless the sink is expanded in the same mutation. |
| SW5 | Add copied transform and expand sink `nconnections` | Pass | Coordinated transform taps require simultaneous sink mutation. |
| SW6 | Add copied sink with duplicate name | Fail | Atomic workflows still need unique block names. |
| SW7 | Add sink from missing source block | Fail | Atomic workflows still need valid endpoints. |

### Broader transform and source follow-up

These probes tested whether broader generic stream workflows were justified.

| ID | Mutation | `grcc` result | Derived rule |
| --- | --- | --- | --- |
| BX1 | Add copied source into occupied `blocks_throttle2_0(0)` | Fail | Source workflows cannot just attach to an occupied input. |
| BX2 | Add copied source plus fresh sink with direct byte-to-sink wiring | Fail | Direct source-to-fresh-sink support is blocked by IO compatibility, not UI state. |
| BX5 | Add copied transform and expand `qtgui_time_sink_x_0` to port `1` | Pass | A narrow coordinated transform tap into an existing sink is valid. |
| BX6 | Repeat BX5 with minimal generated `states` | Pass | Minimal generated `states` still work for the coordinated transform path. |
| BX9 | Add a self-contained copied source-to-sink chain | Pass | Full self-contained source chains are valid, but broader than the implemented workflows. |
| BX10 | Add transform into sink port `1` without expanding `nconnections` | Fail | The new sink port does not exist until the sink is expanded in the same mutation. |
| BX11 | Add copied source plus copied transform into expanded sink port `1` | Pass | The smallest validated source workflow is already a coordinated two-block pipeline. |
| BX12 | Add source, throttle, and transform into expanded sink port `1` | Pass | A throttle-inclusive path also works, but it is broader than BX11. |
| BX13 | Repeat BX11 with minimal generated `states` | Pass | Minimal generated `states` are sufficient for the smaller source-plus-transform workflow. |

### Source workflow confirmation

The SX pass confirmed the smallest supported source workflow and rejected narrower assumptions.

| ID | Mutation | `grcc` result | Derived rule |
| --- | --- | --- | --- |
| SX1 | Add copied source and only expand the existing sink | Fail | Sink expansion alone is not enough for the tested source block. |
| SX2 | Add copied source plus copied transform into expanded existing sink | Pass | The smallest passing source workflow is `analog_random_source_x -> blocks_char_to_float -> qtgui_time_sink_x(port 1)`. |
| SX3 | Repeat SX2 with minimal generated `states` | Pass | Minimal generated `states` are sufficient for the smallest passing source workflow. |
| SX4 | Add copied source, throttle, and transform into expanded existing sink | Pass | A throttle-inclusive path is valid, but not required. |
| SX6 | Add copied source plus copied transform into a fresh copied sink chain | Pass | Self-contained fresh-sink chains are valid, but broader. |
| SX10 | Duplicate source name in the smallest passing source path | Fail | Multi-block workflows still need unique names. |
| SX11 | Missing upstream endpoint in the smallest passing source path | Fail | Candidate validation must still reject malformed endpoints. |
| SX12 | Invalid source parameter expression in the smallest passing source path | Fail | Candidate validation must still reject semantically invalid parameters. |

### Workflow-boundary experiments

Fresh-sink and throttle-inclusive source variants were tested to determine whether the current source workflow should be broadened.

| ID | Mutation | `grcc` result | Derived rule |
| --- | --- | --- | --- |
| CC1 | Current API shape: source -> transform -> existing sink(1) | Pass | The current two-block existing-sink workflow is the narrowest passing source shape. |
| FS1 | Fresh-sink smallest chain: source -> transform -> new float sink | Pass | Fresh-sink chains can work, but they require three new blocks instead of two. |
| FS2 | Fresh-sink with wrong sink type | Fail | A fresh-sink API introduces a new sink-type failure mode. |
| FS5 | Fresh-sink with `nconnections=0` but one connection attached | Pass | `nconnections` is advisory, not a hard port constraint, which makes fresh-sink APIs easier to misuse. |
| TX1 | Throttle-inclusive existing-sink path | Pass | A throttle-inclusive path works, but adds more surface than the current API. |
| TX3 | Source -> throttle -> existing float sink without transform | Fail | The char-to-float transform is mandatory for IO compatibility. |
| TX4 | Throttle-inclusive fresh-sink path | Pass | The broadest tested shape validates, but is even wider than the current API. |

### Removal policy experiments

Connected-block removal variants were tested to determine whether automatic removal should ever be supported.

| ID | Mutation | `grcc` result | Derived rule |
| --- | --- | --- | --- |
| RM1 | Remove throttle and leave dangling connections | Fail | Removed blocks cannot remain referenced by connections. |
| RM2 | Remove throttle and its wires | Fail | Removing a connected stream block still leaves neighbors invalid. |
| RM3 | Remove throttle, detach wires, reconnect source -> transform | Pass | A passing repair requires case-specific domain knowledge. |
| RM5 | Remove source and its wires | Fail | Removing a source leaves the downstream input unsatisfied. |
| RM7 | Remove sink and its wires | Fail | Removing a sink leaves the upstream output unsatisfied. |
| RM8 | Remove transform and its wires | Fail | Mid-chain removal breaks both neighbors at once. |
| RM10 | Remove `samp_rate` and patch all references | Pass | Variable removal also needs case-specific expression repair. |
| RM14 | Remove sink, detach wires, add fresh sink, rewire | Pass | Passing sink replacement requires adding a replacement block, not just removing one. |

### Connection policy experiments

Connection edits were tested to determine whether `connect()` and `disconnect()` should validate before committing.

| ID | Mutation | `grcc` result | Derived rule |
| --- | --- | --- | --- |
| CP1 | Disconnect source -> throttle | Fail | Disconnecting a single edge from the valid fixture immediately creates an invalid graph. |
| CP2 | Disconnect transform -> sink | Fail | Same: the sink input becomes unsatisfied immediately. |
| CP3 | Add second connection to occupied sink port `0` | Fail | Stream inputs cannot have multiple upstream blocks. |
| CP4 | Connect to non-existent sink port `5` | Fail | Invalid endpoint wiring must still fail during explicit validation. |
| CP5 | Disconnect throttle wires, remove throttle, reconnect source -> transform | Pass | Useful staged edits require temporarily invalid intermediate states. |
| CP8 | Remove all connections | Fail | Fully disconnected stream graphs are invalid. |
| CP9 | Disconnect then reconnect the same edge | Pass | A valid final state can be reached only by passing through an invalid intermediate state. |
| CP10 | Add type-mismatched source(byte) -> sink(float) connection | Fail | Type mismatches are still caught by explicit validation. |
| CP11 | Bypass throttle but leave it unconnected in the graph | Fail | An unconnected stream block with required ports is itself a validation error. |

### Rollback conclusion

A shared rollback probe was run against the smallest passing source workflow with an invalid source expression.

Observed behavior:

- candidate validation raised before commit
- parsed block count stayed unchanged
- parsed connection count stayed unchanged
- raw block and connection counts stayed unchanged
- sink `nconnections` stayed unchanged
- `is_dirty` stayed `False`

Derived rule: structural adds can safely rely on the copy-validate-commit pattern as long as the live session is untouched until candidate validation succeeds.

## Condensed Milestones

### Phases 0-3 foundation

- normalized the package under `src/grc_agent/`
- added the fixture flowgraph and first `FlowgraphSession` load, summarize, save, and validate coverage
- standardized the repo on `uv run ...` commands and the current environment checks

### Phases 4-6 core edits and validation

- implemented `set_param(...)`, `disconnect(...)`, and `connect(...)`
- proved persistence through mutate -> save -> reload tests
- added stored validation diagnostics for the most recent `grcc` run

### Phases 7-12 structural-edit implementation

- added conservative `remove_block(...)` for detached, unreferenced blocks
- added narrow `add_block(...)` support for detached `variable` blocks
- added three bespoke structural workflows for the tested sink, transform, and source expansion paths
- kept all structural adds on the copy-validate-commit pattern

### Phases 13-14 structural boundary stabilization

- resolved the remaining policy questions empirically
- broader fresh-sink and throttle-inclusive source workflows remain unsupported
- automatic connected-block removal remains unsupported
- `connect(...)` and `disconnect(...)` remain permissive staged edits that rely on explicit final validation

### Phase 15 thin runtime pass

- added `GrcAgent` as a thin tool registry over `FlowgraphSession`
- added a deterministic CLI `--fake` path to verify runtime routing without a real model backend
- kept the runtime boundary local, minimal, and framework-free

### Phase 16 repository cleanup and blueprint normalization

- shortened the README to overview, status, verification, and links
- split the earlier decision material by role before this consolidation pass
- normalized the repo around a blueprint doc for architecture and future phases

### Phase 17 retrieval index and `search_grc`

- added `src/grc_agent/retrieval/` for GNU catalog discovery, graph build/load, provenance, readiness, and bounded search
- indexed the system GNU catalog from `.block.yml`, `.tree.yml`, and `.domain.yml` metadata only
- added package-level `initialize_retrieval(...)` and `search_grc(...)` entry points without widening the model-facing runtime
- wired the bounded retrieval readiness check into CLI startup and bound the active session context there
- kept graphify behind a thin adapter and kept ranking deterministic and explainable
- added retrieval regression coverage over the real GNU catalog metadata and the canonical `random_bit_generator.grc` fixture

### Phase 18 block description and structured block truth

- added `src/grc_agent/catalog/` for shared GNU catalog discovery/loading, normalization, errors, and package-level block description
- added public `describe_block(block_id)` without widening the model-facing runtime
- kept the payload read-only and structured around identity, categories, parameters, ports, asserts, docs/doc_url, warnings, and a compact signature
- reused the same GNU root discovery and raw catalog loading seam from Phase 1 instead of introducing a second catalog traversal path
- added catalog regression coverage over real GNU metadata, including docs, doc_url, unknown-block, and hierarchical-wrapper cases

## Backlog

1. Validate one concrete tool-aware llama.cpp model/template combination against real `.grc` cases and record the evidence.
2. Add a one-session interactive CLI conversation loop over the current narrowed runtime.
3. Decide when and how the existing package-level retrieval surface should be exposed as a model-facing runtime tool beyond the current CLI startup readiness/binding seam.
4. Improve user-facing summaries, validation reporting, and error surfacing.
5. Revisit structural API growth only if new use cases are backed by new experiments.
6. Keep backend flexibility only if a second backend is justified by real use, not speculation.

## Intentionally Deferred

- direct raw YAML edits by the model
- automatic connected-block removal
- immediate validation on every `connect()` or `disconnect()`
- generic multi-block structural builders inferred from the current bespoke helpers
- broader fresh-sink source workflows
- throttle-inclusive source workflows
- heavy orchestration frameworks
- multi-flowgraph session management

## Risks And Open Questions

- The local model backend still needs to be chosen without hard-coupling the runtime to a fragile SDK path.
- Prompt size and graph-summary shape may need tuning once a real model is in the loop.
- Tool schemas should stay explicit enough that a local model does not drift into unsupported actions.
- Save semantics should remain explicit even if future interactive flows become more capable.

## Related File

- [README.md](../README.md)
- [PACKAGE_GUIDE.md](PACKAGE_GUIDE.md)
