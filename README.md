# GRC Agent

Local GNU Radio `.grc` assistant focused on safe, validated, local-first edits.

## Status

- one `.grc` file per session
- all meaningful graph mutations go through `FlowgraphSession`
- the structural-edit surface is considered stable
- a thin runtime scaffold exists, but no real local model backend is wired yet

## Repo Map

- [src/grc_agent](src/grc_agent): package code, including the session layer and thin runtime wrapper
- [tests](tests): focused `unittest` coverage for `FlowgraphSession`
- [docs/QUICKSTART.md](docs/QUICKSTART.md): verification commands and smoke checks
- [docs/BLUEPRINT.md](docs/BLUEPRINT.md): architecture layers and future phases
- [docs/PROGRESS_RECORDER.md](docs/PROGRESS_RECORDER.md): verified milestones and current backlog
- [docs/decisions/README.md](docs/decisions/README.md): settled decision notes and appendices

## Current Safe Surface

- `FlowgraphSession` supports load, summarize, save, validate, `set_param(...)`, `disconnect(...)`, `connect(...)`, conservative `remove_block(...)`, narrow `add_block(...)` for detached `variable` blocks, `add_and_connect_qtgui_time_sink(...)`, `add_and_connect_char_to_float_to_qtgui_time_sink(...)`, and `add_and_connect_analog_random_source_to_qtgui_time_sink(...)`
- `GrcAgent` is a thin runtime wrapper that exposes the session surface as tools without letting the model touch raw YAML directly
- `grc_agent.cli` remains intentionally small and includes a deterministic `--fake` runtime smoke path

## Verification

Run the current checks with:

```bash
uv run python -m unittest tests.test_flowgraph_session
uv run ruff check
uv run python scripts/check_env.py
```

Optional runtime scaffold smoke test:

```bash
uv run python -m grc_agent.cli --fake tests/data/random_bit_generator.grc
```

## Safety Rules

- the model must never edit raw `.grc` YAML directly
- all graph mutations go through `FlowgraphSession`
- structural APIs only widen when new experiments justify them
- final graph validity is established explicitly through `validate()`

## Next Step

Wire a real local model adapter into the existing runtime/tool surface without bypassing `FlowgraphSession`.
