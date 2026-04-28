# AGENTS.md

## Vision

GRC Agent should become a reliable local autonomous assistant for GNU Radio Companion graphs: it should understand natural prompts, inspect the active graph, make validated tool-based mutations, verify outcomes, and ask the user naturally when required information is missing or contradictory.

Autonomy must come from typed state, explicit tools, deterministic validation, and measured behavior. It must not come from hidden repairs, prompt tricks, YAML patching, fixture-specific shortcuts, or unbounded retries.

## Engineering Rules

- Be concise, objective, and grounded.
- Ask for missing details when needed; do not guess unsupported graph facts.
- Reject ad-hoc logic that adds redundancy, latency, cost, maintenance burden, or worse performance.
- Prefer simple, deterministic, typed, testable designs over clever or fragile flows.
- Prefer the smallest correct change, smallest useful experiment, and smallest sufficient test gate.
- Do not optimize for passing tests at the expense of correctness, safety, or maintainability.
- Measurement comes before redesign: prove a repeated generic gap before changing prompts, schemas, tool order, or runtime behavior.

## Tooling

- Use `uv run` for commands.
- Use `uv add` / `uv add --dev` for dependencies.
- Keep package code under `src/grc_agent/` and use package imports.
- Keep `pyproject.toml` authoritative for metadata and dependencies.
- Use stdlib `unittest` for deterministic tests.
- Lint gate: `uv run ruff check src/ tests/`.
- Full repo lint gate after cleanup/refactors: `uv run ruff check`.
- Regression gate: `uv run python -m unittest`.
- Canonical example fixture: `tests/data/random_bit_generator.grc`. Do not duplicate it at repo root.

## Research And Accuracy

- Use current packages, APIs, model behavior, and documented GNU Radio behavior.
- Before changing GNU-facing behavior, verify against real `.grc` files and `grcc`.
- Do not rely on stale assumptions or undocumented behavior when docs/tests can verify it.
- Record new GNU behavior, edge cases, widened API contracts, and accepted limitations in `docs/BLUEPRINT.md`.

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
- Keep `llama_server.py` transport/loop oriented; GNU Radio policy belongs behind `GrcAgent` and verified tools.

## Tool And Workflow Design

- Tool order matters. Models prefer earlier tools.
- Keep `apply_edit` before `propose_edit` unless a separate live eval proves a safer alternative.
- Keep tool descriptions concise and non-contradictory; put durable routing policy in one place.
- Avoid overlapping tools unless each has a tested, distinct job.
- Add new tools only when repeated evidence proves the current surface is insufficient.
- Before adding or removing a tool, confirm the failure repeats across unrelated cases, the fix is generic, it can be tested without fixture hacks, and it preserves rollback plus `grcc` validation.
- If safe execution is ambiguous, ask for clarification using real graph/tool candidates rather than guessing.
- Clarification options must come from real executable candidates, not hardcoded wording.
- Always include a free-text/custom option when asking the user to choose.

## Autonomy Design

- Prefer a typed turn-state/executor policy over more prompt rules.
- Track requested actions, completed actions, failed actions, mutation state, validation state, save requirements, clarification state, and retry budget explicitly.
- The orchestrator may decide whether to continue, stop, report failure, or ask the user; it must not synthesize graph recipes or mutate outside tools.
- One corrected retry is acceptable only when the user requested recovery and the tool error gives a clear generic fix.
- Do not add a broad planner that invents graph designs from tutorials, examples, or memory.

## Eval And Testing

- Keep deterministic safety tests separate from live/model evals.
- Tier 1 and Tier 2 live evals are routing and behavior evidence, not proof of full autonomy.
- Live eval reports should distinguish routing pass, tool success, semantic/end-state pass, and safety pass.
- Add semantic checks before changing tool design: exact params, graph diffs, saved files, reload/`grcc`, clarification resolution, and no-mutation previews.
- Run targeted tests while iterating.
- Run the full deterministic suite after runtime, schema, tool, prompt, validation, transaction, or harness changes.
- Run Tier 1 after prompt/schema/runtime/live-eval harness changes.
- Run Tier 2 after adapter changes, tool-order changes, major prompt changes, or release candidates.
- Do not delete or weaken safety tests.
- Reclassify bad tests only when the expectation is demonstrably wrong.

## Tutorial Corpus And RAG

- Keep `docs/wiki_gnuradio_org/` as explanation/retrieval/eval material.
- Tutorials are not mutation authority.
- Do not turn tutorials into hidden runtime recipes, block allowlists, block blacklists, or parameter defaults.
- Catalog metadata remains authority for block IDs, ports, params, defaults, and signatures.
- `grcc` remains authority for graph validity.
- Future tutorial retrieval must be read-only, provenance-first, and explanation-scoped before any mutation-adjacent evals.

## Documentation

- `docs/BLUEPRINT.md` is the current source of truth.
- `README.md` should explain the product status, reliability truth, usage, and roadmap without stale reports.
- `docs/QUICKSTART.md` should stay procedural.
- Update docs when tool count, architecture, safety rules, eval gates, capability status, or roadmap changes.
- Keep docs concise and actionable.
