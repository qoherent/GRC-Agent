# Progress Recorder

Use this doc to track verified milestones and the current backlog.
Keep design detail in the decision notes and future-planning detail in [BLUEPRINT.md](./BLUEPRINT.md).

## Current Verified State

- `uv run python -m unittest tests.test_flowgraph_session` passes with 47 tests.
- `uv run ruff check` passes.
- `uv run python scripts/check_env.py` passes.
- The current safe surface supports load, summarize, save, validate, `set_param(...)`, `disconnect(...)`, `connect(...)`, conservative `remove_block(...)`, narrow `add_block(...)`, and the three bespoke add-and-connect helpers.
- `validate()` records diagnostics and treats GNU Radio error markers as failure even when `grcc` exits with status `0`.
- The structural-edit surface is considered stable.
- A thin runtime wrapper exists and routes tool calls through `FlowgraphSession`.

## Milestones

### Phases 0-3 Foundation

- Normalized the package under `src/grc_agent/`.
- Added the fixture flowgraph and the first `FlowgraphSession` load, summarize, save, and validate coverage.
- Standardized the repo on `uv run ...` commands and the current environment checks.

### Phases 4-6 Core Edits And Validation

- Implemented `set_param(...)`, `disconnect(...)`, and `connect(...)`.
- Proved persistence through mutate -> save -> reload tests.
- Added stored validation diagnostics for the most recent `grcc` run.

### Phases 7-12 Structural-Edit Implementation

- Added conservative `remove_block(...)` for detached, unreferenced blocks.
- Added narrow `add_block(...)` support for detached `variable` blocks.
- Added three bespoke structural workflows for the tested sink, transform, and source expansion paths.
- Kept all structural adds on the copy-validate-commit pattern.

### Phases 13-14 Structural Boundary Stabilization

- Resolved the remaining policy questions empirically.
- Broader fresh-sink and throttle-inclusive source workflows remain unsupported.
- Automatic connected-block removal remains unsupported.
- `connect(...)` and `disconnect(...)` remain permissive staged edits that rely on explicit final validation.

### Phase 15 Thin Runtime Pass

- Added `GrcAgent` as a thin tool registry over `FlowgraphSession`.
- Added a deterministic CLI `--fake` path to verify runtime routing without a real model backend.
- Kept the runtime boundary local, minimal, and framework-free.

### Phase 16 Repository Cleanup And Blueprint Normalization

- Shortened the README to overview, status, verification, and links.
- Split the structural decision material into a summary note plus focused appendices.
- Added a blueprint doc so architecture and future phases live outside the progress log.
- Added a decisions index to make the doc set easier to navigate.

## Current Backlog

- wire a real local model adapter into `GrcAgent`
- formalize tool schemas and prompt/context construction for the runtime
- add a one-session interactive CLI conversation loop
- improve user-facing summaries and validation reporting

## Explicitly Deferred

- direct raw `.grc` YAML editing by the model
- automatic connected-block removal
- immediate validation on every `connect()` or `disconnect()`
- broader structural workflows without new experiments
- heavy orchestration frameworks
