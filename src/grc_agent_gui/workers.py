"""Qt worker that runs a ToolAgents-backed turn in a background QThread.

This is the GUI-side adapter around :class:`grc_agent.toolagents_runtime.ToolAgentsRunner`.
It keeps a strict signal-only boundary with the main GUI thread.

Consumes ``ToolAgentsRunner.stream_turn`` and emits a ``response_chunk``
signal as each round's event arrives (round-level streaming): the
assistant's text for a tool-calling round (including any ``<think>``
reasoning) appears as soon as that round completes, rather than only the
final round's text appearing after the whole turn (which may include
several tool round-trips) finishes.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from grc_agent.chat_roles import ASSISTANT_MODEL_ROLE, TOOL_MODEL_ROLE, USER_MODEL_ROLE
from grc_agent.toolagents_runtime import ToolAgentsRunner
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class AgentWorker(QObject):
    """Worker object to run grc-agent turns inside a background QThread.

    Emits these signals to the GUI:

    * ``started`` — when the turn has begun.
    * ``tool_started(name, args_json)`` — before each tool call.
    * ``tool_finished(name, result_json)`` — after each tool call.
    * ``response_chunk(text)`` — one round's model output text, emitted as
      each round completes (or a fallback emitted once, for the final-only
      error paths that never produce a round chunk).
    * ``model_message_added(role, payload_json)`` — typed ``ChatMessage``
      serialized as JSON, for the model-history rows the resume path
      replays. ``role`` is one of ``assistant_model`` / ``tool_model``.
    * ``turn_finished(result)`` — final structured result.
    """

    started = Signal()
    tool_started = Signal(str, str)
    tool_finished = Signal(str, str)
    response_chunk = Signal(str)
    model_message_added = Signal(str, str)
    turn_finished = Signal(dict)
    backend_unreachable = Signal(dict)

    def __init__(
        self,
        agent: Any,
        user_message: str,
        provider_config: Any,
        parent: QObject | None = None,
        *,
        on_backend_unreachable: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.agent = agent
        self.user_message = user_message
        self.provider_config = provider_config
        self.runner: ToolAgentsRunner | None = None
        self._is_cancelled = False
        # Cooperative cancellation token: set by `cancel()` from the main
        # thread, checked cooperatively by the runner inside its stream
        # loop. Replaces the old approach of forcibly closing the HTTP
        # client socket, which made cancellation non-cooperative and
        # could leave the SSOT desynced from the in-memory chat history.
        self._cancel_event = threading.Event()
        # Wire the cross-thread callback via a Qt signal so the
        # GUI-level handler always runs on the main thread.
        if on_backend_unreachable is not None:
            self.backend_unreachable.connect(on_backend_unreachable)

    def run_turn(self) -> None:
        self.started.emit()

        try:
            self.runner = ToolAgentsRunner(self.provider_config)
        except Exception as exc:
            logger.exception("AgentWorker failed to initialize ToolAgentsRunner")
            self.turn_finished.emit(
                {
                    "ok": False,
                    "error_type": "runner_init_error",
                    "assistant_text": (
                        f"Background worker failed to initialize execution runner: {exc}"
                    ),
                }
            )
            return

        try:
            if self._is_cancelled:
                raise RuntimeError("Worker execution cancelled before starting.")

            result: dict[str, Any] = {}
            chunk_emitted = False
            for event in self.runner.stream_turn(
                self.agent,
                self.user_message,
                on_tool_start=self._emit_tool_started,
                on_tool_end=self._emit_tool_finished,
                cancel_event=self._cancel_event,
            ):
                if self._is_cancelled:
                    break
                kind = event.get("event")
                if kind == "model_message":
                    role = event.get("role")
                    payload = event.get("payload")
                    if (
                        role in (USER_MODEL_ROLE, ASSISTANT_MODEL_ROLE, TOOL_MODEL_ROLE)
                        and payload is not None
                    ):
                        self.model_message_added.emit(role, json.dumps(payload, sort_keys=True))
                elif kind == "chunk":
                    text = event.get("text", "")
                    if text:
                        self.response_chunk.emit(text)
                        chunk_emitted = True
                elif kind == "final":
                    result = event.get("result", {})

            if not self._is_cancelled:
                # A few final-only error paths (backend unreachable, safety
                # ceiling) never yield a "chunk" event; fall back to the
                # result's assistant_text so the chat bubble is never empty.
                if not chunk_emitted:
                    text = result.get("assistant_text", "")
                    if text:
                        self.response_chunk.emit(text)
                self.turn_finished.emit(result)
                # When the backend is unreachable, the GUI must surface
                # the typed hint into the chat bubble *and* reset the
                # chat input state to match the degraded mode.  The
                # ``backend_unreachable`` signal ensures these GUI
                # mutations always run on the main thread.
                if result.get("error_type") == "backend_unreachable":
                    self.backend_unreachable.emit(result)
            else:
                # TH-1 fix: a cancelled worker MUST still emit turn_finished
                # so MainWindow.on_turn_finished fires and calls
                # cleanup_thread(). Without this, the QThread stays alive
                # and the next start_generation hits the "thread still
                # running" guard — locking the user out of further prompts.
                self.turn_finished.emit(
                    {"ok": False, "error_type": "cancelled", "assistant_text": ""}
                )
        except Exception as exc:
            logger.exception("AgentWorker failed during turn execution")
            self.turn_finished.emit(
                {
                    "ok": False,
                    "error_type": "worker_error",
                    "assistant_text": (
                        f"An execution error occurred in the background worker: {exc}"
                    ),
                }
            )
        finally:
            self.runner = None

    def _emit_tool_started(self, name: str, args: dict[str, Any]) -> None:
        self.tool_started.emit(name, json.dumps(args, sort_keys=True, default=str))

    def _emit_tool_finished(self, name: str, result: Any) -> None:
        if self._is_cancelled:
            return
        self.tool_finished.emit(name, json.dumps(result, sort_keys=True, default=str))

    def cancel(self) -> None:
        """Cooperatively cancel the in-flight turn.

        Sets the cancel event so ``ToolAgentsRunner._fetch_model_response``
        notices at the next chunk boundary and breaks the stream
        cleanly.  No hard-kill of the HTTP client — that was
        the source of SSOT/desync races (worker thread dies mid-
        event-loop while Qt-queued ``sessions_store.append`` slots
        haven't drained yet).

        The ``self._is_cancelled`` flag is kept as a fast-path for the
        consumer loop in ``run_turn`` so the generator is torn down
        without waiting for the next chunk to arrive.
        """
        self._is_cancelled = True
        self._cancel_event.set()


__all__ = ["AgentWorker"]
