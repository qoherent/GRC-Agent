## Activate the virtual environment
```bash
source .venv/bin/activate
````

## Run the environment check

```bash
python scripts/check_env.py
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

* `uv run` runs the command inside the project environment managed by `uv`.
* `python -m unittest` tells Python to use the built-in unittest runner.
* `tests.test_flowgraph_session` points unittest at the specific test module.

Look for:

* `Ran 13 tests in ...`
* `OK`
* no traceback or `FAIL`

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

## What save and validate do

* `save()` writes the current parsed `.grc` data back to a file.
* `validate()` writes the parsed `.grc` data to a temporary file and asks `grcc` to compile it.
* `set_param(...)` updates both the parsed model and the raw YAML, so the next save/validate sees the mutation.
* `disconnect(...)` removes one wire from both the parsed model and the raw YAML.
* The persistence tests prove mutate → save → reload keeps the new value or removed connection on disk.
* Save, validate, and mutation all use the same in-memory raw YAML as the source of truth.

## Next step

Implement `connect()` after `disconnect()`.
