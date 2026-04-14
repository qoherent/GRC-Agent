# Phase 4: Tool-Call Validation and Preflight Checks

> **For Hermes:** Use subagent-driven-development for implementation. Read Phase 0 through Phase 3 first. This phase is about rejecting bad tool calls early.
>
> This is a suggested plan, not a rigid checklist; the implementer can adapt the validation order if the real rules or fixtures require it.

**Goal:** Build the validation layer that checks proposed agent actions against catalog and session state before any mutation happens.

**Architecture:**
This phase is the guardrail layer. It validates proposed mutations against the current session and catalog state before any mutation touches the live graph.
Validation stays pure and in-memory. It should not save files, mutate live state, or depend on grcc for its own public contract.

**Tech Stack:**
- Python 3.12+
- catalog metadata
- session state
- GNU Radio metadata rules
- `unittest`

## Implemented baseline to reuse

- Phase 1 retrieval and Phase 2 catalog description should already provide the bounded catalog lookup surfaces below this phase.
- Phase 3 session inspection should already provide the bounded live-session lookup surface below this phase.
- Reuse `describe_block(...)` or the catalog package’s shared loaders/normalizers rather than scanning `.block.yml` files directly inside validation code.
- Reuse existing GNU catalog discovery and active-session context rather than adding another startup or metadata-scan path here.
- This phase should stay pure in-memory and consume those lower layers; it should not re-implement retrieval readiness, block description, or graph search as part of validation.

---

## Package boundary

Create a validation package:

```text
src/grc_agent/validation/
  __init__.py
  preflight.py
  errors.py
  rules.py
  checks.py
  messages.py
```

Suggested tests:

```text
tests/validation/
  test_preflight.py
  test_validation_errors.py
  test_validation_rules.py
```

## Feature definition

This phase should expose preflight validation for proposed operations such as:
- add block
- update params
- add connection
- remove connection
- remove block

It should return strict, machine-readable errors with field-level detail.
It should validate ordered transaction lists as well as single operations, using a copied or staged snapshot so one repair step can offset a temporary invalid intermediate state when the transaction semantics allow it.

## Validation ownership

The validation layer owns:
- pure preflight of proposed operations
- shape checks for operation payloads
- existence checks against session state
- simple catalog/session consistency checks
- ordered transaction simulation against a copied snapshot
- structured blocking errors and non-blocking warnings

The validation layer does not own:
- mutation commits
- save logic
- grcc as the contract itself
- broad semantic interpretation beyond what can be proven in memory

## Tests

Create tests that prove:
- invalid block ids are rejected
- invalid enum values are rejected
- missing required params are rejected
- port range errors are rejected
- incompatible dtypes are rejected
- occupied ports are rejected
- duplicate connections are rejected
- valid operations pass
- error payloads are stable and structured

Suggested output shape:
- `ok`
- `errors`
- `warnings`
- optional `error_count`
- optional `warning_count`

Each issue should include:
- `op_index`
- `op_type`
- `field` or `path`
- `code`
- `message`
- optional `hint`

## Strict pass criteria

This phase is done only when all are true:
- bad tool calls are rejected before mutation
- errors are structured and specific
- checks are pure in-memory
- tests cover the important failure modes on real metadata
- warnings do not block
- ordered transaction validation works on a copied/staged snapshot
- grcc remains a downstream semantic authority, not the validation contract itself


## Coding rules for implementers

- Read Phase 0–3 first.
- Do not mutate state in the validation layer.
- Do not hide errors behind generic messages.
- Prefer precise field-level diagnostics.
- Keep rules explicit and testable.
- Validate against real catalog and session data.
- Prefer a simple check registry over a rule engine unless the repo proves otherwise.
- Order errors deterministically: operation order first, then prerequisite/shape/existence, then uniqueness/occupancy/compatibility.
- Do not cascade secondary errors when a prerequisite on the same op already failed.
- update all relevant docs

## Refactors / removals

- Remove implicit validation behavior from mutation helpers once this layer exists.
- Move permissive or duplicated validation checks out of session mutation code.
- Keep only thin calls or adapters in the old paths if backward compatibility is needed temporarily.

## Suggested error schema

Use a stable issue shape for both errors and warnings:
- `op_index`
- `op_type`
- `field` or `path`
- `code`
- `message`
- optional `hint`

## Suggested check model

Use a straightforward registry of explicit check functions rather than a rule engine. The current repo has a small number of known invariants and benefits more from clarity than abstraction.

## Deliverables

- preflight validation package
- structured error schema
- rule coverage tests
- docs describing what is rejected and why

## Tool I/O contract

### Preflight validation input
A transaction operation list with ordered items such as add/remove/update/connect operations.

### Preflight validation output
- `ok`: boolean
- `errors`: list of structured validation errors
- `warnings`: optional list of structured warnings
- each error should include:
  - operation index
  - operation type
  - field
  - code
  - message
  - corrective hint or valid alternatives where relevant

## Implementation checklist

- define the operation schema before coding checks
- define the minimum data required for each operation
- define field-level error codes
- define how multiple errors are ordered
- define what stays pure in-memory
- update all relevant docs

## Refactor / remove checklist

- remove duplicated validation from mutation code paths
- remove permissive fallback behavior from old helpers where possible
- keep only thin compatibility layers if needed temporarily
