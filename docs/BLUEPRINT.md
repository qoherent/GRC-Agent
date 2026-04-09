# Project Blueprint
Link to project: https://riahub.ai/mahmoud/GRC_Agent
## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs.
The project exists to inspect, explain, modify, validate, and eventually drive
safe graph changes without letting the model edit raw YAML directly.

## Current Product Shape

- one `.grc` file per session
- headless CLI
- local validation through `grcc`
- safe mutations owned by `FlowgraphSession`
- structural-edit session surface frozen unless a new experiment pass justifies change
- model-facing runtime deliberately narrower than the session layer
- deterministic `--fake` runtime path for smoke testing
- no real local model adapter wired yet

## Canonical Working Rules

- raw `.grc` YAML remains the persistence format and must not be edited by the model directly
- all meaningful graph mutations go through `FlowgraphSession`
- session mutations must keep parsed objects and raw YAML in sync
- explicit `validate()` remains the final graph-correctness gate
- model-facing `save_graph` is rejected until the latest dirty state has passed validation
- structural APIs widen only when new experiments justify a narrower or clearly necessary contract

## Architecture Layers

### 1. Raw `.grc` on disk

- YAML on disk remains the persistence format.
- Save and validation operate from the same in-memory raw YAML snapshot.

### 2. Session / thin IR layer

- [src/grc_agent/models.py](../src/grc_agent/models.py) holds the lightweight parsed dataclasses.
- [src/grc_agent/flowgraph_session.py](../src/grc_agent/flowgraph_session.py) owns load, summarize, save, validate, and all mutation primitives.
- The session layer is broader than the model-facing runtime because it is also the regression-tested experimentation surface.

### 3. Model-facing runtime layer

- [src/grc_agent/agent.py](../src/grc_agent/agent.py) owns the runtime tool registry and turn history.
- The runtime exposes only four tools: `summarize_graph`, `set_variable`, `validate_graph`, and `save_graph`.
- `set_variable` is intentionally narrower than generic `set_param(...)`; it updates only the `value` parameter on `variable` blocks.
- `save_graph` is explicit and gated by successful validation of the latest dirty state.

### 4. CLI boundary

- [src/grc_agent/cli.py](../src/grc_agent/cli.py) remains a thin entrypoint.
- The `--fake` path exists only to prove the runtime boundary and tool routing without introducing a model backend.

### 5. Future local adapter

- A real local model adapter should call the runtime layer, not `FlowgraphSession` directly.
- Tool schemas, prompt construction, and stop conditions should stay thin and explicit.
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

## Current Verified State

- `uv run python scripts/check_env.py` is the environment preflight check.
- `uv run ruff check` is the lint gate.
- `uv run python -m unittest` is the regression test command.
- `uv run python -m grc_agent.cli --fake tests/data/random_bit_generator.grc` is the runtime smoke test.
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

## Backlog

1. Wire a real local model adapter into the narrowed `GrcAgent` tool surface.
2. Formalize tool schemas and prompt/context construction for the runtime.
3. Add a one-session interactive CLI conversation loop over the current runtime.
4. Improve user-facing summaries, validation reporting, and error surfacing.
5. Revisit structural API growth only if new use cases are backed by new experiments.

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
