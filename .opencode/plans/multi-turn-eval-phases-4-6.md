# Multi-Turn Eval Roadmap — Phases 4–6

**Status**: Approved. Awaiting implementation after existing eval misses are stabilized.

## Summary

Phases 1–3 test single-turn behavior (one user message, one bounded `run_bounded_llama_turn`).
Phases 4–6 extend to multi-turn conversations where the model must maintain state awareness,
handle failures, and complete compound workflows across accumulated history.

---

## Runtime Infrastructure (prerequisite for all multi-turn phases)

Four changes to the existing runtime before any multi-turn eval:

### 1. History compaction (`agent.py` or `llama_server.py`)

Before each new turn:
- Drop all prior `role="session"` entries and replace with one fresh snapshot.
- For `role="tool"` entries older than the previous turn, truncate content to `{ok, message, error_type, active_session}` only — drop large field lists like full parameter/connection arrays.

This keeps ~4 turns comfortably within the 12K token budget. Each turn costs ~500–1500 tokens (user + assistant + tool results + session refresh).

### 2. Session auto-refresh

At the start of each `run_bounded_llama_turn`, the agent re-records `_record_active_session_history(reason="turn_refresh")` so the model always sees the current dirty/validation/revision state, even if edits from the prior turn changed the graph.

### 3. Reminder scope (no change needed)

`_build_follow_up_reminder` already inspects only the `user_message` argument passed to the current turn. Since multi-turn calls `run_bounded_llama_turn` separately for each turn with its own `user_message`, the existing behavior is correct.

### 4. CLI REPL loop (`cli.py`)

New `repl` subcommand (or `chat` invoked without `--message`):
- Reads user input in a loop via `input(">>> ")`
- Calls `run_bounded_llama_turn` on the same agent each iteration
- Prints active session state plus assistant response
- Exits on `/quit`, `/exit`, or `EOFError` (Ctrl-D)
- History accumulates across the full conversation
- Compaction runs between turns

---

## Phase 4 — Multi-Turn Continuity (~40 cases, unbounded turns)

**Purpose**: Tests whether the model maintains state awareness and correct tool routing across multiple conversation turns on the same session.

**Runner**: New `run_phase4.py` with:
- `MultiTurnCase` dataclass containing a list of `TurnSpec` objects
- Each `TurnSpec` carries `prompt`, `expected_tools_in_order`, `transaction_checks`, `tool_arg_checks`, `text_contains_any_checks` — reusing Phase 3 checking logic
- Runner creates one `FlowgraphSession` + `GrcAgent`, calls `run_bounded_llama_turn` for each turn in sequence on the same agent
- Each turn checked independently (routing + args + text)
- A case passes when every turn passes on majority across N runs

### Categories

| Category | Cases | Typical turns | Description |
|----------|-------|---------------|-------------|
| `follow_up_edit` | 6 | 2 | "Change rate to 48k" → "Now make it 96k" |
| `inspect_then_act` | 6 | 2 | "Summarize the graph" → "Change samp_rate to 32k" |
| `search_then_navigate` | 5 | 2 | "Find an AGC block" → "Describe that block's ports" |
| `state_awareness` | 5 | 2–3 | "Change rate to 48k" → "Validate" → "Save" across separate turns |
| `edit_then_query` | 5 | 2–3 | "Add variable debug_flag=0" → "What variables are in my graph now?" |
| `repair_flow` | 4 | 2–3 | "Remove samp_rate but keep graph working" → "Validate" → "What changed?" |
| `error_then_fix` | 4 | 2 | "Remove throttle block" → "That failed, disconnect source first then remove it" |
| `natural_multi` | 5 | 2–4 | Free-form realistic multi-step conversation |

### Key checks

- Turn 2+ must use the *current* session state, not stale state from turn 1
- Pronoun and implicit references ("change it to 96k") must resolve to the correct parameter
- The model must not re-ask about information it already learned in prior turns
- After an edit in turn 1, turn 2 must reflect the updated graph state
- No-tool negative responses must remain stable across turns

---

## Phase 5 — Error Recovery & Self-Correction (~25 cases)

**Purpose**: Tests whether the model correctly handles tool failures and self-corrects within a turn or across turns.

**Runner**: Same `MultiTurnCase` runner as Phase 4. Cases are designed to trigger failures and require recovery.

### Categories

