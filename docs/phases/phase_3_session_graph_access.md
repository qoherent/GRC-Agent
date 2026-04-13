# Phase 3: Session Graph Access and Bounded Graph Context

> **For Hermes:** Use subagent-driven-development for implementation. Read Phase 0, Phase 1, and Phase 2 first. Keep this phase read-oriented.
>
> This is a suggested plan, not a fixed route; the implementer can pivot if the live session model or repo state reveals a better ordering.

**Goal:** Give the agent a safe way to inspect the currently loaded `.grc` session without dumping the entire graph into context.

**Architecture:**
This phase owns the live-session view: load the `.grc`, summarize the graph, and expand small neighborhoods around selected nodes. It should make graph reasoning compact, reproducible, and bounded.
The public output should be a compact structured payload with a bounded mini-graph nested inside it. Do not return a full graph dump.

**Tech Stack:**
- Python 3.12+
- current `.grc` session model
- graph retrieval index
- `unittest`

## Phase 1 baseline to reuse

- `src/grc_agent/retrieval/` already includes session-scope search and a bounded session index.
- The CLI startup path already runs retrieval readiness checks and binds the active session context for `search_grc(..., scope="session")`.
- This phase should add richer bounded session inspection and mini-graph context, not a second ad-hoc session search implementation.
- If neighborhood expansion needs to share traversal or node-shaping logic with the retrieval package, extract shared helpers rather than duplicating session graph walks.

---

## Package boundary

Create a session access package:

```text
src/grc_agent/session/
  __init__.py
  load.py
  summary.py
  context.py
  inspect.py
  provenance.py
```

Suggested tests:

```text
tests/session/
  test_load_grc.py
  test_summarize_graph.py
  test_get_grc_context.py
```

## Feature definition

The session layer should support:
- loading the active `.grc`
- summarizing the graph state
- returning bounded context neighborhoods
- reporting dirty / validated state
- exposing node-centric session facts without full dumps

Session nodes in this phase mean live block instances in the loaded `.grc`, keyed by `instance_name`. The top-level flowgraph/options record is provenance for the session, not a peer node.

Suggested tools:
- `load_grc(file_path)`
- `summarize_graph()`
- `get_grc_context(node_id, hops=1, max_nodes=20)`

## Tests

Create tests that prove:
- a real fixture loads successfully
- summary output is stable and compact
- context expansion returns a bounded neighborhood
- context expansion includes provenance and connection facts
- invalid node ids fail clearly
- large graphs still return bounded slices
- read operations do not mutate session state

Suggested summary shape:
- `ok`
- `summary`
- `path`
- `graph_id`
- `block_count`
- `connection_count`
- `variable_count`
- `dirty`
- `validation`

Suggested context shape:
- `ok`
- `node_id`
- `hops`
- `max_nodes`
- `target`
- `nodes`
- `edges`
- `provenance`
- `dirty`
- `validation`
- `truncated`

If helpful, expression references may be represented as bounded facts attached to nodes, but they should not force the API into a full semantic graph model.

## Strict pass criteria

This phase is done only when all are true:
- loading and summarization are stable on real `.grc` fixtures
- context slices are bounded and deterministic
- the output is useful for reasoning but not a full graph dump
- the package stays read-oriented
- unknown node ids return a stable error shape
- provenance includes path, graph_id, file_format, and grc_version
- dirty/validation state is represented explicitly

## Coding rules for implementers

- Read Phase 0–2 first.
- Do not add mutation APIs here.
- Do not widen the session surface beyond inspection.
- Avoid redundant graph serialization or duplicate summaries.
- Keep outputs small and structured.
- Validate behavior on a real `.grc` fixture.
- Keep node context bounded and mini-graph shaped.
- Do not make expression references a heavy first-class graph model unless the fixture proves it is necessary.

## Refactors / removals

- Keep `flowgraph_session.py` as the session owner, but stop adding new read-only inspection code there once this package exists.
- If there is any future full-graph dump behavior, replace it with bounded context slices.
- Keep session inspection separate from mutation policy.

## Suggested error shape

For unknown node ids, use a stable error payload rather than fuzzy fallback:
- `ok: false`
- `error_type: node_not_found`
- `message`
- `details` with the unknown `node_id`

## Suggested state shape

Represent dirty and validation state as a compact object:
- `dirty`: bool
- `validation`:
  - `status`: `unknown | valid | invalid`
  - `returncode`
  - `stdout` when available
  - `stderr` when available

## Deliverables

- session access package
- load, summary, and context APIs
- bounded context tests
- docs describing the session inspection contract

## Tool I/O contract

### `load_grc(file_path)`
- input: required file path string
- output: session loaded state, graph summary, loaded path, success boolean, and any diagnostics

### `summarize_graph()`
- input: none
- output: compact summary of blocks, connections, variables, dirty state, and validation state

### `get_grc_context(node_id, hops=1, max_nodes=20)`
- input fields:
  - `node_id`: required string
  - `hops`: optional integer, default 1
  - `max_nodes`: optional integer, default 20
- output fields:
  - `ok`
  - target node summary
  - bounded neighborhood nodes
  - bounded neighborhood edges
  - provenance and connection facts
  - optional truncation indicator

## Implementation checklist

- define what counts as a session node
- define the maximum neighborhood size and truncation behavior
- define how summaries differ from context expansion
- decide what provenance fields are mandatory
- decide how dirty/validated state is reported
- decide how missing node ids fail
- update all relevant docs

## Strict pass criteria

- session loading works on the canonical fixture
- summaries are compact and deterministic
- context expansion is bounded
- read-only operations do not mutate state
- output is useful without being large

## Refactor / remove checklist

- stop adding new read-only inspection logic to `flowgraph_session.py`
- replace any future full-graph dumps with bounded context tools
- keep session inspection separate from mutation helpers
