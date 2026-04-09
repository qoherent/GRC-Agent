# Structural Edits Foundations

This appendix records the experiment families that justified the implemented structural-edit surface before the boundary was frozen.

## Fixture

Base fixture: `tests/data/random_bit_generator.grc`

## Early Structural Constraints

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

## Variable Add Block Follow-Up

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

## Stream Add-Plus-Connect Follow-Up

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

## Broader Transform and Source Follow-Up

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

## Source Workflow Confirmation

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

## Rollback Probe

A shared rollback probe was run against the smallest passing source workflow with an invalid source expression.

Observed behavior:

- candidate validation raised before commit
- parsed block count stayed unchanged
- parsed connection count stayed unchanged
- raw block and connection counts stayed unchanged
- sink `nconnections` stayed unchanged
- `is_dirty` stayed `False`

Derived rule: structural adds can safely rely on the copy-validate-commit pattern as long as the live session is untouched until candidate validation succeeds.

## Implemented Contracts That Followed

The experiments above directly justify the currently implemented structural-edit surface:

- `remove_block(instance_name)` for detached, unreferenced blocks only
- `add_block(instance_name, block_type, parameters, states=None)` for detached `variable` blocks only
- `add_and_connect_qtgui_time_sink(...)` for a copied one-input sink block
- `add_and_connect_char_to_float_to_qtgui_time_sink(...)` for a coordinated transform tap into an existing sink
- `add_and_connect_analog_random_source_to_qtgui_time_sink(...)` for the smallest existing-sink source path

## Consequences

- Raw block payloads still need `name`, `id`, `parameters`, and `states`.
- Minimal generated `states` are sufficient for the currently supported add workflows.
- Broader stream builders were intentionally not generalized from these results.
- All higher-level workflows remain justified by specific experiment families rather than by a generic graph-building abstraction.
