# Handoff

Date: 2026-05-24

This handoff is for the next implementation or audit agent. Do not trust it as
proof. Re-read the code, run the commands, inspect raw traces, and be willing to
delete or rewrite anything that is stale, overfit, or misleading.

## Current Context

GRC Agent is a local GNU Radio Companion `.grc` assistant. The current runtime
goal is safe local graph inspection and validated graph edits through a small
ToolAgents/llama.cpp tool surface.

Current model-facing tools:

- `inspect_graph`
- `search_blocks`
- `ask_grc_docs`
- `change_graph`

Everything else is internal: graph loading, saving, catalog lookup, vector
retrieval, transactions, validation, `grcc`, rollback, autosave, and history.
The model must not see lifecycle tools, raw YAML edit paths, shell/filesystem
tools, or low-level graph primitives.

The project is not production-ready. Treat it as an alpha/beta runtime with a
small release-validated subset and active hardening work.

## What Changed Recently

- Replaced the old custom llama chat adapter with ToolAgents runtime plumbing.
- Removed model-facing save/load wrappers and turn-plan logic.
- Kept exactly four model-facing wrappers.
- Made `inspect_graph` compact:
  - overview is topology-only
  - details carries target refs, selected params, connections, ambiguity, and
    truncation only when needed
- Made `search_blocks` hybrid:
  - exact/catalog lexical metadata lookup
  - cached in-memory SQLite FTS5 sparse ranking
  - vector retrieval when the local generated index is available
  - deterministic merge/rerank
- Expanded catalog search fields from installed GNU metadata to include
  parameter defaults, options, option labels, and option attributes. This fixed
  natural catalog queries such as `sine wave source` and `cosine source` without
  hardcoding block names.
- Kept docs/RAG explanation-only and stripped instruction-like source text from
  model-visible docs answers.
- Tightened `change_graph`:
  - compact `op + args` envelope
  - runtime op-specific arg validation
  - no generic model-facing `add_block`, `connect`, or `add_connected_block`
  - no hidden rewrite from unsupported generic ops into supported ops
  - `dry_run=true` stays preview-only
  - `add_signal_source_to_sum` uses preview/commit with preview tokens and graph
    validation
- Added active copied-file integrity refusal before committed mutation when the
  file changed or disappeared on disk.
- Removed the temporary online-agent prompt docs. The durable docs are now
  `BLUEPRINT`, `QUICKSTART`, `ISSUE_INTAKE`, `DEMO_VIDEO`, and this handoff.

## Current Verification Snapshot

Recently passed targeted checks:

```bash
uv run ruff check src/ tests/
git diff --check
uv run python -m unittest tests.test_mvp_tool_profile
uv run python -m unittest tests.test_toolagents_runtime tests.test_runtime_tool_validation
uv run python -m unittest tests.test_runtime_tool_result_contract
uv run grc-agent doctor
uv run grc-agent release-manifest
uv run grc-agent fake tests/data/random_bit_generator.grc
```

Focused search checks were also run after the latest `search_blocks` ranking
change:

```bash
uv run python -m unittest \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_search_blocks_uses_hybrid_retrieval_and_returns_minimal_rows \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_search_blocks_exact_catalog_match_works_without_vector_index \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_search_blocks_catalog_parameter_match_works_without_vector_index \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_search_blocks_catalog_matches_parameter_option_labels_without_vector_index \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_search_blocks_uses_sparse_fts_for_catalog_prose \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_search_blocks_reuses_catalog_fts_index_for_uncached_queries
```

Manual ranking probe after the latest search change:

- `sine wave source` ranked `analog_sig_source_x` first.
- `cosine source` ranked `analog_sig_source_x` first.
- `limit sample rate` ranked `blocks_throttle2` first.

Known verification gaps:

- Full `uv run python -m unittest` was not run because it is expensive and
  currently includes many integration-style tests.
- `uv run python -m tests.retrieval_eval.vector_regression` was attempted
  earlier and stopped after several silent minutes. Do not count it as passed.
- Broad live llama evals across graph families were not rerun after every
  hardening change.
- `uv run grc-agent health` reports `not_ready` when no llama.cpp server is
  reachable. That is expected passive health behavior. Use
  `uv run grc-agent doctor --start-llama` or chat startup to exercise launcher
  startup/reuse.

## Current Risks

- `grcc` proves compilability, not semantic correctness.
- `add_signal_source_to_sum` is useful but still needs more real-graph evals to
  prove it is a structural macro rather than a dial-tone-shaped success.
- Runtime op-specific validation exists, but the model-facing `change_graph`
  schema is still a compact envelope rather than a full JSON Schema
  discriminated union.
- `search_blocks` has cached in-memory FTS5, not a persistent SQLite index.
  That is deliberate for now, but it should be measured against startup and
  repeated-query costs.
- Autosave failure recovery is surfaced but not production-grade.
- File integrity checks cover the active copied graph path before mutation
  commits. Re-check manual `/save` and GUI race behavior before making stronger
  safety claims.
- Docs/RAG injection filtering is a targeted hardening step, not a complete
  security audit.
- There is still a large dirty refactor in the worktree. Do not assume all
  changed/deleted tests are intentionally final without reviewing them.

## Next Steps

1. Start by verifying, not editing:
   - `git status --short`
   - `uv run ruff check src/ tests/`
   - `git diff --check`
   - targeted ToolAgents/runtime tests
   - targeted wrapper tests
   - `uv run grc-agent doctor`
   - `uv run grc-agent fake tests/data/random_bit_generator.grc`
2. Run `uv run grc-agent doctor --start-llama` and `uv run grc-agent health`
   before any live model conclusions.
3. Re-run the source-to-sum live flow on a copied dial-tone graph and inspect:
   - raw requested tool calls
   - raw tool results fed back to the model
   - final assistant text
   - actual graph diff
   - validation/autosave evidence
4. Decide what to do with `vector_regression`:
   - make progress output visible, or
   - split a small fast retrieval smoke from the long regression gate, or
   - document why it is intentionally long.
5. Audit the deleted tests aggressively:
   - keep deletion if the test asserted removed legacy behavior
   - restore/rewrite if it covered a current safety invariant
6. Broaden live mutation evals:
   - two-tone plus noise
   - graph with multiple adders
   - graph with no adder
   - source params disagree
   - complex vs float source graphs
   - autosave failure simulation
7. Harden autosave/manual-save integrity:
   - active copied-file hash behavior
   - external GUI edit detection
   - atomic write/backup behavior
   - clear recovery status after save failure
8. Keep docs short. Delete stale reports instead of archiving them. Update
   durable docs only when behavior actually changes.

## Rules For The Next Agent

- Verify raw evidence yourself. Do not inherit confidence from this handoff.
- Be bold about deleting stale docs/tests/code, but honest about what was not
  verified.
- Do not chase symptoms with prompt patches or fixture-specific shortcuts.
- Fix authoritative data, schema, validation, transaction logic, or output shape.
- Do not restore legacy llama adapter code, turn-plan code, model-facing
  lifecycle tools, or assistant-text fallback parsing.
- Do not claim production readiness without live eval evidence and deterministic
  gates.
- Do not run the full unittest suite reflexively during iteration. Use targeted
  tests, then reserve full discovery for a release-candidate gate.
