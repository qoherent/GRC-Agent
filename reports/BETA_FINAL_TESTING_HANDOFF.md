# Beta Final Testing Handoff

Date: 2026-05-03

## Ready Statement

Ready for user manual beta on copied graphs.

## Supported Scope

- Bounded inspect/search/help/preview/change workflows on copied `.grc` files.
- Default model-facing MVP wrappers only:
  - `inspect_graph`
  - `search_blocks`
  - `search_help`
  - `change_graph`
- Legacy low-level tools remain internal/compatibility-only.
- Advisor remains shadow-only and does not control default runtime routing.
- Vector retrieval remains frozen/read-only.

## Non-Goals

- Production autonomy.
- Arbitrary graph design/repair.
- Raw `.grc` YAML/source editing.
- Advisor runtime promotion.
- Planner-driven graph synthesis.

## Install And Check

```bash
uv sync --locked
uv run grc-agent doctor
uv run grc-agent health
```

## Local Beta Smoke

```bash
uv run grc-agent doctor
uv run grc-agent health
uv run grc-agent fake tests/data/random_bit_generator.grc
uv run python -m tests.retrieval_eval.vector_regression
uv run python -m unittest tests.test_mvp_tool_profile tests.test_mvp_wrapper_dispatch tests.test_history_journal
```

## Safe Manual Beta Flow

1. Copy original graph to a writable path.
2. Open copied graph in chat.
3. Inspect and validate first.
4. Use search wrappers for blocks/help.
5. Preview before commit when uncertain.
6. Apply bounded change and re-validate.
7. Review history/checkpoints.
8. Restore only to explicit new copy path if needed.

Example:

```bash
cp /path/to/original.grc /tmp/grc-agent-test.grc
uv run grc-agent chat /tmp/grc-agent-test.grc
```

Safe first prompts:

- `Summarize this graph.`
- `Validate this graph.`
- `Find a low-pass filter block.`
- `Search help for stream tags.`
- `Preview changing samp_rate to 48000. Do not apply.`
- `Change samp_rate to 48000 and validate.`
- `Show history/checkpoints.`

## History / Restore

- `history list`, `history show`, `history diff` for checkpoint inspection.
- `history restore <id> --to <new_copy_path>` only.
- Restore refuses overwriting existing files.

## Known Limits

- Use copied graphs only; do not edit originals in place.
- `save_graph` is not model-facing in MVP default chat.
- Broad topology “fix/repair/rewire everything” requests clarify/refuse.
- Raw YAML/source edit, undo/redo, and export/codegen are unsupported.
- Search/manual/vector results are read-only guidance and do not authorize mutation.

## Issue Intake

Use:

```bash
uv run grc-agent dogfood record "prompt" --source real_user --task-type other --failure-category other --json
```

Capture:

- prompt
- expected behavior
- actual behavior
- copied graph reference (sanitized)
- wrapper used / handler notes (if available)
- graph delta result
- validation result
- checkpoint result
- severity and reproducible flag

## Patch Policy

Patch immediately only for STOP_THE_LINE:

- unsafe mutation
- preview mutation
- unsupported mutation
- raw YAML bypass
- invalid graph committed/saved
- wrong file write
- checkpoint failure after commit
- rollback bypass
- legacy tool exposure in default MVP path

Patch normal failures only when repeated across 3+ unrelated graphs.

## Latest Verification (This Milestone)

Required deterministic/ops checks were rerun for this handoff:

- `uv run ruff check src/ tests/`
- `uv run ruff check`
- `uv run python -m unittest`
- `uv run python -m tests.retrieval_eval.vector_regression`
- `uv run grc-agent doctor`
- `uv run grc-agent health`
- `uv run grc-agent fake tests/data/random_bit_generator.grc`

Results:

- Ruff checks: pass.
- Unittest: pass (`Ran 1008 tests`, `OK`, `skipped=9`).
- Vector regression: pass (`ok=true`, `vector_top_k_hits=276/290`, protected metrics clean).
- Doctor: pass (Python/GNU Radio/`grcc`/config/retrieval readiness checks OK).
- Health: pass (`status=ok`).
- Fake deterministic smoke: pass.
