# Phase 0: Context, Rules, and Shared Contracts

> **For Hermes:** This is the glue phase for all later phases. Read this file before implementing any later phase. Do not code from this file alone; always pair it with the phase-specific plan and the current repo/docs state.

**Goal:** Define the canonical architecture, the strict coding rules, the package boundaries, and the phase order that all later isolated packages must follow.

**Architecture:**
The agent is GRC-native. Installed GNU Radio metadata is the source of truth for block truth. The active `.grc` file is the source of truth for the live session. graphify is allowed as a retrieval/index layer only. The implementation should be split into isolated packages so later AI agents can work one phase at a time without guessing or cross-contaminating concerns.

**Tech Stack:**
- Python 3.12+
- GNU Radio / `grcc`
- graphify for retrieval only
- `unittest`
- `uv run` / `uv add`

---

## Current repo anchors

Keep the existing root package under `src/grc_agent/` and evolve it by adding isolated subpackages rather than stuffing everything into `agent.py`.

Current important files:
- `src/grc_agent/agent.py`
- `src/grc_agent/cli.py`
- `src/grc_agent/flowgraph_session.py`
- `src/grc_agent/models.py`
- `src/grc_agent/config.py`
- `src/grc_agent/llama_server.py`
- `src/grc_agent/retrieval/`
- `docs/BLUEPRINT.md`
- `docs/PACKAGE_GUIDE.md`
- `README.md`
- `tests/retrieval/`
- `tests/data/random_bit_generator.grc`

## Canonical system model

- Installed GNU Radio metadata = canonical block schema truth
- Active `.grc` session = canonical session truth
- graphify = retrieval/index layer, not truth layer
- Retrieval must stay bounded and provenance-aware
- Mutation must stay isolated and validated
- Save must remain explicit

## Global coding rules for every phase

These rules apply to every later implementation agent:

1. Read Phase 0 and the phase-specific file before coding.
2. Read the current repo state before making assumptions.
3. Read the relevant GNU Radio docs before changing GNU-facing behavior.
4. Verify GNU-facing behavior on real `.grc` files and real `grcc` runs.
5. Never infer GNU semantics from YAML shape alone.
6. Prefer the smallest passing change and the smallest passing test gate.
7. Keep outputs structured, short, and machine-readable.
8. Do not duplicate logic across packages.
9. Keep packages isolated and focused on one concern.
10. Do not widen mutation unless the phase explicitly asks for it.
11. Ask clarifying questions before coding if anything is ambiguous.
12. Plan the implementation in detail before writing code.
13. Reject ad-hoc logic that adds redundancy, extra cost, latency, or unclear behavior.
14. Record any widened GNU-facing behavior in `docs/BLUEPRINT.md` with real evidence.

## Proposed package tree

The later phases should converge toward this structure:

```text
src/grc_agent/
  agent.py
  cli.py
  config.py
  flowgraph_session.py
  llama_server.py
  models.py
  retrieval/
  catalog/
  session/
  validation/
  transaction/
```

Status today:
- `src/grc_agent/retrieval/` is implemented and should be treated as the current Phase 1 baseline.
- later phases should build on that package rather than re-creating GNU metadata discovery, graphify wiring, or bounded search from scratch.

Testing should follow the same boundaries:

```text
tests/
  retrieval/
  catalog/
  session/
  validation/
  transaction/
  integration/
  data/
```

## Phase order

Implement in this order:

1. Phase 1: retrieval index and `search_grc`
2. Phase 2: block description and structured block truth
3. Phase 3: session graph access and bounded context
4. Phase 4: validation and preflight checks
5. Phase 5: transaction editing and commit flow
6. Phase 6: agent/CLI integration

## What should be refactored or removed over time

- Keep `flowgraph_session.py` as the session owner, but do not keep adding unrelated retrieval logic there.
- Keep `agent.py` thin; avoid putting search or transaction logic directly in it.
- Keep `cli.py` thin; avoid duplicating business logic there.
- Phase 1 has landed; do not reintroduce ad-hoc search code in the agent or CLI layers.
- Remove any future prompt-only block schema assumptions once Phase 2 lands.
- Remove any implicit validation behavior from mutation paths once Phase 4 and Phase 5 land.
- Remove narrow, bespoke runtime-only mutation contracts from the final public agent surface once the new tool loop is in place.

## Success criteria for Phase 0

- The phase plan folder exists.
- The phase order is explicit.
- The package boundaries are explicit.
- The coding rules are strong enough for AI implementers to follow without guessing.
- The retrieval-only role of graphify is clear.
- The later phases can reference this file as shared context.

## Test expectations for Phase 0

This phase is documentation-only.

Success means:
- the phase docs exist
- the phase docs agree with each other
- the phase docs align with the current blueprint and package guide
- later phases can use this file as the shared rules source

## Hard stop rules

- Do not implement mutation before retrieval and description exist.
- Do not treat graphify as canonical GRC truth.
- Do not assume a search result is enough without structured description or bounded context.
- Do not let one phase leak responsibilities into another.

## Implementation checklist for later agents

Before starting any phase, the implementer must confirm:
- the phase number and scope
- the exact package boundary
- the current repo files to read
- the real GNU Radio docs or metadata needed
- the precise tests that prove success
- what should be refactored or removed from existing code
- the exact I/O shape of the tools touched by the phase
- update all relevant docs

## Shared I/O conventions

All later tools should follow these rules:
- structured inputs only
- structured outputs only
- bounded result sets
- provenance included where useful
- stable error shapes
- no hidden side effects
- no whole-corpus dumps

## Phase completion gate

A phase is not complete unless:
- the tests are explicit
- the pass criteria are strict
- the resulting tool I/O is documented
- any refactors/removals are listed
- the next phase can build on it without reinterpreting it
