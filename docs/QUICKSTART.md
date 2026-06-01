# Quickstart

GRC Agent ships as two distinct products that share the same underlying
runtime. Pick the one that matches your workflow.

## CLI — terminal chat

Terminal-first, scriptable, single-session per file. Best for: scripted
runs, JSON one-shot execution, manual `/save` workflow, staying in the
chat/mutation plane.

See **[`docs/CLI_QUICKSTART.md`](CLI_QUICKSTART.md)**.

```bash
uv run grc-agent chat playground/grc_agent_interactive/dial_tone_interactive.grc
```

## GUI — PySide6 sidekick panel

Native desktop app that runs alongside GNU Radio Companion. Inspects
variables, blocks, and connections; compiles and runs the flowgraph
directly. Best for: visual graph state, Compile & Run from the panel,
hardware-safe shutdown.

See **[`docs/GUI_QUICKSTART.md`](GUI_QUICKSTART.md)**.

```bash
uv run grc-agent-gui playground/grc_agent_interactive/dial_tone_interactive.grc
```

## Install (both products)

```bash
uv sync --locked
uv run grc-agent doctor
```

Prerequisites: Python >= 3.12, GNU Radio 3.10.x with `grcc` on `PATH`,
and a reachable local `llama-server` for model-backed chat.

## See also

- `docs/BLUEPRINT.md` — design contract, safety guarantees, runtime status
- `docs/PYSIDE6_GUI_BLUEPRINT.md` — GUI architecture and validation report
- `docs/MODEL_CONTEXT_BIBLE.md` — model-facing prompt and wrapper schemas
