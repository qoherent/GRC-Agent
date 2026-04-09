## Run the environment check

```bash
uv run python scripts/check_env.py
```

## Expected result

You should see:

* PASS for Python version
* PASS for `grcc` on PATH
* PASS for GNU Radio import/version

If all checks pass, the local development environment is ready.

## Run the focused test

Use this command for the current stage:

```bash
uv run python -m unittest tests.test_flowgraph_session
```

What each part means:

- `uv run` runs the command inside the project environment managed by `uv`.
- `python -m unittest` tells Python to use the built-in unittest runner.
- `tests.test_flowgraph_session` points unittest at the specific test module.

Look for:

- `Ran 47 tests in ...`
- `OK`
- no traceback or `FAIL`

## Run the linter

```bash
uv run ruff check
```

## Run the smoke check

```bash
uv run python - <<'PY'
from grc_agent.flowgraph_session import FlowgraphSession

session = FlowgraphSession()
session.load("tests/data/random_bit_generator.grc")
print(session.summarize())
PY
```

The output should show the file name, `Blocks: 5`, `Connections: 3`, and the five block entries.

## Run the runtime scaffold smoke check

```bash
uv run python -m grc_agent.cli --fake tests/data/random_bit_generator.grc
```

This verifies that the thin runtime layer routes model-like tool calls through `FlowgraphSession` rather than touching raw YAML directly.

## What save and validate do

- `save()` writes the current parsed `.grc` data back to a file.
- `validate()` writes the parsed `.grc` data to a temporary file and asks `grcc` to compile it.
- `validate()` also treats GNU Radio error markers in stdout/stderr as failure, even if `grcc` exits with status `0`.
- `set_param(...)` updates both the parsed model and the raw YAML, so the next save/validate sees the mutation.
- `disconnect(...)` removes one wire from both the parsed model and the raw YAML.
- `connect(...)` adds one wire to both the parsed model and the raw YAML.
- `remove_block(...)` removes only detached, unreferenced blocks from both the parsed model and the raw YAML.
- `add_block(...)` currently supports detached `variable` blocks only and validates a candidate graph before committing the mutation.
- `add_and_connect_qtgui_time_sink(...)` currently supports one copied `qtgui_time_sink_x` block plus its required input connection, and validates a candidate graph before committing the mutation.
- `add_and_connect_char_to_float_to_qtgui_time_sink(...)` currently supports one copied `blocks_char_to_float` block plus a coordinated `qtgui_time_sink_x.nconnections` increase and both required wires, and validates the full candidate graph before committing the mutation.
- `add_and_connect_analog_random_source_to_qtgui_time_sink(...)` currently supports one copied `analog_random_source_x` block plus one copied `blocks_char_to_float` block, a coordinated `qtgui_time_sink_x.nconnections` increase, and both required wires, and validates the full candidate graph before committing the mutation.
- The persistence tests prove mutate -> save -> reload keeps the new value or removed connection on disk.
- Save, validate, and mutation all use the same in-memory raw YAML as the source of truth.

## Next step

The structural-edit surface is now stable, and the first thin runtime scaffold is already in place. The next step is wiring a real local model adapter into that runtime without bypassing `FlowgraphSession`.
