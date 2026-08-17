"""Per-turn reasoning-trace persistence for the GRC agent.

Each agent turn (a user prompt → final response OR an abort OR an error) is
recorded as one row in the ``turn_traces`` SQLite table (same chat-sessions
DB as ``sessions``). A trace captures:

- ``run_id`` / ``conversation_id`` from pydantic-ai's ``AgentRun``
- ``provider`` / ``model`` / ``base_url`` snapshot taken at turn start (so a
  live provider swap mid-turn is still attributable to the right backend)
- ``system_prompt_hash`` (sha256 of the resolved ``ModelRequest.instructions``)
- ``user_prompt`` and ``origin_page_path``
- ``started_at`` / ``ended_at`` / ``duration_ms`` (monotonic epoch)
- ``events`` — JSON array of *meaningful* structured events in arrival order:
  ``part_start`` (TextPart/ThinkingPart/ToolCallPart/NativeToolCallPart/
  NativeToolReturnPart), ``tool_call`` (FunctionToolCallEvent),
  ``tool_result`` (FunctionToolResultEvent), and ``error``. Per-token
  ``PartDeltaEvent``s are deliberately NOT recorded — their content is
  already consolidated into the ``ThinkingPart``/``TextPart`` content of the
  canonical ``ModelMessage`` history that the ``sessions.messages`` blob
  persists. Recording them separately would bloat the trace ~100x for zero
  additional information.
- ``final_output`` (run result, serialized)
- ``error`` (turn-level exception class + message, NULL on success)
- per-turn token usage (``input_tokens`` / ``output_tokens`` /
  ``reasoning_tokens`` / ``total_tokens``)

``TraceRecorder`` is fed in-memory during the streaming turn (cheap, no I/O).
``save_turn_trace`` is dispatched via ``asyncio.to_thread`` once at the end —
mirroring ``chat_sidebar._save_history``'s pattern (worker-thread SQLite I/O
on the WAL-enabled DB). Cascade delete on ``sessions.id`` means a cleared or
deleted session takes its traces with it — no separate cleanup, no
resurrection risk beyond the clear-generation guard in ``_save_trace``.
"""

import hashlib
import json
import logging
import time
from collections import deque
from typing import Any

from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartStartEvent,
)

from .db import _conn, init_db

_log = logging.getLogger(__name__)

# Bound the in-memory event buffer so a pathological agent turn cannot grow
# memory without limit. Mirrors exec_monitor.py's bounded-deque convention;
# dropped events are summarized as a single ``{kind: "dropped", count: N}``
# trailing record so the drop is explicit, never silent.
_MAX_EVENTS = 5000


def _event_to_record(event: Any) -> dict[str, Any] | None:
    """Map a pydantic-ai stream event to a JSON-serializable record dict.

    Returns None for events we deliberately do NOT record (``PartDeltaEvent``
    per-token deltas — redundant with the consolidated parts in the saved
    message history, see module docstring).
    """
    if isinstance(event, PartStartEvent):
        part = event.part
        pcls = part.__class__.__name__
        rec: dict[str, Any] = {
            "kind": "part_start",
            "part_type": pcls,
            "index": event.index,
        }
        if pcls in ("ToolCallPart", "NativeToolCallPart"):
            rec["tool_name"] = getattr(part, "tool_name", None)
            rec["tool_call_id"] = getattr(part, "tool_call_id", None)
            rec["args"] = getattr(part, "args", None)
        elif pcls == "NativeToolReturnPart":
            rec["tool_call_id"] = getattr(part, "tool_call_id", None)
            rec["content"] = str(getattr(part, "content", ""))
        elif pcls in ("TextPart", "ThinkingPart"):
            # First 120 chars only — the full content is in the saved
            # messages blob; this preview is enough to identify the part in
            # the trace without bloating it.
            rec["content_preview"] = (getattr(part, "content", "") or "")[:120]
        return rec
    if isinstance(event, FunctionToolCallEvent):
        return {
            "kind": "tool_call",
            "tool_name": event.part.tool_name,
            "tool_call_id": event.part.tool_call_id,
            "args": event.part.args,
        }
    if isinstance(event, FunctionToolResultEvent):
        part = event.part
        return {
            "kind": "tool_result",
            "tool_call_id": event.tool_call_id,
            "content": str(getattr(part, "content", "")),
            "outcome": getattr(part, "outcome", None),
            "part_type": part.__class__.__name__,
        }
    return None


