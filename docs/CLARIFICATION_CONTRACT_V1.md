# Clarification Contract v1

## Purpose

When autonomous tool execution is ambiguous — multiple validated candidates exist — the agent asks the user a compact MCQ instead of guessing.

## Core rules

- Options A/B/C are always generated from real executable candidates.
- Option D is always free text / custom.
- No mutation occurs until the user confirms (or the model auto-commits a single validated candidate).
- The live session is never modified when a clarification is created.

## Structure

```python
{
    "clarification_required": True,
    "clarification_id": "uuid",
    "kind": "choose_insert_candidate",
    "question": "Multiple compatible blocks were found...",
    "options": [
        {
            "label": "A",
            "title": "Insert 'blocks_throttle2' into src_0:0->throttle_0:0",
            "description": "...confidence: high",
            "tool_name": "insert_block_on_connection",
            "tool_args": {"connection_id": "...", "block_type": "...", "instance_name": "...", "params": {}},
            "metadata": {"score": 5, "goal_mode": "generic"}
        }
    ],
    "custom_option": {"label": "D", "title": "Other / custom", "free_text": True}
}
```

## Pending state in GrcAgent

- Stored in `_pending_clarification` + `_pending_clarification_revision`.
- Expires when session state_revision changes.
- Resolvable via `resolve_pending_clarification(user_message)`:
  - `A/B/C` -> executes stored option via `execute_tool`.
  - `D` or free text -> clears pending, returns `mode="custom"`.
  - Invalid -> reminder text, no mutation, pending kept.
- `llama_server.py` contains no clarification logic.

## UX Presentation (v1)

Render function: `render_clarification_prompt(payload)` in `src/grc_agent/runtime/clarification.py`.

CLI REPL (`_run_repl_loop` in `src/grc_agent/cli.py`):
- Detects pending clarification before model turn.
- Prints A/B/C/D MCQ.
- User replies A/B/C → executes verified option directly, no model call.
- User replies D → clears pending, passes free text to model.
- Invalid → reminder, pending kept.

## `auto_insert_block` integration

- Candidate validation uses cloned sessions (no live mutation).
- Exactly 1 valid candidate -> auto-commits as today.
- >= 2 valid candidates -> returns clarification payload.
- 0 valid candidates -> safe rejection with diagnostics.
- Explicit-family goals still filter to matching family before validation.

## Manual Validation v1

Deterministic CLI REPL integration tests: `tests/integration/test_cli_clarification_repl.py`.

### Approach

Pending clarification seeded directly via `_store_pending_clarification`.
No `auto_insert_block` dependency. `builtins.input` mocked. No live llama server.

### Coverage

| Scenario | Test | Verified |
|----------|------|----------|
| MCQ render | `test_renders_header_and_mcq_from_seeded_payload` | Header, A/B/D labels, block_type, connection_id, no raw JSON |
| No pending | `test_returns_false_when_no_pending` | Returns `False`, no output |
| Reply A | `test_reply_a_routes_to_verified_tool_handler` | Routes to `execute_tool`, prints "Executed", clears pending |
| Reply C (invalid) | `test_reply_invalid_keeps_pending_and_prints_reminder` | Prints reminder, pending kept |
| Reply D/custom | `test_reply_d_custom_clears_pending_and_routes_to_model` | Clears pending, no mutation, routes to model |

Option A runs `_insert_block_on_connection` unmocked.
The tool result depends on installed blocks; the test verifies CLI routing
regardless of outcome.
Agent-level grcc-verified execution is covered by `tests/test_clarification_contract.py`.

### Known limits

- Full end-to-end manual REPL with a live llama.cpp server is not automated.
- Seeded payload is fixed; real payloads come from `auto_insert_block`.

## Known limits v1

- Only `auto_insert_block` uses clarification.
- Clarification options are limited to `insert_block_on_connection`.
- Max 3 options (A/B/C) + D.
- UX presentation is only in the CLI REPL (`_run_repl_loop`). Single-turn (`--message`) renders after the assistant response but cannot wait for user reply interactively.
