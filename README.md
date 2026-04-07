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

This pass implements the first usable load and summarize path and keeps save, validate, and mutation out of scope for now.

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
- CPU-first local inference
- planned model runtime: llama.cpp server
- planned client side: Python + OpenAI SDK + thin custom AgentRuntime

## Development Order
1. Formalize environment contract
2. Build `FlowgraphSession`
3. Add thin internal models
4. Implement first safe edit path
5. Prove load → summarize → validate → save
6. Add agent runtime
7. Expand capabilities