| Category | Cases | Description |
|----------|-------|-------------|
| `validation_rejection` | 5 | Tool-call schema rejection → model retries with correct args |
| `preflight_failure` | 5 | `apply_edit` returns preflight errors → model reports or adjusts |
| `gnu_validation_fail` | 4 | Edit passes preflight but fails `grcc` → model explains the failure |
| `stale_reference` | 4 | Model uses a block name removed in a prior turn → handles gracefully |
| `retry_with_fix` | 4 | Model attempts edit, gets specific error hint, retries with corrected args |
| `negative_cascaded` | 3 | Multi-step where intermediate failure should stop the chain cleanly |

---

## Phase 6 — Compound Workflows (~30 cases)

**Purpose**: Tests longer 4–6+ turn workflows with backtracking, decision-making, and mixed tool sequences.

**Runner**: Same runner. Deeper cases.

### Categories

| Category | Cases | Description |
|----------|-------|-------------|
| `full_pipeline` | 5 | Search → describe → edit → validate → save across multiple turns |
| `rewire_complex` | 5 | Add second trace → verify → add third trace → validate → save |
| `multi_block_edit` | 5 | Coordinated edits across multiple blocks with state checks in between |
| `exploration_driven` | 5 | "I want to do X" → model must search, choose, plan, apply |
| `cross_session` | 5 | Load graph A → edit → load graph B → edit → switch back to A |
| `undo_workaround` | 5 | Simulate undo by reversing edits (model must track what changed) |
| `backtrack` | 5 | Apply edit → realize wrong → reverse → try different approach |

---

## Implementation Order

1. **Runtime infrastructure**: history compaction, session auto-refresh, CLI REPL loop
2. **Phase 4**: runner (`run_phase4.py`) + ~40 cases
3. **Phase 4 eval run** + prompt/hint tuning
4. **Phase 5**: after Phase 4 stabilizes above ~90%
5. **Phase 6**: after Phase 5 stabilizes above ~90%

## Changes to BLUEPRINT.md

After implementation begins, the following sections in `docs/BLUEPRINT.md` should be updated:

1. Add `## Multi-Turn Eval Roadmap (Phases 4–6)` section with the full plan above
2. Update `## Backlog` to reflect:
   - Item 3 ("Add a one-session interactive CLI conversation loop") → link to Phase 4 infrastructure
   - Item 4 ("Decide multi-turn session persistence") → resolved: one-session REPL with compaction
3. Add milestone entry for Phase 4 infrastructure + eval once results are available
4. Update `## Current Verified State > Live model eval` table with Phase 4+ results as they land
5. Update `docs/LLAMA_EVAL.md` with Phase 4+ case definitions and results

## Dataclass Design for `run_phase4.py`

```python
@dataclass(frozen=True)
class TurnSpec:
    prompt: str
    expected_tools_in_order: list[str] = field(default_factory=list)
    checked_tool_name: str | None = None
    tool_arg_checks: dict[str, Any] | None = None
    transaction_checks: list[dict[str, Any]] | None = None
    transaction_checks_ordered: bool = True
    text_contains_any_checks: list[str] | None = None

@dataclass(frozen=True)
class MultiTurnCase:
    category: str
    name: str
    turns: list[TurnSpec]
    fixture_name: str = DEFAULT_FIXTURE_NAME
    target_fixture_name: str | None = None
    description: str = ""
```

The runner loop:

```python
for turn_spec in case.turns:
    result = run_bounded_llama_turn(agent, client, turn_spec.prompt, model=model)
    # check routing, args, text against turn_spec expectations
    # accumulate per-turn results
```

Each turn runs on the same agent with accumulating history. Compaction fires between turns.
A case passes when *every* turn passes on majority across N runs (same MAJORITY_THRESHOLD = 0.5).

## History Compaction Design

Placement: a new `compact_history()` method on `GrcAgent` (or a standalone helper).

Called at the start of `run_bounded_llama_turn` (before appending the user message).

Algorithm:
1. Find the latest `role="session"` entry. Remove all earlier `role="session"` entries.
2. For `role="tool"` entries that are older than the most recent complete turn boundary
   (where a turn boundary = a `role="user"` entry), truncate the content dict:
   - Keep: `ok`, `message`, `error_type`, `active_session`, `name`
   - Drop: `results`, `parameters`, `inputs`, `outputs`, `asserts`, `documentation`,
     `blocks`, `connections`, `nodes`, `edges`, `affected_blocks`, `affected_connections`,
     and any other list/dict fields larger than 200 chars when serialized
3. Keep all `role="user"` and `role="assistant"` entries intact (they are small).
4. Keep all `role="reminder"` entries intact (they are small and de-duplicated already).

This preserves the conversation narrative while capping token growth.
