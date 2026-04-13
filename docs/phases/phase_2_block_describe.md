# Phase 2: Block Description and Structured Block Truth

> **For Hermes:** Use subagent-driven-development for implementation. Read Phase 0 and Phase 1 first. This phase must stay read-only.
>
> This is a suggested plan, not a rigid sequence; the implementer may adjust the path if the real metadata or repo state points to a better approach.

**Goal:** Build `describe_block(block_id)` so the agent can ask the catalog what a block is, how it behaves, and how it should be instantiated.

**Architecture:**
This phase sits on top of the retrieval/index layer. It returns normalized block truth extracted from the installed GNU Radio metadata. It must be the canonical description path for block identity, parameters, ports, asserts, docs, and caveats.
The public output should stay tight and structured: no prose summary, no raw codegen templates, and no loader-only metadata unless it is clearly useful to the agent.

**Tech Stack:**
- Python 3.12+
- GNU Radio metadata
- graphify-backed retrieval
- `unittest`

## Phase 1 baseline to reuse

- `src/grc_agent/retrieval/` already exists and is the current Phase 1 baseline.
- Reuse the existing GNU catalog root discovery and retrieval package behavior instead of adding a second catalog-discovery path.
- If this phase needs raw catalog loaders or normalized shared records that Phase 1 does not expose yet, extract a shared seam instead of duplicating YAML traversal in parallel packages.
- `search_grc(query, scope="catalog|session", k=5)` and `initialize_retrieval(...)` are already live and should be treated as the stable retrieval/search boundary below this phase.

---

## Package boundary

Create an isolated catalog package:

```text
src/grc_agent/catalog/
  __init__.py
  describe.py
  normalize.py
  schema.py
  loaders.py
  errors.py
```

Suggested tests:

```text
tests/catalog/
  test_describe_block.py
  test_catalog_normalize.py
  test_catalog_errors.py
```

## Feature definition

`describe_block(block_id)` must return:
- block id
- label
- category path
- flags
- loaded-from path
- normalized parameters
- normalized ports
- asserts
- doc_url pointer
- warnings / caveats
- a compact instantiation signature or skeleton

## Tests

Create tests that prove:
- known blocks return complete structured descriptions
- ports and parameters are present and normalized
- docs pointers are preserved
- asserts are included
- hier blocks are clearly marked, not excluded
- unknown block ids return a stable error shape
- output is concise, structured, and machine-readable

## Strict success criteria

This phase is done only when all are true:
- `describe_block` exists and is stable
- it uses local catalog data, not free-form prose
- it returns enough detail to choose or instantiate a block safely
- it has tests on real GRC block metadata
- it does not mutate the graph

## Coding rules for implementers

- Read Phase 0 and Phase 1 first.
- Do not invent schema fields from memory.
- Do not guess port compatibility.
- Do not use graphify as the truth layer; it is retrieval support only.
- Keep the output shape stable and structured.
- Prefer exact metadata extraction over explanatory prose.
- Keep hierarchy handling lightweight; mark it, do not over-model it.
- Do not expose raw templates or codegen bodies in the first public contract.

## Refactors / removals

- Remove any future prompt-only block schema assumptions once this phase lands.
- Remove duplicated block-description logic from `agent.py` or `cli.py` if it appears.
- Keep block normalization in this package rather than scattered across callers.

## Deliverables

- describe_block API
- normalized block schema records
- tests for known and unknown blocks
- docs update explaining the output fields

## Tool I/O contract

### `describe_block(block_id)`

Input fields:
- `block_id`: required string

Output fields:
- `ok`: boolean
- `block_id`
- `label`
- `category_path`
- `flags`
- `loaded_from`
- `parameters`
- `inputs`
- `outputs`
- `asserts`
- `documentation`
- `doc_url`
- `warnings`
- `signature`

Error shape:
- `ok: false`
- `error_type`
- `message`
- `details` with the unknown `block_id`

## Implementation checklist

- define the normalized block record shape first
- decide which metadata fields are authoritative vs derived
- decide how to represent ports compactly
- decide how to surface asserts without overexplaining them
- decide what a minimal warning looks like
- decide how hier blocks are flagged in a lightweight way
- decide how unknown block ids are handled
- update all relevant docs

## Strict pass criteria

- known blocks return complete structured truth
- output is concise and machine-readable
- docs pointers and caveats are preserved
- no graph mutation occurs
- tests cover known, unknown, and hier-block cases

## Refactor / remove checklist

- remove prompt-only block descriptions once this exists
- remove duplicated schema extraction from callers
- keep all normalization in the catalog package
