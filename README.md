# GRC Agent

Local GNU Radio `.grc` assistant focused on safe, validated, local-first edits.

## Status

- one `.grc` file per session
- `FlowgraphSession` owns parsed state, persistence, validation, and all graph-mutation primitives
- `GrcAgent` intentionally exposes a smaller model-facing runtime than the full session surface
- the structural-edit surface is frozen pending new experiments
- no real local model adapter is wired yet

## Repo Map

- [src/grc_agent](src/grc_agent): session layer, model-facing runtime wrapper, and CLI entrypoint
- [tests](tests): focused `unittest` regression coverage
- [tests/data/random_bit_generator.grc](tests/data/random_bit_generator.grc): canonical fixture flowgraph
- [docs/BLUEPRINT.md](docs/BLUEPRINT.md): architecture, settled decisions, evidence, milestones, and backlog

## Model-Facing Runtime

The model-facing runtime is intentionally narrower than the session layer.

- `summarize_graph`: report the current graph shape
- `set_variable(instance_name, value)`: update only a `variable` block's `value` parameter through `FlowgraphSession`
- `validate_graph`: run `grcc` validation on the current in-memory graph
- `save_graph(path=None)`: persist the current graph, but only after the latest dirty state has passed validation

The broader `FlowgraphSession` mutation methods remain available for direct code paths and regression tests, but they are not part of the model tool contract.

## Verification

Use the package entrypoint directly:

```bash
uv run python scripts/check_env.py
uv run ruff check
uv run python -m unittest
uv run python -m grc_agent.cli --fake tests/data/random_bit_generator.grc
```

What to expect:

- `check_env.py` passes Python, `grcc`, and GNU Radio version checks
- `ruff check` is clean
- `python -m unittest` passes the current regression suite
- the `--fake` CLI path routes a deterministic tool sequence through `GrcAgent` and `FlowgraphSession`

## Safety Rules

- the model never edits raw `.grc` YAML directly
- all meaningful mutations flow through `FlowgraphSession`
- the runtime save path is blocked until the current dirty state has passed validation
- structural APIs only widen when a new experiment pass justifies them
- final graph validity is established explicitly through `validate()`

See [docs/BLUEPRINT.md](docs/BLUEPRINT.md) for the settled structural boundary, runtime decision, condensed experiment evidence, milestones, and backlog.
