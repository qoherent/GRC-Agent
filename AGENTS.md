# AGENTS.md

## Vision

GRC Agent should become a reliable local assistant for GNU Radio Companion graphs: it should understand natural prompts, inspect the active graph, make validated tool-based mutations, verify outcomes, and ask the user naturally when required information is missing or contradictory.

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
- Context budgeting rule: keep wrapper/tool outputs compact via retrieval and
  schema limits; do not treat low `max_tokens` as compression.
- Desired llama context target is `120000` tokens when server/model support it;
  verify desired vs actual via doctor/health before claiming large-context behavior.

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

- Default model-facing chat surface is MVP wrappers only:
  `inspect_graph`, `search_blocks`, `ask_grc_docs`, `change_graph`,
  `save_graph_explicit`, `load_graph_explicit`.
- `change_graph` remains mutation-only. Save/load are explicit lifecycle wrappers
  and require explicit user intent.
- Save/load wrappers are beta-validated and not release-validated yet.
- Legacy low-level tools remain internal/compatibility-only unless explicitly
  enabled for developer compatibility mode.
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

- Advisor-first intent classification is the intended direction: a local LLM
  advisor classifies user intent into a small structured mode, and runtime code
  maps that mode to a bounded tool class.
- Advisor is currently shadow-only and does not control default runtime routing.
- Current MVP advisor shadow vocabulary is:
  `inspect`, `preview`, `change`, `clarify`, `unsupported`.
- Do not solve intent routing with regexes, phrase dictionaries, or hardcoded
  natural-language branches. Intent routing belongs to the Advisor.
- Runtime code enforces contracts and graph safety: enum/schema validation,
  allowed tool lists, operation type validation, preflight, `grcc`, rollback,
  explicit save state, and UID target-ref structure.
- Runtime code must not maintain phrase lists for preview wording, raw YAML
  wording, vague topology wording, block UID wording, or natural-language
  synonyms.
- If the Advisor is uncertain, it must output `clarify` or `unsupported`;
  improve advisor prompt/context/evals rather than bypassing it with growing
  hardcoded language rules.
- Prefer a typed turn-state/executor policy over more prompt rules.
- Track requested actions, completed actions, failed actions, mutation state, validation state, save requirements, clarification state, and retry budget explicitly.
- The orchestrator may decide whether to continue, stop, report failure, or ask the user; it must not synthesize graph recipes or mutate outside tools.
- One corrected retry is acceptable only when the user requested recovery and the tool error gives a clear generic fix.
- Do not add a broad planner that invents graph designs from tutorials, examples, or memory.

## Eval And Testing

- Keep deterministic safety tests separate from live/model evals.
- Default development gate is deterministic (`ruff`, `unittest`,
  `tests.retrieval_eval.vector_regression`).
- Retrieval/vector eval gates currently run sequentially when sharing the same
  local index path.
- Live quick tiers run only after runtime/model-facing behavior changes.
- `--n-runs 3` + `release_dashboard` is release-only evidence, not routine.
- Advisor/model bakeoff scripts are research-only and must be run explicitly.
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
- `ask_grc_docs` production-candidate default is deterministic grounded answering with source
  evidence and honest `insufficient_evidence`; helper synthesis is optional
  research-only and not required for the frozen runtime path.
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
