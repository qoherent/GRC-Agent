# Beta Ready Status

Date: 2026-05-03

## Scope Statement

Beta-ready for bounded `inspect/search/help/preview/change` workflows on copied `.grc` graphs.

This is not production autonomy, not arbitrary graph design/repair, and not safe-by-default for original files.

## Supported Scope

- Default model-facing wrappers:
  - `inspect_graph`
  - `search_blocks`
  - `search_help`
  - `change_graph`
- Read-only inspection/search/help flows.
- Preview-only edits (no mutation).
- Verified committed edits through wrapper dispatch + runtime gates.
- Graph validation via `grcc`.
- Local checkpoint/history capture with CLI-only restore to explicit copy path.

## Non-Goals

- Production readiness
- Arbitrary graph autonomy
- Broad topology auto-repair
- Raw `.grc` YAML/source mutation
- Advisor-driven runtime routing
- Planner-based graph synthesis

## Runtime Boundary

- Semantic routing assistant (Advisor): shadow-only by default.
- Structural safety authority: runtime gates (schema/route/preflight/`grcc`/rollback/save rules).
- Mutation entrypoint for model-backed chat: `change_graph` only (MVP wrapper profile).
- `save_graph` is not model-facing in MVP default chat.

## Deterministic Handler Boundary

Python deterministic handlers own:

- parameter coercion/defaulting and validation
- endpoint/connection resolution
- insertion compatibility checks
- graph-delta tracking
- rollback behavior
- checkpoint journaling

Ambiguous target/endpoint/placement cases clarify; they do not auto-pick first candidates.

## Checkpoint / History Behavior

- Baseline checkpoint on load.
- Accepted checkpoint after committed success.
- No accepted checkpoint for preview/failure.
- Retention bounded per lineage.
- Restore is CLI-only and requires `--to` explicit copy path.
- Restore refuses overwrite of existing files.

## Latest Passing Evidence

- Controlled MVP wrapper dogfood:
  - `reports/dogfood/MVP_WRAPPER_CONTROLLED_DOGFOOD_2026-05-03.md`
  - 135/135 clean/safe observations on copied installed examples
  - legacy exposure 0, wrong handler 0, preview mutation 0, unsupported mutation 0, invalid commit 0, checkpoint-missing 0
- Maintenance verification:
  - `reports/MAINTENANCE_STATUS_2026-05-03.md`
  - deterministic gates + doctor/health/fake + vector regression pass

Latest release dashboard path:
- Not part of this checkout snapshot (`reports/live_eval/` not included).

## Beta Operating Instructions

1. Copy the graph first; never edit originals in place.
2. Run `uv run grc-agent doctor` and `uv run grc-agent health`.
3. Use chat with MVP wrappers (default profile).
4. Use preview before committed changes when uncertain.
5. Validate after committed changes.
6. Use history restore only to explicit new copy paths.
7. Record anomalies with `grc-agent dogfood record`.

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

Patch normal failures only if repeated across 3+ unrelated graphs.
