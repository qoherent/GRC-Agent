import logging
from typing import Any

from grc_agent.toolagents_runtime import ToolAgentsRunner
from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer, Signal, Slot

logger = logging.getLogger(__name__)


class AgentWorker(QObject):
    """Worker object to run grc-agent turns inside a background QThread.

    This object has zero direct visual UI references to enforce a clean
    signal-only boundary with the main GUI thread.
    """

    started = Signal()
    tool_started = Signal(str, str)  # (tool_name, arguments_json_str)
    tool_finished = Signal(str, str)  # (tool_name, result_json_str)
    response_chunk = Signal(str)  # Streamed token/character chunks
    turn_finished = Signal(dict)  # Final turn completion payload

    def __init__(
        self,
        agent: Any,
        user_message: str,
        provider_config: Any,
        parent: QObject = None,
    ) -> None:
        super().__init__(parent)
        self.agent = agent
        self.user_message = user_message
        self.provider_config = provider_config
        self.runner = None
        self._is_cancelled = False
        # Throttled streaming state (audit 2.5).
        self._stream_timer: QTimer | None = None
        self._stream_buffer: str = ""
        self._stream_pos: int = 0
        self._stream_chunk_size: int = 16
        self._stream_interval_ms: int = 50
        self._pending_result: dict[str, Any] | None = None

    def run_turn(self) -> None:
        """Run the synchronous tool-use LLM execution loop."""
        self.started.emit()

        try:
            # Store the runner instance on the worker to allow forceful aborts on close/cancel
            self.runner = ToolAgentsRunner(self.provider_config)
        except Exception as e:
            logger.exception("AgentWorker failed to initialize ToolAgentsRunner")
            error_payload = {
                "ok": False,
                "error_type": "runner_init_error",
                "assistant_text": f"Background worker failed to initialize execution runner: {e}",
            }
            self.turn_finished.emit(error_payload)
            return

        try:
            if self._is_cancelled:
                raise RuntimeError("Worker execution cancelled before starting.")

            # Execute the turn through the ToolAgentsRunner framework
            result = self.runner.run_turn(
                self.agent,
                self.user_message,
                on_tool_start=self._emit_tool_started,
                on_tool_end=self._emit_tool_finished,
            )

            if not self._is_cancelled:
                text = result.get("assistant_text", "")
                if text:
                    self._pending_result = result
                    self._start_throttled_stream(text)
                else:
                    self.turn_finished.emit(result)
        except Exception as e:
            logger.exception("AgentWorker failed during turn execution")
            error_payload = {
                "ok": False,
                "error_type": "worker_error",
                "assistant_text": f"An execution error occurred in the background worker: {e}",
            }
            self.turn_finished.emit(error_payload)
        finally:
            self.runner = None

    def _start_throttled_stream(self, text: str) -> None:
        """Begin a QTimer-throttled emission of ``text`` as small chunks.

        The first chunk is emitted immediately so the user sees content
        without waiting for the first timer tick. Subsequent chunks are
        emitted at ``_stream_interval_ms`` intervals, which produces a
        typewriter-like effect for large responses without blocking the
        worker thread.
        """
        self._stream_buffer = text
        self._stream_pos = 0
        if not text:
            return

        timer = QTimer(self)
        timer.setInterval(self._stream_interval_ms)
        timer.timeout.connect(self._emit_next_chunk)
        self._stream_timer = timer
        timer.start()

    def _emit_next_chunk(self) -> None:
        if self._is_cancelled or self._stream_pos >= len(self._stream_buffer):
            self._stop_stream_timer()
            self._flush_turn_finished()
            return
        end = min(self._stream_pos + self._stream_chunk_size, len(self._stream_buffer))
        chunk = self._stream_buffer[self._stream_pos : end]
        self._stream_pos = end
        self.response_chunk.emit(chunk)
        if self._stream_pos >= len(self._stream_buffer):
            self._stop_stream_timer()
            self._flush_turn_finished()

    def _flush_turn_finished(self) -> None:
        """Emit the deferred turn_finished exactly once after streaming ends."""
        if self._pending_result is not None:
            result, self._pending_result = self._pending_result, None
            self.turn_finished.emit(result)

    def _stop_stream_timer(self) -> None:
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer.deleteLater()
            self._stream_timer = None

    def _emit_tool_started(self, name: str, args: dict[str, Any]) -> None:
        if self._is_cancelled:
            raise RuntimeError("Worker execution cancelled by user request.")
        self.tool_started.emit(name, str(args))

    def _emit_tool_finished(self, name: str, result: Any) -> None:
        if self._is_cancelled:
            return
        self.tool_finished.emit(name, str(result))

    @Slot()
    def _stop_stream_and_clear(self) -> None:
        """Safely stop the stream timer and clear pending results on the worker thread."""
        self._stop_stream_timer()
        self._pending_result = None

    def cancel(self) -> None:
        """Abort execution cooperatively and forcefully close HTTP client socket connections."""
        self._is_cancelled = True
        # Safely stop the throttled stream timer and clear the pending result
        # on the worker thread to avoid cross-thread QTimer stop violations.
        QMetaObject.invokeMethod(self, "_stop_stream_and_clear", Qt.QueuedConnection)

        runner = self.runner
        if runner and hasattr(runner, "provider") and runner.provider:
            try:
                # GrcOpenAIChatAPI has an underlying self.client (openai.OpenAI client)
                client = getattr(runner.provider, "client", None)
                if client:
                    client.close()
                    logger.info(
                        "Forcefully closed HTTP client socket for active worker."
                    )
            except Exception as e:
                logger.warning(
                    f"Error during forceful close of HTTP client socket: {e}"
                )
