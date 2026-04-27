# Manual External Test Checklist v1

Quick manual verification on a fresh `.grc` file before release.

## Prerequisites

- `grc-agent` installed (`uv sync`)
- `llama.cpp` server running or auto-started
- A temp copy of a real `.grc` file (not the installed original)

## Setup

```bash
mkdir -p /tmp/grc_agent_external_test
cp /usr/share/gnuradio/examples/audio/dial_tone.grc /tmp/grc_agent_external_test/dial_tone.grc
uv run grc-agent chat /tmp/grc_agent_external_test/dial_tone.grc
```

## Steps

```
# 1. Summarize
> Summarize the graph

# 2. Validate
> Validate the graph

# 3. Natural insert request
> Add a throttle between the signal source and the audio sink

# 4. Resolve MCQ if shown (pick A/B/C or describe what you want)

# 5. Edit one parameter
> Change the sample rate to 48000

# 6. Save to temp copy
> Save to /tmp/grc_agent_external_test/test_output.grc

# 7. grcc the saved output
grcc /tmp/grc_agent_external_test/test_output.grc
```

## Expected

- Steps 1-2: tool called, correct result
- Step 3: `auto_insert_block` called; MCQ shown if ambiguous, or committed
- Step 4: user picks option, graph updated, no crash
- Step 5: `apply_edit` called, parameter changed
- Step 6: `save_graph` called, file written
- Step 7: grcc exits 0, no errors

## Validation expectations

- If the original graph validates before editing, the saved output must validate too.
- If the original graph fails because of environment/version mismatch (e.g. missing UHD hardware), record that and do not treat it as an agent regression unless the agent made it worse.

## Pass criteria

- No unsafe mutation
- No raw YAML shown to user
- grcc exits clean on saved output (or original also failed for the same reason)
- No STOP_THE_LINE
