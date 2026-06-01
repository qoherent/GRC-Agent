import logging
from typing import Any
from PySide6.QtCore import QObject, Signal

from grc_agent.toolagents_runtime import ToolAgentsRunner

logger = logging.getLogger(__name__)


class AgentWorker(QObject):
    """Worker object to run grc-agent turns inside a background QThread.
    
    This object has zero direct visual UI references to enforce a clean 
    signal-only boundary with the main GUI thread.
    """
    
    started = Signal()
    tool_started = Signal(str, str)     # (tool_name, arguments_json_str)
    tool_finished = Signal(str, str)    # (tool_name, result_json_str)
    response_chunk = Signal(str)        # Streamed token/character chunks
    turn_finished = Signal(dict)        # Final turn completion payload

    def __init__(self, agent: Any, user_message: str, provider_config: Any, parent: QObject = None) -> None:
        super().__init__(parent)
        self.agent = agent
        self.user_message = user_message
        self.provider_config = provider_config
        self.runner = None
        self._is_cancelled = False

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
                # To support simulated/real streaming updates in the chat window,
                # emit assistant text in chunks.
                text = result.get("assistant_text", "")
                if text:
                    chunk_size = 64
                    for i in range(0, len(text), chunk_size):
                        if self._is_cancelled:
                            break
                        self.response_chunk.emit(text[i : i + chunk_size])
                
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

    def _emit_tool_started(self, name: str, args: dict[str, Any]) -> None:
        if self._is_cancelled:
            raise RuntimeError("Worker execution cancelled by user request.")
        self.tool_started.emit(name, str(args))

    def _emit_tool_finished(self, name: str, result: Any) -> None:
        self.tool_finished.emit(name, str(result))

    def cancel(self) -> None:
        """Abort execution cooperatively and forcefully close HTTP client socket connections."""
        self._is_cancelled = True
        runner = self.runner
        if runner and hasattr(runner, "provider") and runner.provider:
            try:
                # GrcOpenAIChatAPI has an underlying self.client (openai.OpenAI client)
                client = getattr(runner.provider, "client", None)
                if client:
                    client.close()
                    logger.info("Forcefully closed HTTP client socket for active worker.")
            except Exception as e:
                logger.warning(f"Error during forceful close of HTTP client socket: {e}")
