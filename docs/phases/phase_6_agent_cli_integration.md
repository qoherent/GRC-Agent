# Phase 6: Agent and CLI Integration

> **For Hermes:** Use subagent-driven-development for implementation. Read Phase 0 through Phase 5 first. This is the orchestration layer.
>
> This is a suggested plan, not a strict script; the implementer can adjust routing or command shape if the lower layers expose a better final interface.

**Goal:** Wire the new retrieval, description, session, validation, and transaction packages into the agent runtime and CLI without turning the agent into a monolith.

**Architecture:**
This phase keeps the model-facing surface small while routing each tool to the correct isolated package. The CLI stays explicit and thin.
The orchestration layer should only glue package owners together, normalize tool errors, and keep the final tool surface stable.
Eager startup should stay bounded: load config and check the selected backend/session entrypoints early, but defer heavier retrieval/catalog/session work until the relevant tool path needs it.

**Tech Stack:**
- Python 3.12+
- current CLI stack
- agent runtime
- graph retrieval and transaction packages
- `unittest`

## Prior phase baseline to reuse

- Phase 1 already provides package-level retrieval through `initialize_retrieval(...)` and `search_grc(...)`.
- The CLI startup path already runs the bounded retrieval readiness check and binds the active session context before runtime flow continues.
- This phase should integrate that existing retrieval surface into the agent/tool loop if needed, not redesign startup readiness or rebuild search from scratch.

---

## Package boundary

Modify only the thin agent orchestration and CLI integration layers.

Suggested touch points:
- `src/grc_agent/agent.py`
- `src/grc_agent/cli.py`
- `src/grc_agent/__init__.py`

## Feature definition

The runtime should expose the final tool loop in a small, explicit form:
- `load_grc`
- `summarize_graph`
- `search_grc`
- `get_grc_context`
- `describe_block`
- `propose_edit`
- `apply_edit`
- `validate_graph`
- `save_graph`

`set_variable` should not be part of the final public model-facing surface unless a compatibility shim is explicitly required.

The orchestration layer should not own business rules. It should only route, normalize, and present the tool surface.

## Tests

Create tests that prove:
- tools route to the correct package
- tool outputs remain structured
- CLI can call the read-only tools
- CLI can surface validation and mutation outcomes
- unsupported actions fail clearly
- the orchestration layer does not duplicate business logic

Suggested test location:

```text
tests/integration/
  test_agent_tool_routing.py
  test_cli_tool_flow.py
```

## Strict success criteria

This phase is done only when all are true:
- orchestration is thin
- tool routing is explicit
- no duplicated logic appears in the agent layer
- CLI and agent tests pass on the real flow
- unsupported actions fail with stable structured errors
- startup stays bounded: cheap checks early, heavier work only when needed

## Coding rules for implementers

- Read Phase 0 through Phase 5 first.
- Do not reimplement package logic inside orchestration.
- Keep the tool surface stable and small.
- Keep CLI commands explicit.
- Prefer direct routing over abstraction layers.
- Verify behavior on real fixtures and real GNU tools.
- Keep eager startup bounded; defer retrieval/catalog/session work until needed.
- Keep read-only and edit flows clearly separated.
- Do not let unsupported actions fall through to heuristics.

## Refactors / removals

- Remove business logic from `agent.py` if it is duplicated elsewhere.
- Remove CLI parsing shortcuts that obscure the final tool flow.
- Deprecate the old narrow runtime tool contract once the new loop is in place.
- Update docs to reflect the new tool names and phase layout.
- Remove any lingering assumption that `set_variable` is a first-class public tool if Phase 5 transaction editing covers the needed mutations.

## Deliverables

- integrated agent runtime
- thin CLI updates
- orchestration tests
- docs update for the final tool surface
- updated README.md, docs/BLUEPRINT.md, and docs/PACKAGE_GUIDE.md to reflect the new final tool surface and flow

## Tool I/O contract

The orchestration layer should route but not re-interpret the tool outputs.

Expected routed tools:
- `load_grc`
- `summarize_graph`
- `search_grc`
- `get_grc_context`
- `describe_block`
- `propose_edit`
- `apply_edit`
- `validate_graph`
- `save_graph`

The agent/CLI layer should preserve:
- structured success payloads
- structured errors
- bounded retrieval payloads
- explicit validation results

The CLI should stay thin and explicit. Prefer a small set of clear modes or subcommands rather than burying routing logic in flags, and keep unsupported CLI actions failing with normal CLI-style errors.

## Implementation checklist

- define how each public tool maps to a package
- define which tool outputs are passed through untouched
- define which user messages trigger read-only flow vs edit flow
- define CLI command boundaries and defaults
- define how unsupported actions fail
- define how the final history is rendered
- update all relevant docs

## Strict pass criteria

- orchestration remains thin
- no duplicated business logic exists in agent.py or cli.py
- tool routing is explicit
- read-only tools work end to end
- edit and validation outcomes are surfaced clearly
- tests cover the real flow using fixtures

## Refactor / remove checklist

- remove any duplicated business logic from orchestration files
- remove old runtime assumptions from user-facing docs once the new tool loop is live
- keep agent/CLI as routing layers only
