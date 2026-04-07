# GRC Agent

Local GNU Radio `.grc` assistant.

## Project Structure

```text
.
├── main.py
├── pyproject.toml
├── README.md
├── docs/
│   └── QUICKSTART.md
├── scripts/
│   └── check_env.py
├── AGENTS.md
├── src/
│   └── grc_agent/
│       ├── __init__.py
│       ├── cli.py
│       ├── flowgraph_session.py
│       └── models.py
├── tests/
│   ├── data/
│   │   └── random_bit_generator.grc
│   └── test_flowgraph_session.py
└── workarea/
    └── random_bit_generator.grc
```

## Goal
Build a fully local, CPU-first CLI agent that can read, explain, modify, validate, save, and later create GNU Radio Companion `.grc` flowgraphs.

## Current Scope
v1 focuses on:
- one `.grc` per session
- headless CLI
- safe editing through an internal layer
- explicit validation before save

This pass now covers load, summarize, save, validate, `set_param(...)`, and `disconnect(...)`.

## Testing This Stage

Run the focused unit test with:

```bash
uv run python -m unittest tests.test_flowgraph_session
```

Look for these exact signs of success:

- `Ran 13 tests in ...`
- `OK`
- no traceback or failure lines

This module now checks loading, connection parsing, save round-tripping, validation, the new `set_param(...)` and `disconnect(...)` mutations, persistence after save and reload, and the failure cases for unloaded sessions.

If you want the short version of what the command means, see [docs/QUICKSTART.md](docs/QUICKSTART.md).

Next step: implement `connect()` after `disconnect()`.

If you only want the summary smoke check, run:

```bash
uv run python - <<'PY'
from grc_agent.flowgraph_session import FlowgraphSession

session = FlowgraphSession()
session.load("tests/data/random_bit_generator.grc")
print(session.summarize())
PY
```

That smoke output should include the file name, `Blocks: 5`, `Connections: 3`, and the five block lines.

## Environment
- Ubuntu/Linux
- GNU Radio 3.10.9.2
- Python 3.12.3
- `grcc` available on PATH

## Architecture Direction
- `.grc` file on disk is the source of truth
- the model must not edit raw `.grc` YAML directly
- a thin internal layer will sit between the model and `.grc`
- validation gates save
- safe mutations must update both the parsed model and raw YAML
- wiring changes happen one edit at a time: disconnect first, then connect
- CPU-first local inference
- planned model runtime: llama.cpp server
- planned client side: Python + OpenAI SDK + thin custom AgentRuntime

## Development Order
1. Formalize environment contract
2. Build `FlowgraphSession`
3. Add thin internal models
4. Implement first safe edit path
5. Prove load → summarize → validate → save
6. Add first safe mutation: `set_param`
7. Add wiring mutation: `disconnect`
8. Add agent runtime
9. Expand capabilities
