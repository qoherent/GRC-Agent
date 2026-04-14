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

## Implementation status

Phase 2 is implemented in the current repo state.

What landed:
- `src/grc_agent/catalog/{__init__,describe,normalize,schema,loaders,errors}.py`
- `tests/catalog/{test_describe_block,test_catalog_errors}.py`
- public contract: `describe_block(block_id)`
- shared GNU catalog loader seam: catalog root discovery, file collection, and raw `.block.yml` loading now live under `src/grc_agent/catalog/loaders.py` and are reused by `src/grc_agent/retrieval/index.py`
- normalized category paths: block-local `category` first, tree-derived fallback second, warning on multi-category ambiguity
- warning-based hierarchy marking for generated and built-in hierarchical wrappers
- stable bad-metadata behavior: malformed YAML, unreadable files, and invalid section shapes return structured `ok: false` payloads instead of uncaught parser errors

## Implemented baseline to reuse

- `src/grc_agent/retrieval/` already exists and is the current Phase 1 baseline.
- `src/grc_agent/catalog/` already exists and is the current Phase 2 baseline.
- Reuse the existing GNU catalog root discovery and retrieval package behavior instead of adding a second catalog-discovery path.
- If a later phase needs raw catalog loaders or normalized shared records, reuse the shared catalog seam instead of duplicating YAML traversal in parallel packages.
- `search_grc(query, scope="catalog|session", k=5)` and `initialize_retrieval(...)` are already live and should be treated as the stable retrieval/search boundary below this phase.

---

## Package boundary

Current package boundary:

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
- malformed metadata and unreadable files fail with a stable public error payload
- output is concise, structured, and machine-readable

## Strict success criteria

This phase is done only when all are true:
- `describe_block` exists and is stable
- it uses local catalog data, not free-form prose
- it returns enough detail to choose or instantiate a block safely
- it has tests on real GRC block metadata
- malformed catalog metadata fails inside the structured error envelope
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
- tests cover known, unknown, hier-block, and malformed-metadata cases

## Refactor / remove checklist

- remove prompt-only block descriptions once this exists
- remove duplicated schema extraction from callers
- keep all normalization in the catalog package
