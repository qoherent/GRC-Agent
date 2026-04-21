# Project Rules

## Tooling

- Use `uv run` for commands and `uv add` / `uv add --dev` for dependencies.
- Keep the real package under `src/grc_agent/` and use package imports.
- Keep `pyproject.toml` authoritative for project metadata and dependencies.
- Keep tests focused and use stdlib `unittest` for now.
- `uv run ruff check` is the lint gate. `uv run python -m unittest` is the regression gate.
- The canonical example fixture is `tests/data/random_bit_generator.grc`. Do not keep duplicate copies at repo root.

## Decision principles

- Do not add save, validate, or mutation behavior unless the pass explicitly asks for it.
- Ask for more details when needed. Do not make assumptions.
- Reject any ad-hoc logic or flow that leads to redundancy, extra cost, latency, or worse performance.
- Lean towards simplifying, not complicating.
- Keep replies concise, free of fluff.
- Prefer the smallest passing change, the smallest passing experiment, and the smallest passing CI gate.

## GNU Radio contract

- Test and verify any assumptions on real GRC test cases (not mock) with `grcc`.
- Before changing any GNU-facing behavior, read the relevant GNU Radio docs and reproduce on a real `.grc` case.
- Any new GNU behavior, edge case, or widened API must be recorded in `docs/BLUEPRINT.md` with real validation evidence.

## Eval harness

- The llama.cpp eval runners auto-start the server via `LlamaServerLauncher`.
- Use targeted phase/case runs while iterating; keep the slow full sweep for the end.
- Run `uv run python -m tests.llama_eval.run_phase1` (same for phase2, phase3) — no manual server start needed.
- Run `uv run python -m tests.llama_eval.run_all` only after fixes are in and the focused checks are green.
- Tool order in `get_tool_schemas()` matters: models prefer earlier tools. `apply_edit` must appear before `propose_edit`.
- After changing system prompt, tool schemas, or loop reminders, re-run the eval suite and record results in `docs/LLAMA_EVAL.md`.
- Update `docs/BLUEPRINT.md` when the runtime contract or harness changes.
