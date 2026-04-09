# Structural Edits

## Status

The structural-edit surface is considered stable as of Phases 13-14.

The current implementation supports:

- `remove_block(...)` for detached, unreferenced blocks only
- `add_block(...)` for detached `variable` blocks only
- `add_and_connect_qtgui_time_sink(...)`
- `add_and_connect_char_to_float_to_qtgui_time_sink(...)`
- `add_and_connect_analog_random_source_to_qtgui_time_sink(...)`

## Settled Rules

- The model must never edit raw `.grc` YAML directly.
- All meaningful graph mutations go through `FlowgraphSession`.
- `connect(...)` and `disconnect(...)` remain permissive staged edits; callers validate the final graph explicitly.
- Connected-block removal stays conservative: detached and unreferenced only.
- Broader fresh-sink and throttle-inclusive source workflows remain intentionally unsupported.

## Why The Boundary Stays Narrow

- Connected-block removal always required case-specific repair rather than a reusable automatic contract.
- Every useful rewiring path passed through invalid intermediate states, so immediate invariant enforcement would block staged edits.
- Broader source workflows were wider than the implemented API and introduced more failure modes without simplifying the public contract.
- Minimal generated `states` were sufficient for the currently supported structural additions, so a generic block-builder abstraction was not justified.

## What Remains Deferred

- automatic removal of connected stream blocks
- immediate validation on every `connect()` or `disconnect()`
- broader fresh-sink source workflows
- throttle-inclusive source workflows
- generic multi-block structural builders inferred from the bespoke helpers

## Supporting Notes

- [structural_edits_foundations.md](./structural_edits_foundations.md): experiment log and conclusions for the implemented structural-edit surface from the early passes
- [structural_edits_policies.md](./structural_edits_policies.md): workflow-boundary and policy experiments that froze the surface in Phases 13-14

## Consequence For Future Work

Further feature work should build on the existing session and runtime surface rather than widening structural APIs casually. Any proposed structural expansion should start with new `.grc` mutation experiments and an explicit decision update.