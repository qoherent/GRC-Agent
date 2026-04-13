# Phase 1: GRC Retrieval Index and `search_grc`

> **For Hermes:** Use subagent-driven-development for implementation. Read Phase 0 first, then read the current repo docs and inspect the real GRC data model. Treat this as an isolated package build.
>
> This is a suggested plan, not a fixed script: the implementer may pivot based on discoveries in the repo, GNU Radio metadata, or graphify behavior as long as the core safety and retrieval goals remain intact.

**Goal:** Build the retrieval layer that lets the agent search GRC catalog and session content without flooding context.

**Architecture:**
This phase introduces a graph-backed retrieval package. graphify is allowed as the indexing/retrieval substrate, but the retrieval output must remain compact, deterministic, and provenance-aware. This phase does not mutate graphs.

The current direction is that this retrieval block should be available as part of app startup, or at least be initialized immediately on app start with light checks and cache/load preparation. That startup path should stay bounded and reliable: verify the environment, confirm the GRC metadata sources, warm any needed caches, and fail clearly if the retrieval substrate is not ready. Heavier work may still be deferred if the implementer finds a better startup boundary.

## Implementation status

Phase 1 is implemented in the current repo state.

What landed:
- `src/grc_agent/retrieval/{__init__,index,search,graphify_adapter,schema,provenance}.py`
- `tests/retrieval/{test_index_build,test_search_grc,test_retrieval_bounding,test_search_quality}.py`
- public search contract: `search_grc(query, scope="catalog|session", k=5)`
- bounded startup seam: `initialize_retrieval(catalog_root=None, warm_catalog=False)`
- real CLI startup wiring: the CLI now runs the bounded retrieval readiness check and binds the active session context before runtime flow continues
- catalog corpus boundary: system GNU metadata roots only, preferring `/usr/share/gnuradio/grc/blocks` and then `/usr/local/share/gnuradio/grc/blocks`
- readiness rule: the selected catalog root must contain `.block.yml`, `.tree.yml`, and `.domain.yml` metadata or retrieval fails clearly
- graphify usage: `graphifyy==0.4.11` through `build_from_json()` only; ranking and result shaping stay local and deterministic
- follow-up tuning: the searchable index is block-centric by default, result payloads are compacted to a short `summary`, and normalized token indexes are precomputed during index build

## Context to read first

Before coding, the implementer should read:
- `docs/BLUEPRINT.md`
- `docs/PACKAGE_GUIDE.md`
- `README.md`
- `docs/phases/phase_0_context_and_rules.md`
- `/home/mahmoud/Desktop/GRC_Agent/Temp/graphify/README.md`

The temp graphify checkout is reference-only. Use its README to understand graph construction, query extraction, and always-on assistant hooks, but do not depend on the cloned repo as an implementation source. Install graphify into this project’s `.venv` using the upstream README flow and the local project environment.

## Environment rules

This repository should be worked in through the local project environment, not the global interpreter.

Preferred rules:
- create or reuse the project `.venv`
- run Python via `uv run ...` when possible
- prefer `uv sync` or an equivalent project-managed dependency flow over ad hoc global installs
- do not assume `python` resolves correctly outside the virtual environment
- do not assume system packages are available unless the repo or docs prove it
- if a tool depends on GNU Radio binaries, verify them explicitly rather than guessing
- keep install and execution steps reproducible in the repo docs

A practical local setup usually looks like this:
```bash
uv sync
uv run python -m unittest
```

If graphify needs to be installed locally for experimentation, install it into the project environment, not into the cloned temp repo:
```bash
uv add graphifyy==0.4.11
```

The current implementation uses graphify's Python API for graph construction only; the assistant-hook / `graphify install` CLI path is out of scope for this repo.

**Tech Stack:**
- Python 3.12+
- graphify
- GNU Radio metadata
- `.grc` session data
- `unittest`
- `uv run`
- project `.venv`
- local GNU Radio / `grcc` tooling

---

## Package boundary

Create an isolated retrieval package:

```text
src/grc_agent/retrieval/
  __init__.py
  index.py
  search.py
  graphify_adapter.py
  schema.py
  provenance.py
```

Suggested tests:

```text
tests/retrieval/
  test_index_build.py
  test_search_grc.py
  test_retrieval_bounding.py
  test_search_quality.py
```

The package should own only retrieval concerns:
- graph build/load
- query normalization
- search
- result ranking
- bounded slices
- provenance fields

## Suggested implementation sequence

Treat this as a guided path, not a rigid checklist. If a subagent discovers a better order, it can pivot as long as the retrieval contract stays safe and bounded.

