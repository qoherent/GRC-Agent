# Project Rules

- Use `uv run` for commands and `uv add` / `uv add --dev` for dependencies.
- Keep the real package under `src/grc_agent/` and use package imports.
- Keep `pyproject.toml` authoritative for project metadata and dependencies.
- Keep tests focused and use stdlib `unittest` for now.
- Comment scripts and study files concisely when they help explain the flow.
- Update README and docs when the workflow or verification command changes.
- Do not add save, validate, or mutation behavior unless the pass explicitly asks for it.
- You must must always ask for more details when needed and you must not make assumptions.

- You Must always reject any ad-hoc logic or flow that leads to redundancy, extra cost, latency, or worse performance.

- You must always be bold, objective and grounded.

- You must always lean in decisions towards simplifying not complicating.

- You must always keep your replies concise, free of fluff.

- You must always test and verify any assumptions or bespkoke recommendations you propose on real GRC test cases (Not mock) and valiate this is the correct pattern expected by the GNU system.

- Before changing any GNU-facing behavior, you must first read the relevant GNU Radio documentation and then reproduce the behavior on a real `.grc` case with `grcc`; never infer GNU semantics from YAML shape alone.

- Any new GNU behavior, edge case, or widened API must be recorded in `docs/BLUEPRINT.md` with the smallest real validation evidence before it becomes part of the supported contract.

- Prefer the smallest passing change, the smallest passing experiment, and the smallest passing CI gate. Do not widen APIs or automation until the narrower shape is proven insufficient.
