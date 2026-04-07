# Project Rules

- Use `uv run` for commands and `uv add` / `uv add --dev` for dependencies.
- Keep the real package under `src/grc_agent/` and use package imports.
- Keep `.python-version` pinned to `3.12`.
- Keep `pyproject.toml` authoritative for project metadata and dependencies.
- Keep tests focused and use stdlib `unittest` for now.
- Comment scripts and study files concisely when they help explain the flow.
- Update README and docs when the workflow or verification command changes.
- Do not add save, validate, or mutation behavior unless the pass explicitly asks for it.