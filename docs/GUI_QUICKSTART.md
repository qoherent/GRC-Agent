# GRC Agent GUI — Quickstart

The GUI is a native PySide6 desktop sidekick panel that runs alongside the
GNU Radio Companion (GRC) editor. It hosts the same four-wrapper agent as
the CLI in a background `QThread`, displays a live inspector of variables,
blocks, and connections, and can compile and run the flowgraph directly
from the panel.

For the terminal chat workflow, see `docs/CLI_QUICKSTART.md`.

## Prerequisites

- Python >= 3.12
- GNU Radio 3.10.x with `grcc` on `PATH` (for Compile & Run)
- `llama-server` (CUDA-enabled for NVIDIA machines; `grc-agent doctor --start-llama` will start it for you)
- A working X11 or Wayland display (or `xvfb-run` on headless systems)

## Install

```bash
uv sync --locked
uv run grc-agent doctor
```

## Launch the GUI

Without a graph (empty session — use "Open in GRC" to point at a file):

```bash
uv run grc-agent-gui
```

With a graph loaded at startup:

```bash
uv run grc-agent-gui playground/grc_agent_interactive/dial_tone_interactive.grc
```

The graph is validated (file exists + native `grcc` passes) before the
window opens. If validation fails, the GUI exits with code 2 and an error
on stderr.

On headless systems:

```bash
xvfb-run uv run grc-agent-gui playground/grc_agent_interactive/dial_tone_interactive.grc
```

## The sidekick pattern

The GUI does not replace GRC. It sits next to it. The recommended loop:

1. Launch the GUI preloaded with a copied `.grc` (see above).
2. Use the **Inspector** (right panel) to browse variables, blocks by
   category, and connections.
3. Send a chat prompt (bottom input) to mutate the graph. The model
   commits verified changes through `change_graph` and the file is
   autosaved to the active copied path.
4. GRC's file-watcher detects the change and prompts you to reload.
   Reload to see the updated flowgraph.
5. To edit the flowgraph visually, click **Open in GRC** to launch
   the native GNU Radio Companion editor against the same path.

## Layout

```
+-----------------------------+--------------------+
| Chat                        | Inspector           |
| (left pane)                 | (right pane)        |
|                             |   - Variables table |
|                             |   - Blocks tree     |
|                             |   - Connections     |
+-----------------------------+--------------------+
| Console: Compile & Run / Stop                     |
| (bottom pane)                                     |
+--------------------------------------------------+
| Status bar                                        |
+--------------------------------------------------+
```

## Compile & Run

The bottom console panel exposes **Compile & Run** and **Stop** buttons.
Compilation runs `grcc` in a child `QProcess`; the compiled Python script
runs in a second `QProcess` with the system environment and working
directory inherited from the original `.grc` parent.

Termination is two-phase (SIGTERM → 2000 ms → SIGKILL) to prevent SDR
hardware locks. If you close the window while a flowgraph is running,
the GUI defers close, stops the process cleanly, and only then exits.

## Closing the window

Closing the window while a flowgraph is running:

- The close is **deferred** — the window stays open.
- A status message appears: "Shutting down running processes and thread workers..."
- The process is sent SIGTERM; if it does not exit within 2 seconds, SIGKILL.
- The worker thread is cancelled and the HTTP client is closed.
- The window then closes cleanly.

This is the hardware-safe shutdown path. It is the same path that runs on
`QApplication.aboutToQuit`.

## Safety

- Graph mutations go through the same `change_graph` wrapper contract as
  the CLI. The model cannot save, load, or edit raw YAML.
- The model runs in a `QThread`; only `Signal`/`Slot` cross the boundary
  to the GUI thread. No direct widget manipulation from the worker.
- Compilation and execution run in child processes with the system
  environment, but are killed and reaped on close.
- The `console_log` widget is capped at 10,000 blocks to prevent
  unbounded memory growth on long-running flowgraphs.

## When to use the GUI

- You want a live, visual view of the active graph as you mutate it.
- You want to compile and run the flowgraph from the same panel
  without dropping to a terminal.
- You want a sidekick that stays out of your way while GRC does the
  visual editing.
- You want to keep the GRC editor open and reload after each mutation.

For a terminal-only, scriptable, single-shot workflow, see
`docs/CLI_QUICKSTART.md`.

## Useful commands

```bash
uv run grc-agent-gui                              # empty session
uv run grc-agent-gui path/to/copy.grc             # preload a graph
xvfb-run uv run grc-agent-gui path/to/copy.grc    # headless display
uv run grc-agent doctor --start-llama             # ensure llama.cpp is up
uv run grc-agent health                           # confirm model reachability
```
