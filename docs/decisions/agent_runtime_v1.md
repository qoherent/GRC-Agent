# Thin Agent Runtime V1

## Decision

Use a thin local runtime wrapper over `FlowgraphSession` instead of wiring the CLI directly to a model client or adopting a framework.

## Boundary

- `GrcAgent` owns a minimal turn history and tool registry.
- The runtime exposes only session-backed tools.
- The model never edits raw `.grc` YAML directly.
- The CLI creates the session and invokes the runtime.
- The `--fake` CLI path exists only for deterministic runtime verification.

## Why This Stays Thin

- The current bottleneck is contract clarity, not orchestration capability.
- A framework would hide too much logic while the tool surface is still intentionally narrow.
- One flowgraph and one session are enough for the current verified scope.
- Keeping the runtime backend-agnostic preserves flexibility for a future local model choice.

## Consequences

- Future model adapters should call the runtime layer, not `FlowgraphSession` internals directly.
- Prompt and tool-schema work can stay small and explicit.
- Save remains an explicit action rather than an automatic side effect.

## Next Step

- wire a real local model adapter into `GrcAgent`
- formalize tool schemas for the current registry
- keep all meaningful mutations flowing through `FlowgraphSession`
