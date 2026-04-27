# AGENTS.md

## Core Rules

- Be concise, objective, and grounded.
- Ask for missing details when needed. Do not guess or make unsupported assumptions.
- Call out bad ideas, outdated approaches, weak logic, and unnecessary complexity before implementation.
- Reject ad-hoc logic that adds redundancy, latency, cost, maintenance burden, or worse performance.
- Prefer simple, deterministic, typed, testable designs over clever or fragile flows.
- Prefer the smallest correct change, smallest useful experiment, and smallest sufficient test gate.
- Do not optimize for passing tests at the expense of correctness, safety, or maintainability.

## Tooling

- Use `uv run` for commands.
- Use `uv add` / `uv add --dev` for dependencies.
- Keep package code under `src/grc_agent/` and use package imports.
- Keep `pyproject.toml` authoritative for metadata and dependencies.
- Use stdlib `unittest` for now.
- Lint gate: `uv run ruff check src/ tests/`.
- Regression gate: `uv run python -m unittest`.
- Canonical example fixture: `tests/data/random_bit_generator.grc`. Do not duplicate it at repo root.

## Research and Accuracy

- Use current packages, APIs, model behavior, and documented GNU Radio behavior.
- Before changing GNU-facing behavior, verify against real `.grc` files and `grcc`.
- Do not rely on stale assumptions or undocumented behavior when docs/tests can verify it.
- Record new GNU behavior, edge cases, or widened API contracts in `docs/BLUEPRINT.md` with evidence.

## GRC Safety Contract

- Never edit raw `.grc` YAML directly.
- All graph mutations must go through verified tools.
- `apply_edit` / verified workflow tools are the mutation boundary.
- `grcc` remains final validation authority.
- Failed edits must roll back atomically.
- Preview operations must never mutate.
- Save only when explicitly requested.
- No hidden repairs.
- No prompt-regex transaction rewriting.
- No fixture-specific logic.
- No block recipes or block blacklists.
- No unbounded retries or unbounded candidate search.
- Keep `llama_server.py` transport-only; no GNU Radio domain logic there.

## Tool and Workflow Design

- Tool order matters. Models prefer earlier tools.
- Keep `apply_edit` before `propose_edit`.
- Add new tools only when repeated evidence proves the current tool surface is insufficient.
- Before adding a tool, confirm:
  - the failure repeats across unrelated cases
  - the fix is generic
  - it can be tested without fixture hacks
  - it preserves rollback and `grcc` validation
- If safe execution is ambiguous, return structured clarification options instead of guessing.
- Clarification options must come from real graph/tool candidates, not hardcoded wording.
- Always include a free-text/custom option when asking the user to choose.

## Eval and Testing

- Run targeted tests while iterating.
- Run the full deterministic suite after runtime, schema, tool, or validation changes.
- Do not delete or weaken safety tests.
- Reclassify bad tests only when the expectation is demonstrably wrong.
- Keep live/model evals separate from deterministic regression tests.
- Use focused live evals before slow full sweeps.

## Documentation

- `docs/BLUEPRINT.md` is the current source of truth.
- Historical reports can stay historical, but current docs must not contradict current behavior.
- Update docs when tool count, architecture, safety rules, or capability status changes.
- Keep docs concise and actionable.