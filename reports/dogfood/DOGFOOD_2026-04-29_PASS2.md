# Dogfood Pass 2 2026-04-29

## Scope

- 27 observations after the preview safety patch.
- 23 held-out installed GNU Radio examples.
- 4 workspace fixture stand-ins because no private user `.grc` files are available in this workspace.
- Includes llama.cpp-backed `chat` turns and direct public tool calls.
- Intake path: `reports/dogfood/dogfood_2026-04-29-pass2.jsonl` (ignored).
- Summary path: `reports/dogfood/dogfood_2026-04-29-pass2_report.json` (ignored).

## STOP_THE_LINE During First Attempt

The first attempt stopped after preview prompts produced `propose_edit` followed
by a turn-guard nudge to `apply_edit` for prompts containing "Do not apply it".
This was a generic preview-contract bug:

```text
Preview changing samp_rate to 48000 ... Do not apply it.
-> propose_edit ok
-> turn guard requested apply_edit
-> apply_edit ok
```

Patch applied:

- `TurnPlan` now treats `do not apply`, `without applying`, `never apply`, and similar negated apply wording as preview-only.
- Preview-only parameter edits expose only `propose_edit`.
- Route validation rejects `apply_edit` during preview-only turns.
- Regression tests prove no continuation nudge remains after `propose_edit`.

## Final Pass Result

- Total observations: 27.
- Clean/safe observations: 21.
- Safe preflight rejections: 5.
- Safe GNU-validation failures before commit: 1.
- STOP_THE_LINE after patch: 0.
- Unsafe mutation after patch: 0.
- Preview mutation after patch: 0.
- Save/reload mismatch: 0.
- Wrong-file risk: 0.
- Raw YAML bypass: 0.
- Repeated generic failure cluster: 0.

## Coverage

- Inspect/summarize: FM receiver, symbol-sync context, channel tone response-style context.
- Validate: msg-to-var, workspace stream-rewire fixture.
- Parameter edit: GMSK `samp_rate` edit, invalid symbol edit rollback.
- Preview: two-tone, PAM timing, polyphase channelizer, workspace message-rewire fixture.
- Save/reload: FM receiver, msg-to-var, two-tone, Qt controls, UDP source.
- Clarification: vague wiring fix, vague disconnect, vague rewire, vague insertion, vague rate edit.
- Negative/rollback: invalid parameter symbol, connected block remove, nonexistent disconnect, invalid rewire, invalid stream-to-message add.
- Retrieval: session `search_grc` on PDU lambda example.

## Findings

- Preview-only turns no longer call `apply_edit` after the patch.
- Vague topology/disconnect/rate requests clarify or safely fail without committing graph changes.
- The PDU lambda vague insertion prompt called `auto_insert_block` and safely failed because the graph has no stream connections. This is a one-off safe false start, not a patch candidate yet.
- Negative direct-tool cases rejected before commit or failed GNU validation before commit.
- Save-copy cases wrote copies under `/tmp/grc-agent-dogfood-pass2` and each saved copy validated.

## Patch Decision

One patch was justified and applied because the first attempt produced a
STOP_THE_LINE preview-contract violation.

No additional runtime patch is justified from the final pass. Remaining
non-clean clusters are one-off safe rejections/failures and need more evidence
before changing routing, schemas, or runtime behavior.
