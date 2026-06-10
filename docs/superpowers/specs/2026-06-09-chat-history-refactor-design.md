# ChatHistory refactor — design spec

Date: 2026-06-09
Status: Approved (build mode)
Owner: GRC Agent runtime
Scope: Backend `agent.history`, `toolagents_runtime`, `sessions_store`, GUI
`main_window` / `chat_widget`, and tests.

## 1. Background and motivation

GRC Agent currently keeps **three** parallel representations of the same
conversation:

1. `GrcAgent.history: list[dict[str, Any]]` — backend model input, accumulated
   by hand with custom role keys (`user`, `assistant`, `tool`, `session`).
2. `ChatWidget._history: list[dict[str, str]]` — GUI display log, separate
   in-memory structure owned by the chat widget.
3. Rows in `~/.grc_agent/sessions.db` — flat text + role pairs, used for
   persistence, recent-sessions preview, and resume.

These three never share a structure. A hand-rolled `ToolAgentsHistoryAdapter`
exists only to translate between (1) and the ToolAgents `ChatMessage` objects
that `ChatToolAgent.step` actually accepts.

The current architecture has a critical correctness bug: when the user reopens
a past session, the agent's in-memory history is reconstructed from the DB
but **drops every tool message, every assistant `tool_calls` row, and the
active `session` snapshot**. A resumed session therefore starts with no
`inspect_graph` / `search_blocks` evidence in the model's context. BLUEPRINT.md
documents this as intentional for *display* but the implementation reuses the
same rows as the model input, so the model loses context.

The ToolAgents library we already depend on (`ToolAgents==0.3.0`) ships a
`ChatHistory` Pydantic model with `add_user_message`, `add_assistant_message`,
`add_message`, `save_to_json`, `load_from_json`, etc. — exactly the surface
the reference docs recommend. Adopting it is the smallest correct change.

## 2. Goals

1. One conversation object: `GrcAgent.chat_history: ChatHistory`.
2. Display rows (`tool_started`, `tool_finished`, `mutation`, `error`) stay
   separate from model rows. They are not part of the model's input.
3. Resume replays *model* rows from the DB payload column. The current
   resume bug is fixed in the same change.
4. Streaming becomes real: `ChatToolAgent.stream_step` emits tokens as the
   model produces them. The post-hoc QTimer throttle in `workers.py` is
   deleted.
5. The `ToolAgentsHistoryAdapter` collapses to a thin shim that just builds
   the system message and the optional reminder. All other translation is
   eliminated.
6. Tests cover resume, round-trip persistence, and the new render path.

## 3. Non-goals

- `AdvancedAgent`, semantic memory, summarization, app state. Out of scope.
- Changes to the three model-facing wrappers
  (`inspect_graph`, `query_knowledge`, `change_graph`).
- Changes to the `.grc` transaction model, validation, `grcc`, or rollback.
- The CLI Markdown export of sessions (`sessions show`, `sessions export`).
  The CLI view becomes a projection over the new payload column.

## 4. New data shapes

### 4.1 Backend

`GrcAgent.chat_history: ChatHistory` (replaces `self.history: list[dict]`).
The old `self.history` attribute is removed in this refactor. All readers and
writers are updated in the same change.

The "session snapshot" pseudo-role is moved out-of-band. `GrcAgent` keeps a
`session_snapshot: dict | None` set by `_record_active_session_snapshot()`.
The snapshot is no longer smuggled through the conversation list.

### 4.2 Persistence

`SessionStore.append(session_id, role, text, payload=None)` — `payload` already
exists. The contract is extended:

- Display rows: `role ∈ {user, assistant, tool_started, tool_finished, mutation, error}`
  keep the flat `text`-only shape. `payload` is unused.
- Model rows: `role ∈ {assistant_model, tool_model}` carry
  `payload: ChatMessage.model_dump()` serialized as JSON. They are *never*
  shown in the chat widget; they exist only to rebuild the model's context on
  resume.

Schema is unchanged. Two new role strings appear in the `messages.role` column.
No migration needed.

### 4.3 GUI

`ChatWidget._history` is unchanged (it is the display log). The GUI's role
during resume is now:

1. Read all rows for the session from `sessions.db`.
2. Partition rows into `display_rows` (existing roles) and `model_rows`
   (the new `assistant_model` / `tool_model` roles).
3. Replay `display_rows` into `chat_widget._history`.
4. Replay `model_rows` into `agent.chat_history` by `ChatMessage.from_dict(payload)`.
5. Reset KV-cache session id before replay so the model starts the next
   turn with a fresh context window.

## 5. Render path

`grc_agent.runtime.model_context.render_model_messages` becomes:

