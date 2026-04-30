# Dogfood Pass 3 2026-04-29

## Scope

- 28 targeted observations after the preview/apply STOP_THE_LINE fix.
- 24 held-out installed GNU Radio examples.
- 4 workspace/eval fixture stand-ins because no private user `.grc` files are available in this workspace.
- Focused on boundary behavior rather than happy-path coverage: preview-only wording, explicit apply after preview, save boundaries, vague topology requests, exact disconnect/rewire, failed rewires/adds, and save/reload-oriented tasks.
- Intake path: `reports/dogfood/dogfood_2026-04-29-pass3.jsonl` (ignored).
- Summary path: `reports/dogfood/dogfood_2026-04-29-pass3_report.json` (ignored).

## Result

- Total observations: 28.
- Clean/safe observations: 19.
- Safe preflight rejections: 4.
- Safe GNU-validation failures before commit: 1.
- Safe clarification or unsupported-input observations: 4.
- STOP_THE_LINE: 0.
- Preview mutations: 0.
- Apply during preview-only prompts: 0.
- Save without explicit request: 0.
- Save/reload mismatch: 0.
- Wrong-file risk: 0.
- Raw YAML bypass: 0.
- Repeated generic failure cluster: 0.

## Coverage

- Preview-only: "do not apply", "before applying", "preview only", "dry run", and "what would happen" variants.
- Preview then explicit apply: two installed examples where `propose_edit` was followed by an explicit apply/validate request.
- Save boundaries: validate without save, successful edit/validate/save, failed edit followed by a save request, and direct save-copy checks.
- Clarification: vague topology repair, vague rewire, vague disconnect, missing-anchor insertion, and absent-target preview.
- Negative/rollback: invalid parameter symbol, invalid disconnect, invalid rewire, invalid message-to-stream add, and invalid absent-target preview.
- Direct public tools: `remove_connection`, `rewire_connection`, `apply_edit`, `propose_edit`, `save_graph`, `validate_graph`, `get_grc_context`, and `search_grc`.

## Findings

- Preview-only prompts no longer expose or trigger `apply_edit`, including negated apply wording.
- Explicit apply after preview remains available when the user asks for it.
- Save only occurred in save-copy tasks with explicit save wording.
- Failed mutation attempts returned preflight or GNU-validation failure before commit; no saved partial state was observed.
- One installed trellis graph failed to load because the file was not in the supported top-level mapping shape. This is an input/corpus limitation, not runtime mutation behavior.
- Vague insertion/topology/disconnect requests clarified or safely failed instead of guessing placement or endpoints.
- A tags-to-PDU insertion attempt used the bounded `auto_insert_block` workflow and failed validation without committing. This remains a one-off safe false start, not a routing/schema patch candidate.

## Patch Decision

No additional runtime patch is justified from this pass.

The pass found no unresolved STOP_THE_LINE event, no preview mutation, no save boundary violation, and no repeated generic failure across unrelated graphs. Remaining non-clean observations are safe clarification, safe preflight rejection, safe GNU-validation failure before commit, or unsupported installed-example input.
