# Structural Edits Policies And Boundaries

This appendix records the experiment families that froze the structural-edit surface instead of widening it.

## Workflow Boundary Experiments (Phase 13)

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

### Phase 13 Decision

The current bespoke source workflow is the correct stopping point.

- No broader variant was narrower than the implemented API.
- Fresh-sink variants add both new block count and a hidden `nconnections` correctness trap.
- Throttle-inclusive variants are strictly larger and do not simplify the contract.

## Removal Policy Experiments (Phase 14)

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

### Phase 14 Removal Decision

Automatic connected-block removal remains unsupported.

- Simple removal plus wire deletion always failed.
- Every passing case needed a different repair strategy.
- No narrow, reusable automatic-removal contract exists.

## Connection Policy Experiments (Phase 14)

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

### Phase 14 Connection Decision

Connection edits remain permissive and do not perform immediate invariant enforcement.

- Every useful staged rewrite starts from an invalid intermediate disconnect.
- Immediate validation on `connect()` and `disconnect()` would block all staged rewiring.
- The correct contract is explicit final-state validation via `validate()`.

## Stabilized Structural-Edit Policy

The structural-edit surface is now considered stable.

- Connected-block removal stays conservative: detached and unreferenced only.
- `connect()` and `disconnect()` stay permissive so staged rewiring remains possible.
- Broader fresh-sink and throttle-inclusive source workflows remain intentionally unsupported.
- Further API widening should happen only if a new concrete use case is backed by new experiments.
