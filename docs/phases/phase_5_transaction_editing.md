# Phase 5: Transaction Editing and Commit Flow

> **For Hermes:** Use subagent-driven-development for implementation. Read Phase 0 through Phase 4 first. This phase is the first one that mutates the graph.
>
> This is a suggested plan, not a fixed contract; the implementer may change sequencing if that produces a safer or clearer transaction flow.

**Goal:** Build an atomic edit flow that applies validated transactions to the active `.grc` session and commits only when the final graph is valid.

**Architecture:**
This phase consumes the retrieval, description, session, and validation layers. It turns a small ordered transaction into an applied graph change, then runs final GNU Radio validation before commit.
Rollback should be snapshot-based for this phase: stage on a copied session state, apply the ordered ops, validate, and only then swap the live state. If anything fails, discard the candidate and leave the live session unchanged.

The first public transaction contract should stay intentionally narrow. Prefer the smallest safe edit surface that is demonstrably supported by the current session and fixture behavior.

**Tech Stack:**
- Python 3.12+
- `.grc` session model
- validation layer
- `grcc`
- `unittest`

## Implemented baseline to reuse

- Phase 1 retrieval readiness/search and Phase 2 catalog description are already live, and Phase 4 should already provide pure preflight validation.
- This phase should consume those lower layers rather than bypassing them with bespoke catalog/session lookups or ad-hoc block schema parsing inside transaction code.
- Keep the current bounded startup behavior intact: cheap retrieval/session checks early, heavier catalog or transaction work only when the edit path actually needs it.

---

## Package boundary

Create a transaction package:

```text
src/grc_agent/transaction/
  __init__.py
  edit.py
  apply.py
  commit.py
  planner.py
  rollback.py
```

Suggested tests:

```text
tests/transaction/
  test_transaction_apply.py
  test_transaction_commit.py
  test_transaction_rollback.py
```

## Feature definition

This phase should support a flat ordered transaction such as:
- update params
- add connection
- remove connection
- remove detached/unreferenced block
- add detached variable block

Broader structural builders can stay internal adapters for now if they are needed, but the first explicit transaction contract should remain narrow and safe.

Suggested tools:
- `propose_edit(transaction)`
- `apply_edit(transaction)`
- `validate_graph()`
- `save_graph(path=None)`

## Tests

Create tests that prove:
- a valid transaction passes preflight
- a valid transaction mutates a copy before commit
- failed validation leaves live state unchanged
- commit only happens after final GNU validation
- save is blocked until validation passes
- transaction ordering is respected
- multi-step changes remain atomic
- rollback restores the previous live state

## Strict pass criteria

This phase is done only when all are true:
- transaction edits are atomic
- mutation happens only after preflight
- final commit is gated by GNU validation
- rollback is verified
- tests cover both success and failure paths
- rollback is snapshot-based and leaves live state unchanged on any failure
- the first public transaction contract stays narrow and explicit

## Coding rules for implementers

- Read Phase 0–4 first.
- Keep transaction semantics simple and ordered.
- Do not bypass validation.
- Do not split one atomic transaction into hidden side effects.
- Keep commit behavior explicit and testable.
- Verify on the real GNU Radio path.

## Refactors / removals

- Refactor current mutation helpers in `flowgraph_session.py` to become thin lower-level primitives or adapters.
- Remove direct agent-facing exposure of low-level mutators if the new transaction path replaces them.
- De-emphasize the old narrow mutation contract from the final public tool surface.

## Deliverables

- transaction engine package
- apply/commit flow
- atomic transaction tests
- docs describing the final edit contract

## Tool I/O contract

### `propose_edit(transaction)`
- input: ordered transaction list
- output: preflight result, planned operations, normalized ops, and any blocking issues
- `commit_eligible`: false on proposal, only true after a successful apply/validate cycle

### `apply_edit(transaction)`
- input: preflight-approved transaction
- output: applied status, dirty state, affected nodes/edges, commit eligibility, and revision markers
- if anything fails mid-flight, live state must remain unchanged

### `validate_graph()`
- input: none
- output: validation result, diagnostics, return code, and validation success flag
- should validate the current live staged state

### `save_graph(path=None)`
- input: optional path string
- output: saved path, success flag, and dirty state
- save is allowed only after the latest dirty revision validated successfully

## Implementation checklist

- define the transaction atomicity rule before coding
- define how rollback behaves on failure
- define when dirty state changes
- define what happens after validation failure
- define how commit eligibility is represented
- define how transaction order is preserved
- define the simplest safe transaction object shape before coding
- define whether `set_param` stays internal or becomes public; keep the first contract narrow unless real evidence requires widening
- update all relevant docs

## Strict pass criteria

- one transaction is either fully applied or not applied
- rollback restores the previous state
- save is blocked until validation succeeds
- final graph validity is checked explicitly
- tests cover success, failure, and rollback paths

## Refactor / remove checklist

- refactor current mutation helpers into lower-level primitives or adapters
- remove direct agent-facing exposure of old low-level mutators if the new path replaces them
- de-emphasize the old narrow mutation contract in final public tool docs
