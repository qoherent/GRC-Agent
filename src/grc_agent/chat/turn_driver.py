"""Turn-driver mixin for ChatSidebar.

Owns the agent turn loop and its satellites: `_run_agent_turn` (the async
driver - deferred-approval resume loop, streaming, and the success /
cancel / error exit paths), `_send_fix_when_free` (queued auto-fix
dispatch once the loop is idle), `notify_run_failure` (run-failure
notification; the unused log payload is documented design - the agent
reads the log on demand), `_on_chat_task_done` (unhandled-exception log
and busy-release backstop), and `_recover_history_after_failure`
(salvages a failed run's messages into the history at the failure
boundary). Moved out of `chat_sidebar.py` by the decomposition follow-up
(U5) - a move-only extraction. No GTK symbols: this mixin is testable
without a display.

Host-attribute contract - assumes on the full ChatSidebar instance:
- turn state: `_agent`, `_agent_mode`, `_active_run`, `_busy`,
  `_idle_event`, `_shutting_down`, `_fix_task`, `_active_provider`,
  `_message_history`, `_active_session_id`, `current_page`
- busy/lifecycle methods: `_set_busy`, `_model_wait_start`,
  `_model_wait_stop`, `_clear_generation`, `_track_background_task`
- render/stream methods: `_start_agent_message`, `_stream_request`,
  `_stream_tools`, `_flush_streaming`, `_close_text`, `_close_thinking`,
  `_replace_streaming_turn`, `_render_history`, `_render_markdown_to_box`,
  `_scroll_to_bottom`, `_append_error`, `_archive_truncated_thinking`
- session/satellite methods: `_save_history`, `_remember_user_message`,
  `_update_context_label`, `_show_implement_plan_if_ready`, `set_status`,
  `send_message`, `_send_fix_when_free`, `_request_approvals`,
  `_get_effective_path`, `_model_build_error`, `_recover_history_after_failure`
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import UserContent
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
)
from pydantic_graph import End

from ..db import (
    conversation_id_for_session,
    save_session,
    user_request,
)
from ..settings import load_settings, resolve_key
from ..ui.providers import PROVIDER_API_KEY as _PROVIDER_API_KEY
from ..ui.providers import PROVIDER_KEY_OPTIONAL as _PROVIDER_KEY_OPTIONAL
from ..ui.providers import PROVIDER_LABELS as _PROVIDER_LABELS
from .errors import _format_turn_error
from .history import (
    _clean_message_history_for_new_turn,
    _without_truncated_thinking_tail,
)
from .stream_view import _StreamCtx

_log = logging.getLogger(__name__)


class TurnDriverMixin:
    async def _recover_history_after_failure(
        self,
        active_run: Any,
        *,
        session_id: int | None,
        agent_mode: str,
        fallback_text: str | Sequence[UserContent],
    ) -> bool:
        """Salvage a failed turn's messages into `_message_history`.

        Returns True when a truncated-thinking tail was archived and dropped, so
        the caller can explain that specific failure. The cancel and exception
        paths of `_run_agent_turn` each carried a verbatim copy of this sequence;
        with no run to salvage from, or if the salvage itself fails, the user's
        prompt is re-remembered so it is not lost from the history.
        """
        if active_run is None:
            self._remember_user_message(fallback_text)
            return False
        try:
            failed_messages = _clean_message_history_for_new_turn(active_run.all_messages())
            cleaned_messages, had_truncated_thinking = _without_truncated_thinking_tail(
                failed_messages
            )
            archived = False
            if had_truncated_thinking and await self._archive_truncated_thinking(
                failed_messages, session_id, agent_mode
            ):
                failed_messages = cleaned_messages
                archived = True
            self._message_history = failed_messages
            return archived
        except Exception:
            self._remember_user_message(fallback_text)
            return False

    def notify_run_failure(self, return_code: int, log_text: str) -> None:  # noqa: ARG002
        """Called by exec_monitor when a flowgraph run fails. Sends a short
        notification to the agent so it can decide whether to investigate via
        ``get_run_log`` and propose a fix — replacing the old Yes/No bubble
        that injected the full log as a prompt.

        The full log is NOT injected here — the agent reads it on demand via
        the ``get_run_log`` tool (one source of truth, structured tool result
        instead of a prompt blob).
        """
        _log.info("notify_run_failure: code=%d, log=%d chars", return_code, len(log_text))
        origin_page = self.current_page
        prompt = (
            f"Flowgraph run failed (return code {return_code}). "
            "Use the get_run_log tool to read the console output and diagnose the error."
        )
        self._fix_task = self._track_background_task(
            asyncio.ensure_future(self._send_fix_when_free(prompt, origin_page))
        )

    async def _send_fix_when_free(self, text: str, origin_page: Any) -> None:
        """Wait out any in-flight agent turn, then send `text` as the next
        user message in the ORIGIN page's session — not whatever page happens
        to be current when the await returns.

        The await yields control to the gbulb loop, which can process a
        notebook ``switch-page`` in the meantime. Without the origin-page
        capture, the fix would silently dispatch against whatever page is
        current when the await returns, "fixing" the wrong flowgraph (H2).
        On a detected switch we surface a status message instead of acting
        on the wrong target — same one-rule shape as _run_agent_turn's
        ``origin_page`` guard.
        """
        while self._busy:
            await self._idle_event.wait()
        if self.current_page is not origin_page:
            self.set_status(
                "Auto-fix cancelled \u2014 you switched flowgraphs. Re-open the failed flowgraph and try again.",
                error=True,
            )
            return
        if not self.send_message(text):
            _log.warning("Failed to dispatch auto-fix message despite idle event")
            self.set_status("Flowgraph run failed. Check console or send message to diagnose.", error=True)

    async def _run_agent_turn(self, prompt: str | Sequence[UserContent]) -> None:  # noqa: C901
        rich_rendered = False
        origin_page = self.current_page
        origin_gen = self._clear_generation
        origin_agent_mode = self._agent_mode
        ctx: _StreamCtx | None = None
        active_run: Any = None
        try:
            if self._agent is None:
                self._append_error("No agent configured.")
                return

            # Create the session row off the unified loop (the same
            # asyncio.to_thread rule _save_history follows — never a blocking
            # SQLite INSERT on the GLib loop) BEFORE capturing the origin
            # session id, so conversation grouping, the plan handoff, and the
            # archive paths all see it. Payload: the user prompt included
            # inline — NOT by mutating _message_history. agent.iter(prompt, ...)
            # appends the prompt to the canonical history itself; if we pre-loaded
            # it into _message_history here, the success path's
            # run.result.all_messages() would contain the prompt TWICE (once
            # from our pre-load, once from pydantic-ai's own append) and
            # _render_history() would display it twice. Keeping
            # _message_history clean until the run completes avoids that
            # duplication (M2 fix).
            if self._active_session_id is None:
                path = self._get_effective_path()
                if path:
                    try:
                        history_with_prompt = [
                            *self._message_history,
                            user_request(prompt),
                        ]
                        self._active_session_id = await asyncio.to_thread(
                            save_session, None, path, history_with_prompt
                        )
                    except Exception as e:
                        _log.error("Failed to create new session in database: %s", e)
            origin_session_id = self._active_session_id

            try:
                cfg = load_settings()
                configured_provider = cfg.get("provider", self._active_provider)
            except Exception:
                configured_provider = self._active_provider

            key_var = _PROVIDER_API_KEY.get(configured_provider)
            if key_var and configured_provider not in _PROVIDER_KEY_OPTIONAL:
                key_val = resolve_key(key_var)
                if not key_val:
                    provider_title = _PROVIDER_LABELS.get(
                        configured_provider, configured_provider
                    )
                    self._append_error(
                        f"API key for {provider_title} ({key_var}) is not set. "
                        "Open Preferences (Ctrl+,) to configure your API key."
                    )
                    return

            if configured_provider == "openai_codex":
                from ..providers.openai_codex import is_signed_in as codex_is_signed_in

                if not codex_is_signed_in():
                    self._append_error(
                        "Not signed in to ChatGPT. Open Preferences (Ctrl+,) and click 'Sign in with ChatGPT'."
                    )
                    return

            if self._model_build_error:
                provider_title = _PROVIDER_LABELS.get(
                    configured_provider, configured_provider
                )
                self._append_error(
                    f"Cannot run {provider_title}: {self._model_build_error}. "
                    "Open Preferences (Ctrl+,) to configure."
                )
                return

            ctx = _StreamCtx(self._start_agent_message())

            # Human-in-the-loop approval loop: change_graph requires approval
            # (pydantic-ai requires_approval=True), so a run can END with a
            # DeferredToolRequests output before the model's final answer.
            # Persist that run's messages, surface the approval card(s), then
            # resume the SAME turn with the native deferred-tool results
            # (ToolApproved/ToolDenied) until the run reaches a final output.
            deferred_results: DeferredToolResults | None = None
            turn_required_approval = False
            while True:
                async with self._agent.iter(
                    prompt if deferred_results is None else None,
                    message_history=self._message_history,
                    deferred_tool_results=deferred_results,
                    deps=self._flowgraph_proxy,
                    # Groups this turn's StepPersistence runs/events/snapshots
                    # under the active chat session — the same conversation id
                    # db.py's cleanup SQL matches. Inherited by message_history
                    # on later turns, but passed explicitly every turn as one
                    # uniform rule (runs before a session row exists — e.g. a
                    # failed first send — fall back to pydantic-ai's fresh id
                    # and are simply ungrouped).
                    conversation_id=(
                        conversation_id_for_session(self._active_session_id)
                        if self._active_session_id is not None
                        else None
                    ),
                ) as run:
                    active_run = run
                    self._active_run = run
                    node = run.next_node
                    while node is not None and not isinstance(node, End):
                        if Agent.is_model_request_node(node):
                            self._model_wait_start()
                            try:
                                await self._stream_request(ctx, node, run)
                            finally:
                                self._model_wait_stop()
                        elif Agent.is_call_tools_node(node):
                            self._close_text(ctx)
                            self._close_thinking(ctx)
                            await self._stream_tools(ctx, node, run)
                        self._scroll_to_bottom()
                        node = await run.next(node)
                        self._update_context_label()

                if run.result is None or not isinstance(
                    run.result.output, DeferredToolRequests
                ):
                    break
                # A change_graph call is pending approval. The run's messages
                # (including the unapproved call) are persisted now so a crash
                # mid-approval keeps the transcript; the next turn strips the
                # unfulfilled trailing call. The approval cards live in the
                # streaming row and are transient — the final transcript is
                # rebuilt from canonical history below.
                self._message_history = run.result.all_messages()
                await self._save_history()
                deferred_results = await self._request_approvals(ctx, run.result.output)
                prompt = None
                turn_required_approval = True

            if run.result is not None:
                self._message_history = run.result.all_messages()
                await self._save_history()
                if turn_required_approval:
                    # The turn spanned an approval pause; rebuild the
                    # transcript from canonical history so the first run's
                    # tool calls (and the resumed final answer) both render —
                    # the streaming row carried the transient approval cards.
                    self._render_history()
                else:
                    self._replace_streaming_turn(ctx, run.result.new_messages())
                if origin_agent_mode == "planner" and origin_session_id is not None:
                    await self._show_implement_plan_if_ready(origin_session_id)
                rich_rendered = True
        except asyncio.CancelledError:
            if self.current_page is origin_page and self._clear_generation == origin_gen:
                await self._recover_history_after_failure(
                    active_run,
                    session_id=origin_session_id,
                    agent_mode=origin_agent_mode,
                    fallback_text=prompt,
                )
                # Tracked like every other fire-and-forget: a bare
                # ensure_future here was invisible to _cancel_background_tasks,
                # so a racing clear orphaned the handle (U3/F-04).
                self._track_background_task(asyncio.ensure_future(self._save_history()))
                self._append_error("[aborted]", style="aborted")
                rich_rendered = True
            raise
        except Exception as e:
            _log.exception("agent run failed")
            if self.current_page is origin_page:
                truncated_thinking_archived = await self._recover_history_after_failure(
                    active_run,
                    session_id=origin_session_id,
                    agent_mode=origin_agent_mode,
                    fallback_text=prompt,
                )
                await self._save_history()
                if truncated_thinking_archived:
                    self._append_error(
                        "Model reasoning repeated until the provider output limit. "
                        "The full failed trace was archived, and the unusable repetition was removed "
                        "from active context. Send Continue to resume from the completed tool steps."
                    )
                else:
                    self._append_error(_format_turn_error(e))
                rich_rendered = True
        finally:
            self._active_run = None
            self._update_context_label()
            # Paint any throttled-but-unflushed tail before deciding whether to
            # markdown-render, so an error/cancel mid-part never leaves the live
            # bubble stuck at a ~33ms-stale snapshot (the per-token throttle can
            # hold back the last chunk when the stream raises before a flush).
            # Skip during app shutdown to avoid widget ops on mid-destroy
            # widgets — the window's `destroy` signal fires _shutdown, which
            # sets _shutting_down before stop_chat() cancels this task (L7).
            if self._shutting_down:
                return  # noqa: B012
            if ctx is not None:
                self._flush_streaming(ctx, force=True)
            if (
                ctx is not None
                and not rich_rendered
                and ctx.full_raw_text
                and self.current_page is origin_page
            ):
                self._render_markdown_to_box(ctx.box, str(ctx.full_raw_text))
            self._set_busy(False)
            self._scroll_to_bottom()

    def _on_chat_task_done(self, task: asyncio.Task) -> None:
        """Defence in depth: log any unhandled exception that escaped the
        _run_agent_turn try/except (e.g. a BaseException), and guarantee the
        busy UI is released. The finally in _run_agent_turn already resets
        busy for normal paths."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _log.error("chat task ended with unhandled exception: %s", exc, exc_info=exc)
        if self._busy:
            self._set_busy(False)
        # Belt-and-braces: the turn loop's finally already stops the timer
        # (including on cancellation, which unwinds through it); this catches
        # any future path that ends a task without unwinding the loop. Note
        # task.cancelled() returns early above — a cancelled task's timer is
        # stopped by that finally, not here.
        self._model_wait_stop()
