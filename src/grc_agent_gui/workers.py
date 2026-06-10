"""Qt worker that runs a ToolAgents-backed turn in a background QThread.

This is the GUI-side adapter around :class:`grc_agent.toolagents_runtime.ToolAgentsRunner`.
It keeps a strict signal-only boundary with the main GUI thread.

The streaming path uses ``ChatToolAgent.stream_step`` to deliver model
tokens as they arrive, replacing the previous post-hoc 16-char QTimer
throttle. When streaming is not available from the provider, the worker
falls back to ``ToolAgentsRunner.run_turn`` and emits the final text in
one chunk.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

from grc_agent.session_roles import (
    ASSISTANT_MODEL_ROLE,
    TOOL_MODEL_ROLE,
    chat_message_payload,
)
from grc_agent.toolagents_runtime import ToolAgentsRunner
from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer, Signal, Slot
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
)

logger = logging.getLogger(__name__)


class AgentWorker(QObject):
    """Worker object to run grc-agent turns inside a background QThread.

    Emits these signals to the GUI:

    * ``started`` — when the turn has begun.
    * ``tool_started(name, args_json)`` — before each tool call.
    * ``tool_finished(name, result_json)`` — after each tool call.
    * ``response_chunk(text)`` — model output chunk (real or fallback).
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
        self._stream_timer: QTimer | None = None
        self._pending_result: dict[str, Any] | None = None

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

            result = self.runner.run_turn(
                self.agent,
                self.user_message,
                on_tool_start=self._emit_tool_started,
                on_tool_end=self._emit_tool_finished,
            )
            if not self._is_cancelled:
                self._emit_typed_messages(self.agent.chat_history.get_messages())
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

    def run_turn_streaming(self) -> None:
        """Stream a turn via ``ToolAgentsRunner.stream_turn`` (or fall back).

        Streaming requires the provider to expose
        ``ChatAPIProvider.get_streaming_response``. When that path is
        unavailable the worker falls back to the bounded non-streaming
        ``run_turn`` and emits the final text in one chunk.
        """
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

            events: Iterator[dict[str, Any]] = self.runner.stream_turn(
                self.agent,
                self.user_message,
                on_tool_start=self._emit_tool_started,
                on_tool_end=self._emit_tool_finished,
            )

            final: dict[str, Any] | None = None
            for event in events:
                if self._is_cancelled:
                    raise RuntimeError("Worker execution cancelled mid-stream.")
                kind = event.get("event")
                if kind == "chunk":
                    self.response_chunk.emit(str(event.get("text", "")))
                elif kind == "model_message":
                    self.model_message_added.emit(
                        str(event.get("role", "")),
                        json.dumps(event.get("payload", {}), sort_keys=True),
                    )
                elif kind == "final":
                    final = event.get("result")
            if final is None:
                final = {
                    "ok": False,
                    "error_type": "stream_no_final",
                    "assistant_text": "Stream completed without a final event.",
                }
            if not self._is_cancelled:
                if "model_messages" in final:
                    for role, payload in final["model_messages"]:
                        self.model_message_added.emit(role, json.dumps(payload, sort_keys=True))
                # Surface the typed backend-unreachable hint into the
                # chat bubble (the stream had no incremental tokens) and
                # notify the GUI so it can re-enter degraded mode.
                # The ``backend_unreachable`` signal ensures all GUI
                # mutations run on the main thread.
                if final.get("error_type") == "backend_unreachable":
                    text = final.get("assistant_text", "")
                    if text:
                        self.response_chunk.emit(text)
                    self.backend_unreachable.emit(final)
                self.turn_finished.emit(final)
        except AttributeError as exc:
            logger.warning("stream_turn unavailable, falling back to run_turn: %s", exc)
            self.run_turn()
            return
        except Exception as exc:
            logger.exception("AgentWorker failed during streaming turn")
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

    def _emit_typed_messages(self, messages: list[ChatMessage]) -> None:
        for message in messages:
            role = _role_to_model_role(message)
            if role is None:
                continue
            payload = chat_message_payload(message)
            self.model_message_added.emit(role, json.dumps(payload, sort_keys=True))

    def _emit_tool_started(self, name: str, args: dict[str, Any]) -> None:
        if self._is_cancelled:
            raise RuntimeError("Worker execution cancelled by user request.")
        self.tool_started.emit(name, json.dumps(args, sort_keys=True, default=str))

    def _emit_tool_finished(self, name: str, result: Any) -> None:
        if self._is_cancelled:
            return
        self.tool_finished.emit(name, json.dumps(result, sort_keys=True, default=str))

    @Slot()
    def _stop_stream_and_clear(self) -> None:
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer.deleteLater()
            self._stream_timer = None
        self._pending_result = None

    def cancel(self) -> None:
        self._is_cancelled = True
        QMetaObject.invokeMethod(self, "_stop_stream_and_clear", Qt.QueuedConnection)
        runner = self.runner
        if runner and hasattr(runner, "provider") and runner.provider:
            try:
                client = getattr(runner.provider, "client", None)
                if client:
                    client.close()
                    logger.info(
                        "Forcefully closed HTTP client socket for active worker."
                    )
            except Exception as exc:
                logger.warning(
                    f"Error during forceful close of HTTP client socket: {exc}"
                )


def _role_to_model_role(message: ChatMessage) -> str | None:
    if message.role == ChatMessageRole.Assistant:
        return ASSISTANT_MODEL_ROLE
    if message.role == ChatMessageRole.Tool:
        return TOOL_MODEL_ROLE
    return None


__all__ = ["AgentWorker"]
