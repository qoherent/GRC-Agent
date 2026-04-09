# Project Blueprint

## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs.
The project exists to inspect, explain, modify, validate, and eventually drive
safe graph changes without letting the model edit raw YAML directly.

## Current Product Shape

- one `.grc` file per session
- headless CLI
- local validation through `grcc`
- safe mutations owned by `FlowgraphSession`
- structural-edit surface treated as stable unless new experiments justify a change
- thin runtime scaffold present, but no real local model adapter wired yet

## Architecture Layers

### 1. Raw `.grc` on disk

- YAML on disk remains the persistence format.
- The on-disk file is still the source of truth for save and validation.

### 2. Session / thin IR layer

- `src/grc_agent/models.py` holds the lightweight parsed dataclasses.
- `src/grc_agent/flowgraph_session.py` owns load, summarize, save, validate,
	and all safe mutations.
- Mutations must keep the parsed model and the raw YAML in sync.

### 3. Safe tool surface

- Higher layers should rely on the existing `FlowgraphSession` methods rather
	than inventing a second mutation API.
- The current surface includes:
	- `summarize()`
	- `validate()`
	- `save()`
	- `set_param(...)`
	- `disconnect(...)`
	- `connect(...)`
	- `remove_block(...)`
	- `add_block(...)`
	- bespoke add-and-connect helpers for the tested sink, transform, and source workflows

### 4. Runtime / agent layer

- `src/grc_agent/agent.py` wraps the session surface as callable tools.
- Runtime state stays minimal: one session, one turn history, one tool registry.
- The CLI `--fake` path exists to verify tool routing empirically without a real model backend.

### 5. Future local-model adapter

- A real local model backend should call the runtime layer, not `FlowgraphSession` directly.
- Prompt construction, tool schemas, and stop conditions should stay thin and local-first.
- Avoid orchestration frameworks unless the current direct tool-calling shape proves inadequate.

## Current Implemented Scope

- Load and summarize an existing `.grc` flowgraph.
- Save and validate the current in-memory graph.
- Mutate existing parameters and connections safely.
- Perform a narrow set of structural edits justified by experiments.
- Exercise a thin runtime wrapper using a deterministic fake-model flow.

## Structural-Edit Boundary

These rules are currently settled:

- The model never edits raw `.grc` YAML directly.
- `remove_block(...)` is conservative: detached and unreferenced blocks only.
- `connect(...)` and `disconnect(...)` are permissive staged edits; callers validate the final graph explicitly.
- `add_block(...)` remains limited to detached `variable` blocks.
- Broader fresh-sink and throttle-inclusive source workflows stay unsupported.
- Structural APIs should only widen when a new experiment pass shows a narrower or clearly necessary contract.

See [decisions/structural_edits.md](./decisions/structural_edits.md) for the current summary and linked appendices.

## Runtime Direction

- Keep the runtime thin and tool-driven.
- Keep the CLI as a lightweight entry point that creates one session and invokes the runtime.
- Keep conversation state simple until a real multi-turn limitation appears.
- Keep save explicit rather than auto-saving behind the model's back.

## Future Phases

1. Wire a real local model adapter into the existing `GrcAgent` tool surface.
2. Formalize tool schemas and prompt/context construction for the runtime.
3. Add a one-session interactive CLI conversation loop over the current runtime.
4. Improve user-facing summaries, validation reporting, and error surfacing.
5. Revisit structural API growth only if new use cases are backed by experiments.
6. Consider broader creation workflows only after the runtime path is proven useful.

## Risks And Open Questions

- Local model backend choice still needs to be made without introducing framework creep.
- Prompt size and graph-summary shape may need tuning once a real model is in the loop.
- Tool schemas should stay explicit enough that a local model does not drift into unsupported actions.
- Save semantics should remain explicit even if future interactive flows become more capable.

## Intentionally Deferred

- direct raw YAML edits by the model
- automatic connected-block removal
- immediate validation on every `connect()` or `disconnect()`
- generic multi-block structural builders inferred from the current bespoke helpers
- heavy orchestration frameworks
- multi-flowgraph session management

## Related Docs

- [README.md](../README.md)
- [PROGRESS_RECORDER.md](./PROGRESS_RECORDER.md)
- [QUICKSTART.md](./QUICKSTART.md)
- [decisions/README.md](./decisions/README.md)
