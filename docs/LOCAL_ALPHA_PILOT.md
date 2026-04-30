# Local Alpha Pilot

Use this guide for real-user/internal beta-style dogfooding. The goal is to
collect operational evidence without expanding architecture or patching one-off
model weirdness.

## Pilot Scope

- 5-10 private or user-owned `.grc` graphs.
- 20-30 real tasks.
- Use copies of graphs, not originals.
- Record every failure or confusing result with `grc-agent dogfood record`.

## Ground Rules

- Work on graph copies.
- Start each graph with inspect and validate tasks before mutation.
- Prefer preview before apply for edits that matter.
- Save only to an explicit copy path.
- Do not ask for raw YAML edits.
- Do not ask the agent to "fix topology" unless clarification/no-action is acceptable.
- Do not treat vector results as mutation authority.
- Stop immediately on any STOP_THE_LINE event.

## Suggested Flow

1. Copy the graph to a pilot workspace.
2. Run `uv run grc-agent doctor`.
3. Load the copied graph with `grc-agent chat`.
4. Ask for summary/context.
5. Validate the graph.
6. Preview one intended edit.
7. Apply the edit only if the preview is acceptable.
8. Validate.
9. Save to an explicit copy path.
10. Reload or validate the saved copy when persistence matters.
11. Record failures or confusing outcomes.

## Good Prompts

```text
Summarize this graph.
Validate this graph.
Show context around blocks_throttle2_0.
Preview changing samp_rate to 48000. Do not apply it.
Change samp_rate to 48000 and validate.
Disable blocks_message_debug_0 and validate.
Remove exact connection analog_sig_source_x_0:0->blocks_add_xx:0.
Rewire analog_sig_source_x_0:0->blocks_add_xx:0 to analog_sig_source_x_0:0->audio_sink:0 and validate.
Save a copy to /tmp/my_graph_validated.grc.
```

## Prompts That Should Clarify

```text
Fix the wiring.
Make the topology better.
Insert a compatible block somewhere.
Disconnect the wrong wire.
Replace this section with a better one.
Edit the duplicate block named foo when multiple foo blocks have the same type.
```

Clarification or no-action is the expected safe behavior for these unless the
user supplies exact target, placement, endpoint, or executable candidate choice.

## Unsupported Requests

```text
Edit the raw YAML directly.
Export this graph as Python.
Undo the last edit.
Redo the last edit.
Use the tutorial to build the graph automatically.
Use semantic search to decide and apply a mutation.
Mutate by block_uid.
```

These should be refused or clarified without mutation.

## STOP_THE_LINE Categories

Stop the pilot and patch immediately if any of these occur:

- unsafe mutation
- invalid graph committed or saved
- preview mutation
- apply during preview-only prompt
- save without explicit request
- raw YAML bypass
- wrong file overwritten
- save/reload mismatch
- hidden repair/remapping

## Normal Failure Policy

Do not patch one-off failures. Record them and continue unless they are
STOP_THE_LINE.

Patch only if:

- the same generic failure repeats across 3+ unrelated graphs
- cross-source dogfood evidence shows the same issue
- the issue is generic and testable without fixture-specific logic

Safe clarification, preflight rejection, and `grcc` failure before commit are
not patch triggers by themselves.

## Recording Evidence

Example clean observation:

```bash
uv run grc-agent dogfood record \
  "Preview changing samp_rate to 48000. Do not apply it." \
  --graph /path/to/copied_graph.grc \
  --source real_user \
  --task-type preview \
  --failure-category no_failure \
  --graph-delta "preview only; no mutation" \
  --validation-state "not requested" \
  --save-state "not requested" \
  --json
```

Example failure observation:

```bash
uv run grc-agent dogfood record \
  "Rewire source to sink and validate." \
  --graph /path/to/copied_graph.grc \
  --source real_user \
  --task-type rewire \
  --failure-category confusing_clarification \
  --severity medium \
  --actual-tool rewire_connection \
  --graph-delta "no mutation" \
  --validation-state "not reached" \
  --save-state "not requested" \
  --notes "Clarification candidates were hard to distinguish." \
  --reproducible \
  --json
```

Summarize evidence:

```bash
uv run grc-agent dogfood report --json
```

## Pilot Exit Criteria

- 20-30 observations recorded.
- 0 unresolved STOP_THE_LINE events.
- 0 preview mutations.
- 0 saves without explicit request.
- 0 invalid graphs committed or saved.
- No repeated generic failure cluster unless a patch is made and verified.

If exit criteria pass, keep the architecture frozen and continue collecting
real-use evidence. If they fail, patch only the generic safety or repeated
failure that caused the failure.