def _format_exc(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    return f"{type(exc).__name__}: {exc}"


def _extract_input_and_prompt_hash(messages: list[Any]) -> tuple[int, str]:
    """Walk a message list once and return (input_tokens, system_prompt_hash).

    input_tokens is the LAST ModelResponse's input — the context size at the
    end of the turn, which is what the sidebar's context label displays.
    Per-turn output/reasoning/total come from the run's own aggregated usage
    instead (see TraceRecorder.finalize): all_messages() includes prior
    turns' responses, so summing them here would leak across turns, and the
    last-response-only output undercounts multi-request turns.
    system_prompt_hash is sha256 of the first ModelRequest.instructions found.
    """
    input_tokens = 0
    system_prompt_hash = ""
    for m in messages:
        mcls = m.__class__.__name__
        if mcls == "ModelResponse":
            u = getattr(m, "usage", None)
            if not u:
                continue
            input_tokens = getattr(u, "input_tokens", 0) or 0
        elif mcls == "ModelRequest" and not system_prompt_hash:
            instr = getattr(m, "instructions", None)
            if instr:
                system_prompt_hash = hashlib.sha256(instr.encode("utf-8")).hexdigest()
    return input_tokens, system_prompt_hash


class TraceRecorder:
    """Per-turn trace collector. Fed in-memory by the stream handlers during
    ``_run_agent_turn``; ``finalize`` produces the row for ``save_turn_trace``.

    Held outside ``_StreamCtx`` because the trace is orthogonal to the GTK
    rendering state — a trace is captured even when the canvas is shutting
    down (``_shutting_down`` skips widget ops but not trace capture).
    """

    __slots__ = (
        "session_id",
        "provider",
        "model",
        "base_url",
        "user_prompt",
        "origin_page_path",
        "started_at",
        "_events",
        "_dropped",
    )

    def __init__(
        self,
        *,
        session_id: int | None,
        provider: str,
        model: str,
        base_url: str,
        user_prompt: str,
        origin_page_path: str | None,
    ) -> None:
        self.session_id = session_id
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.user_prompt = user_prompt
        self.origin_page_path = origin_page_path
        self.started_at = time.time()
        self._events: deque[dict[str, Any]] = deque()
        self._dropped = 0

    def on_event(self, event: Any) -> None:
        """Record a structured event from pydantic-ai's ``iter()`` stream."""
        rec = _event_to_record(event)
        if rec is None:
            return
        if len(self._events) >= _MAX_EVENTS:
            self._events.popleft()
            self._dropped += 1
        self._events.append(rec)

    def record_error(self, stage: str, exc: BaseException) -> None:
        """Record a turn-level error (caught by _run_agent_turn's catch blocks)."""
        self._events.append(
            {
                "kind": "error",
                "stage": stage,
                "exc_type": type(exc).__name__,
                "message": str(exc),
            }
        )

    def finalize(
        self,
        run: Any | None,
        exc: BaseException | None = None,
    ) -> dict[str, Any]:
        """Produce the row dict for ``save_turn_trace``.

        Reads run_id/conversation_id/messages/usage off the run object when
        available. Safe to call with ``run=None`` (e.g. the agent was None
        or the iter() context never entered) and with ``exc`` set for a
        failed turn.
        """
        ended_at = time.time()
        run_id: str | None = None
        conversation_id: str | None = None
        messages: list[Any] = []
        if run is not None:
            run_id = getattr(run, "run_id", None)
            conversation_id = getattr(run, "conversation_id", None)
            try:
                messages = list(run.all_messages())
            except Exception:
                messages = []

        input_tokens, system_prompt_hash = _extract_input_and_prompt_hash(messages)

        # Per-turn output/reasoning/total come from the run's own aggregated
        # usage — pydantic-ai sums every request in THIS run (verified: a
        # tool-calling turn's run.usage.output_tokens equals the sum of both
        # responses, while all_messages() also contains prior turns' responses
        # and the last-response-only extraction undercounts). input_tokens
        # keeps the last-response semantic above: it is the context size at
        # the end of the turn, which is what the sidebar's context label shows.
        output_tokens = reasoning_tokens = total_tokens = 0
        if run is not None:
            usage = getattr(run, "usage", None)
            if usage is not None:
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                total_tokens = getattr(usage, "total_tokens", 0) or 0
                details = getattr(usage, "details", None) or {}
                reasoning_tokens = details.get("reasoning_tokens", 0) or 0

        final_output = ""
        result = getattr(run, "result", None) if run is not None else None
        if result is not None:
            try:
                output = result.output
                final_output = (
                    output if isinstance(output, str) else json.dumps(output, default=str)
                )
            except Exception:
                final_output = ""

        events_list = list(self._events)
        if self._dropped:
            events_list.append({"kind": "dropped", "count": self._dropped})

        return {
            "session_id": self.session_id,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "system_prompt_hash": system_prompt_hash,
            "user_prompt": self.user_prompt,
            "origin_page_path": self.origin_page_path,
            "started_at": self.started_at,
            "ended_at": ended_at,
            "duration_ms": int((ended_at - self.started_at) * 1000),
            "events": json.dumps(events_list, default=str),
            "final_output": final_output,
            "error": _format_exc(exc),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
        }


_INSERT_TURN_TRACE_SQL = """
INSERT INTO turn_traces (
    session_id, run_id, conversation_id, provider, model, base_url,
    system_prompt_hash, user_prompt, origin_page_path, started_at, ended_at,
    duration_ms, events, final_output, error,
    input_tokens, output_tokens, reasoning_tokens, total_tokens
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def save_turn_trace(row: dict[str, Any]) -> int | None:
    """Persist one finalized ``TraceRecorder`` row.

    Dispatched via ``asyncio.to_thread`` from ``chat_sidebar._save_trace``.
    Returns the new trace id, or None if the parent session is no longer on
    disk (cleared/deleted concurrently — the clear-generation guard in
    ``_save_trace`` already covered that case; this is belt-and-suspenders
    so a late save can't raise ``IntegrityError`` against a missing parent).
    """
    init_db()
    session_id = row.get("session_id")
    if session_id is None:
        return None
    with _conn() as conn:
        # FK would also reject this via IntegrityError, but the explicit
        # SELECT avoids a thrown/caught exception on the common race.
        parent = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not parent:
            return None
        cursor = conn.execute(
            _INSERT_TURN_TRACE_SQL,
            (
                session_id,
                row["run_id"],
                row["conversation_id"],
                row["provider"],
                row["model"],
                row["base_url"],
                row["system_prompt_hash"],
                row["user_prompt"],
                row["origin_page_path"],
                row["started_at"],
                row["ended_at"],
                row["duration_ms"],
                row["events"],
                row["final_output"],
                row["error"],
                row["input_tokens"],
                row["output_tokens"],
                row["reasoning_tokens"],
                row["total_tokens"],
            ),
        )
        conn.commit()
        return cursor.lastrowid


def delete_turn_trace(trace_id: int) -> None:
    """Delete a single trace row. Used by ``_save_trace``'s clear-generation
    guard to undo a late insert that landed after a global Clear History."""
    init_db()
    with _conn() as conn:
        conn.execute("DELETE FROM turn_traces WHERE id = ?", (trace_id,))
        conn.commit()


def get_turn_traces_for_session(session_id: int) -> list[dict[str, Any]]:
    """Load every trace row for a session, oldest first (turn arrival order).
    Used by tests and any future trace-viewer UI — not on any hot path."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM turn_traces WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]