1. Inspect the live GNU Radio metadata layout and identify which files are actually useful for search (`.block.yml`, `.tree.yml`, `.domain.yml`, and any related docs or session artifacts).
2. Try graphify against a small representative corpus so the implementer understands the shape of its output, its install flow, and what counts as a useful retrieval slice.
3. Decide the retrieval entities that matter most for the agent: blocks, block descriptions, ports, fields/parameters, nearby categories, and session nodes.
4. Define the compact result schema before writing the index code.
5. Build the initial catalog/session index path.
6. Add startup initialization or warmup hooks only after the index behavior is understood.
7. Add tests that prove the search results are useful, bounded, and deterministic.

## Feature definition

Main API:
- `search_grc(query, scope="catalog|session", k=5)`

Startup seam:
- `initialize_retrieval(catalog_root=None, warm_catalog=False)`

Scope meaning:
- `catalog`: search block/catalog graph content
- `session`: search the active `.grc` graph through the active session context bound during startup
- later implementations may support a hybrid mode if explicitly needed, but not in Phase 1

Search behavior should be compact but still informative enough to help the agent orient itself around blocks, not just node IDs. The current direction is block-centric results with a short reason and one compact summary field rather than a wide multi-field payload for every hit.

## Output shape

Each result should include at least:
- node id
- node type
- display label
- short reason / match explanation
- provenance pointer
- confidence / rank score
- source scope
- optional compact summary

## Tests

Create tests that prove:
- known catalog queries return expected results
- known session queries return expected results
- output is bounded and does not dump the whole graph
- provenance is included
- scope selection changes the result set correctly
- empty or missing queries fail clearly
- non-matching queries return a stable empty or low-signal shape
- repeated queries are deterministic

Use real GNU Radio metadata and the canonical `.grc` fixture where possible.

## Strict success criteria

This phase is done only when all are true:
- `search_grc` exists as a stable function/tool
- it returns structured, bounded results
- it works on real GRC content
- it distinguishes catalog vs session scope
- it does not mutate any state
- it has tests for the important happy and unhappy paths
- the app-startup retrieval path can initialize or verify readiness without blocking on unbounded work

## Coding rules for implementers

- Read Phase 0 first.
- Read the repo state before writing code.
- Do not add mutation logic here.
- Do not duplicate catalog parsing in multiple places.
- Prefer a deterministic base search before any semantic reranking.
- Keep ranking explainable.
- Keep outputs short enough for agent use.
- Validate on real GRC data, not mock-only cases.

## Refactors / removals

- Do not keep ad-hoc search logic in `agent.py` or `cli.py` once this package exists.
- If a temporary search prototype exists elsewhere, move it into this package and delete the duplicate path.
- Keep retrieval concerns out of `flowgraph_session.py`.

## Deliverables

- retrieval package
- indexed graph loading path
- `search_grc` API
- tests and fixtures
- minimal docs update describing how to call the tool

## Tool I/O contract

### `search_grc(query, scope="catalog|session", k=5)`

Input fields:
- `query`: required string
- `scope`: required string, one of `catalog`, `session`
- `k`: optional integer, default 5, maximum should be explicit and documented by implementation

Output fields:
- `ok`: boolean
- `scope`: catalog or session
- `query`: echoed normalized query
- `results`: list of bounded matches
- `results[*].node_id`
- `results[*].node_type`
- `results[*].label`
- `results[*].reason`
- `results[*].provenance`
- `results[*].score`
- `results[*].source_scope`
- optional `results[*].summary`
- optional warnings or empty-result hints

Error shape:
- `ok: false`
- `error_type`
- `message`
- optional `details`

### `initialize_retrieval(catalog_root=None, warm_catalog=False)`

Input fields:
- `catalog_root`: optional explicit catalog root override
- `warm_catalog`: optional boolean; when true, warm the cached catalog index after readiness checks pass

Output fields:
- `ok`
- `message`
- `graphify_version`
- `catalog_root`
- `catalog_files.block`
- `catalog_files.tree`
- `catalog_files.domain`
- `catalog_index_warmed`
- optional warmed index node/edge counts

Error shape:
- `ok: false`
- `error_type`
- `message`
- optional `details`

## Implementation checklist

- define the retrieval graph boundary before coding
- define how catalog nodes differ from session nodes
- decide how provenance is attached to each result
- decide the maximum context size per result
- decide deterministic ranking behavior before semantic reranking
- decide how empty queries fail
- decide how unsupported scopes fail
- update all relevant docs

## Strict pass criteria

- results are bounded by `k`
- no result dumps the whole graph
- catalog and session scopes are distinguishable
- provenance is always present when available
- identical inputs produce stable outputs
- test coverage includes positive and negative cases

## Refactor / remove checklist

- remove any search logic from `agent.py` once this package exists
- remove any duplicate search path from `cli.py`
- remove any ad-hoc graph traversal that bypasses retrieval packaging