```python
def render_model_messages(
    chat_history: ChatHistory,
    *,
    system_prompt: str,
    reminder: str | None = None,
) -> list[ChatMessage]:
    messages: list[ChatMessage] = [ChatMessage.create_system_message(system_prompt)]
    messages.extend(chat_history.get_messages())
    if reminder:
        # Add as a structured additional field on the last user message, OR
        # add as a Custom-role ChatMessage. We pick the latter for clarity.
        messages.append(
            ChatMessage(
                id=str(uuid.uuid4()),
                role=ChatMessageRole.Custom,
                content=[TextContent(content=f"Runtime reminder: {reminder}")],
                additional_fields={"custom_role": "runtime_reminder"},
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )
    return messages
```

`GrcAgent.get_model_messages()` returns `list[ChatMessage]`, not
`list[HistoryEntry]`. Callers (`ToolAgentsRunner.run_turn`) consume
`ChatMessage` directly — no adapter needed.

## 6. ToolAgentsHistoryAdapter collapse

`ToolAgentsHistoryAdapter` keeps exactly one helper:

```python
def system_message_with_reminder(system_prompt: str, reminder: str | None) -> ChatMessage
```

Everything else (`from_openai_messages`, `from_openai_message`,
`assistant_history_entry`, `_parse_history_tool_call`,
`_tool_call_as_history_payload`, `_content_list_as_text`, `_message_text`,
`_chat_message_as_openai_response`) is deleted. The runtime appends the
typed `ChatMessage` returned by `chat_agent.step` directly to
`chat_history` via `chat_history.add_message(message)`.

`ToolAgentsJsonClient.create_chat_completion` (used for JSON-only
helper calls like docs answer) becomes:

```python
def create_chat_completion(self, *, model, messages, response_format=None):
    chat_history = ChatHistory()
    chat_history.add_messages_from_dictionaries(messages)
    settings = self.provider_config.create_settings(...)
    response = self.agent.step(chat_history.get_messages(), settings=settings, ...)
    return chat_message_as_openai_response(response, ...)
```

The OpenAI-shaped response is built from the `ChatMessage` directly with no
list-of-dicts round trip.

## 7. Resume path (the bug fix)

`main_window._open_past_session` after the refactor:

```python
def _open_past_session(self, session_id: int) -> None:
    session_rec = get_session_sync(_default_sessions_db(), session_id)
    rows = list_messages_sync(_default_sessions_db(), session_id)

    # 1. Autoload the GRC graph associated with the session
    if session_rec and session_rec.graph_path and Path(session_rec.graph_path).exists():
        self.open_file(Path(session_rec.graph_path))
    else:
        self.chat_widget.clear()

    # 2. Reset both the GUI log and the agent chat history.
    #    reset_chat_session rotates the KV-cache id and clears the chat_history.
    self.chat_widget.clear()
    self.agent.reset_chat_session()

    # 3. Replay display rows into the chat widget.
    for row in rows:
        if row.role in DISPLAY_ROLES:
            self.chat_widget.append_message(row.role, row.text)

    # 4. Replay model rows into the agent chat history.
    for row in rows:
        if row.role not in MODEL_ROLES:
            continue
        if not row.payload:
            continue
        try:
            message = ChatMessage.from_dict(row.payload)
        except Exception:
            logger.exception("Failed to decode model row %s; skipping", row.id)
            continue
        self.agent.chat_history.add_message(message)

    self.active_session_id = session_id
    self.status_bar.showMessage(
        f"Resumed session {session_id} ({len(rows)} rows).", 5000
    )
```

`DISPLAY_ROLES = {"user", "assistant", "tool_started", "tool_finished", "mutation", "error"}`
`MODEL_ROLES = {"assistant_model", "tool_model"}`

The new rows are written by `on_tool_finished` and `on_turn_finished` in
`main_window`. See §8.

## 8. Where the new rows are written

- `on_turn_finished` (assistant final text + tool calls): if the assistant
  message contains tool calls, also append a model row with
  `role="assistant_model"` and `payload=assistant_message.model_dump()`.
- `on_tool_finished` (one per executed tool call): also append a model row
  with `role="tool_model"` and `payload=tool_result_message.model_dump()`.
  The display row (`tool_finished` / `mutation` / `error`) is unchanged.

The `AgentWorker` exposes the typed `ChatMessage` objects to the GUI via a
new signal `turn_messages: Signal(list)` carrying the messages added during
the turn. `main_window` partitions them into display vs. model rows.

## 9. Streaming refactor

`ToolAgentsRunner` gains:

```python
def stream_turn(self, agent, user_message, *, ...) -> Iterator[dict]:
    """Yield chunks of the assistant response and execute tool calls."""
    # ... build chat_history messages from chat_history
    # stream from chat_agent.stream_step
    # for each finished tool result, append to chat_history
    # yield {"event": "chunk", "text": "..."} or {"event": "tool_start", ...} etc.
```

`AgentWorker.run_turn` consumes the iterator and emits the existing
`response_chunk` / `tool_started` / `tool_finished` signals. The post-hoc
QTimer throttle is deleted.

`GrcOpenAIChatAPI` already extends `OpenAIChatAPI` and supports
`get_streaming_response`. No provider changes are needed.

If streaming is unavailable in the runtime path, the runner falls back to
`run_turn` and emits the final text in a single chunk. This is the only
behavioral fallback.

## 10. Compact budget

`ChatHistory` does not ship a compactor. A new helper
`grc_agent.runtime.chat_history.compact_chat_history(chat_history, *, budget_chars)`
walks `chat_history.get_messages()` and replaces `ToolCallResultContent`
payloads (which are the biggest by far) with short placeholders until the
total char count is under budget. `TextContent` and `ToolCallContent` are
kept verbatim.

This replaces `GrcAgent.compact_history` and
`GrcAgent._proactive_compact_if_needed`. The "session" pseudo-role
heuristic is gone.

## 11. Trace / journal projection

`GraphHistoryJournal` continues to consume a list of dicts (its existing
shape). `agent.py` exposes:

```python
def chat_history_to_trace_dicts(chat_history: ChatHistory) -> list[dict]:
    return [
        {"role": _role_to_trace(m.role), "content": m.get_as_text(), ...}
        for m in chat_history.get_messages()
    ]
```

for the call sites that still need a dict projection.

## 12. Test plan

New `tests/agent/test_chat_history.py`:

- `test_chat_history_round_trip_with_tool_messages` — save/load preserves
  assistant tool calls and tool results.
- `test_render_model_messages_includes_reminder` — the reminder is the last
  message, with the custom role tag.
- `test_compact_chat_history_keeps_tool_calls_drops_results` — after
  compaction, `ToolCallContent` is intact and `ToolCallResultContent` is
  short.
- `test_resume_replays_tool_messages` — write a session with model rows,
  read it back, build a fresh `GrcAgent`, replay rows, verify
  `chat_history.get_messages()` contains the original tool messages.
- `test_stream_step_emits_chunks` — stub the provider, call
  `runner.stream_turn`, verify the expected sequence of chunk/tool events.

Updated:

- `tests/test_agent_loop_fixes.py` — replace any `self.history.append(...)`
  references with `chat_history.add_*` calls.
- `tests/sessions/test_store.py` — assert the new `payload`-bearing rows
  round-trip via `MessageRecord.payload`.
- `tests/gui/test_recent_sessions_dialog.py` — assert the new role
  constants are filtered correctly.

## 13. Documentation

- `docs/BLUEPRINT.md` § 3.4 (M11 session history): the resume path is
  described as replaying model rows from `payload`, not as filtering by
  display roles.
- `docs/MODEL_CONTEXT_BIBLE.md` is generated; it will be regenerated
  against the new code. No hand-edits.
- `docs/CHANGELOG.md` adds a "ChatHistory refactor" entry noting the
  resume-bug fix and the real-token streaming.

## 14. Risk register

- **Pydantic datetime round-trip.** `ChatHistory.save_to_json` uses a
  custom encoder; `load_from_json` calls `datetime.fromisoformat` for both
  `created_at` and `updated_at`. We must verify the encoder emits ISO-8601
  that `fromisoformat` accepts (microsecond precision is fine).
- **Provider streaming over llama.cpp.** If `stream_step` returns chunks
  with a different shape than `step` does, the fallback to `run_turn` must
  be safe. The runner is the boundary that owns this fallback.
- **Existing safety tests.** Tests for `change_graph` validation, journal
  rollback, and tool-call route validation must remain green. None of
  them touch `agent.history` directly; they go through `execute_tool`.
- **Eval gate.** The model-context bible is regenerated. Eval prompts
  should be unchanged at the user-visible level; we re-run the eval
  matrix after the refactor.

## 15. Rollout

Single coordinated change. The shape change of `GrcAgent.history` is
internal — no public API surface depends on it. The DB schema is
backward-compatible (new role strings are added; old role strings still
parse).

After the change:

- `agent.history` attribute is gone.
- `ToolAgentsHistoryAdapter` is ~30 LOC.
- `workers.py` is ~120 LOC instead of 172.
- `_resolve_final_assistant_text` is deleted.
- `render_model_messages` is ~25 LOC instead of 100.
- The resume bug is fixed.
- Real-token streaming is on by default.
