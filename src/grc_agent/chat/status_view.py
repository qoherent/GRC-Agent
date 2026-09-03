# ruff: noqa: E402
"""The sidebar's status bar, context-usage label, and model-wait indicator.

Owns: ``set_status`` (with the sticky-error rule), the model-wait elapsed
timer, the context-window probe and cache, ``_current_messages`` (the one
authority for "what are the messages right now"), ``_update_context_label``
(usage extraction + cost + escalation classes), the RAG indexing poll, and
``_domain_label``.

Host-attribute contract — every method assumes the full ChatSidebar
instance and reads/edits these host attributes (created in ``__init__`` or
``_build_*``, which stay in the composition root):

- widgets: ``_status_label``, ``_wait_label``, ``_context_label``
- state: ``_status_is_error``, ``_wait_timer_id``, ``_wait_started``,
  ``_context_window_cache``, ``_context_window_probed``,
  ``_context_window_tasks``, ``_last_index_state``, ``_last_index_msg``,
  ``_active_provider``, ``_active_model``, ``_active_run``,
  ``_message_history``
- sibling methods called through ``self``: ``_set_busy`` (turn driver),
  ``_schedule_context_window_probe`` callers elsewhere.
"""

from __future__ import annotations

import asyncio
import logging
import time

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib
from pydantic_ai.messages import ModelMessage

from ..agent_factory import aresolve_model_context_length
from .format import format_tokens
from .usage import (
    _collect_token_usage,
    _format_native_cost,
    _run_usage_cost_override,
    _run_usage_output_override,
)

_log = logging.getLogger(__name__)


class StatusContextMixin:
    """Status bar, context-usage label, and model-wait; see module docstring."""

    def _schedule_context_window_probe(self, provider: str, model: str) -> None:
        """Resolve the model's context window once, off the unified loop."""
        if not provider or not model:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from a synchronous render path with no loop running (a
            # headless test, or before install()). Leave the key unprobed so
            # the next call under the unified loop schedules it.
            return
        key = (provider, model)
        self._context_window_probed.add(key)

        async def _probe() -> None:
            try:
                window = await aresolve_model_context_length(provider, model)
            except Exception as exc:  # never let a probe break a turn
                _log.debug("context-window probe failed for %s/%s: %s", provider, model, exc)
                return
            if window is not None:
                self._context_window_cache[key] = window
                self._update_context_label()

        task = loop.create_task(_probe())
        self._context_window_tasks.add(task)
        task.add_done_callback(self._context_window_tasks.discard)

    def _current_messages(self) -> list[ModelMessage]:
        """The one authoritative answer to "what are the messages right now".

        self._message_history is the STABLE snapshot, only reassigned at a
        few discrete points in the turn's lifecycle (a new prompt appended,
        the turn's final result, an approval-pause checkpoint) — it goes
        stale the moment a run starts streaming and stays stale until one of
        those points lands. self._active_run.all_messages() is live and
        current for exactly the window a run is in flight. Everything that
        needs "the current transcript" reads through this one method rather
        than re-deriving which of the two to trust.
        """
        return (
            self._active_run.all_messages()
            if self._active_run is not None
            else self._message_history
        )

    def _update_context_label(self) -> None:
        """Update the context usage label under the input box using Pydantic AI's native msg.usage."""
        msgs = self._current_messages()
        (
            last_input_tokens,
            last_output_tokens,
            last_reasoning_tokens,
            total_session_tokens,
            last_turn_cost,
            has_usage,
        ) = _collect_token_usage(msgs)
        # The run's own aggregated usage is the authoritative per-turn total:
        # all_messages() includes prior turns' responses, and the
        # last-response-only extraction undercounts multi-request turns. The
        # context label's main number (last_input_tokens) keeps the
        # last-response semantic — it is the context size at the end of the
        # turn.
        last_output_tokens, last_reasoning_tokens = _run_usage_output_override(
            self._active_run, last_output_tokens, last_reasoning_tokens
        )
        last_turn_cost, has_usage = _run_usage_cost_override(
            self._active_run, last_turn_cost, has_usage
        )

        active_provider = self._active_provider or ""
        active_model = self._active_model or ""
        # Read a cached value only. This runs inside the agent.iter() node
        # loop — after every node — and resolve_model_context_length makes a
        # blocking 3s HTTP request on a cache miss, which stalled the unified
        # GTK+asyncio loop mid-stream and did it again whenever the 60s
        # negative-cache TTL expired. The refresh happens off-loop instead,
        # scheduled once per (provider, model).
        max_context = self._context_window_cache.get((active_provider, active_model))
        if max_context is None and (active_provider, active_model) not in self._context_window_probed:
            self._schedule_context_window_probe(active_provider, active_model)

        pct: float | None = None
        if not msgs or last_input_tokens == 0:
            text = f"0 / {format_tokens(max_context)} tok" if max_context else "0 tok"
        else:
            if max_context:
                pct = min(100.0, (last_input_tokens / max_context) * 100)
                text = (
                    f"{format_tokens(last_input_tokens)} / {format_tokens(max_context)} tok ({pct:.0f}%)"
                )
            else:
                text = f"{format_tokens(last_input_tokens)} tok"

        if has_usage:
            cost_text = (
                f"Cost: {_format_native_cost(last_turn_cost)}"
                if last_turn_cost is not None
                else "Cost: N/A"
            )
            text = f"{text} · {cost_text}"

        # Escalation ramp via CSS classes (ui/css.py): quiet at 0-74%,
        # bold at 75-89%, theme accent at >=90%. No hardcoded colors.
        ctx_classes = self._context_label.get_style_context()
        ctx_classes.remove_class("warn")
        ctx_classes.remove_class("alarm")
        if pct is not None:
            if pct >= 90:
                ctx_classes.add_class("alarm")
            elif pct >= 75:
                ctx_classes.add_class("warn")
        self._context_label.set_text(text)
        reasoning_str = (
            f" ({last_reasoning_tokens:,} reasoning)" if last_reasoning_tokens else ""
        )
        self._context_label.set_tooltip_text(
            f"Active model: {active_model or 'default'}\n"
            f"Provider: {active_provider or 'unknown'}\n"
            f"Last turn input context: {last_input_tokens:,} tokens\n"
            f"Last turn output: {last_output_tokens:,} tokens{reasoning_str}\n"
            f"Total session tokens: {total_session_tokens:,} tokens\n"
            f"Native Pydantic AI last-turn cost: "
            f"{_format_native_cost(last_turn_cost) if last_turn_cost is not None else 'unavailable for one or more provider/model responses'}\n"
            f"Max model context: {f'{max_context:,}' if max_context else 'unknown'}"
        )

    def set_status(self, msg: str, *, error: bool = False, background: bool = False) -> None:
        """Update the status bar.

        Errors are sticky — a background message (``background=True``, e.g.
        the indexing poll) cannot overwrite a current error. User-initiated
        actions (the default) and other errors always overwrite. One uniform
        rule that keeps save errors / preflight failures / unreachable-backend
        warnings visible past the next "Catalog indexed" transition (M5).
        """
        if background and not error and self._status_is_error:
            return
        self._status_label.set_text(msg)
        self._status_is_error = error
        if error:
            self._status_label.get_style_context().add_class("validation-invalid")
        else:
            self._status_label.get_style_context().remove_class("validation-invalid")

    # -- model-wait elapsed indicator --------------------------------------
    # One uniform rule: the label is visible exactly while a model request
    # is awaited in the turn loop (start before `await _stream_request`, stop
    # in the finally). Tool execution shows its own expanders — no timer
    # there.

    def _model_wait_start(self) -> None:
        if self._wait_timer_id is not None:
            return
        self._wait_started = time.monotonic()
        self._update_wait_label()
        self._wait_label.show()
        self._wait_timer_id = GLib.timeout_add_seconds(1, self._on_wait_tick)

    def _on_wait_tick(self) -> bool:
        self._update_wait_label()
        return GLib.SOURCE_CONTINUE

    def _update_wait_label(self) -> None:
        secs = max(0, int(time.monotonic() - self._wait_started))
        text = f"{secs}s" if secs < 60 else f"{secs // 60}m{secs % 60:02d}s"
        self._wait_label.set_text(f"Waiting for model\u2026 {text}")

    def _model_wait_stop(self) -> None:
        if self._wait_timer_id is not None:
            GLib.source_remove(self._wait_timer_id)
            self._wait_timer_id = None
        self._wait_label.hide()

    def _domain_label(self, domain: str | None) -> str:
        if domain == "catalog":
            return "block library"
        if domain == "docs":
            return "documentation"
        return "index"

    def _poll_indexing(self) -> bool:
        """Surface RAG index-build progress in the status bar.

        Builds run on worker threads (dispatched via ``asyncio.to_thread`` from
        the agent tools) and mutate the per-domain ``_rag_building`` entries in
        place. This polls from the main loop so no cross-thread widget calls are
        needed (CPython per-key dict reads/writes are atomic). Catalog and docs
        builds can run concurrently (pydantic-ai runs tools in parallel), so
        status is tracked per-domain. Only writes the status bar while a build
        is in progress or on a transition — never when idle — so it can't
        clobber other messages.
        """
        from ..adapter import build_status

        # build_status() returns a snapshot: the worker thread may add a
        # domain entry concurrently, and iterating the live dict raises.
        building_msg: str | None = None
        for domain, entry in build_status().items():
            if not entry:
                continue
            status = entry.get("status")
            last = self._last_index_state.get(domain)
            label = self._domain_label(domain)
            if status == "building":
                self._last_index_state[domain] = "building"
                # Show progress for the first building domain found; a second
                # concurrent build is rare and its transition is still notified.
                if building_msg is None:
                    current = entry.get("current", 0)
                    total = entry.get("total", 0)
                    if total:
                        building_msg = f"Indexing {label} for search\u2026 {current}/{total}"
                    else:
                        building_msg = f"Indexing {label} for search\u2026"
            elif status in ("ready", "failed") and last != status:
                # Terminal transition for this domain — notify exactly once.
                self._last_index_state[domain] = status
                self._last_index_msg = None
                if status == "ready":
                    # `indexed` is the actually-embedded count (may be < total).
                    n = entry.get("indexed", entry.get("total", 0))
                    # background=True so a "Catalog indexed" transition can't
                    # clobber a sticky save/preflight error the user still
                    # needs to read (M5).
                    self.set_status(
                        f"{label.capitalize()} indexed \u2014 {n} entries ready for search.",
                        background=True,
                    )
                else:
                    # Indexing failures ARE surfaced — they're actionable
                    # ("search may return no or stale results") and the
                    # error class is preserved by the sticky rule.
                    self.set_status(
                        f"{label.capitalize()} indexing failed; search may return no or stale results.",
                        error=True,
                    )
                return True  # re-arm
        if building_msg is not None and building_msg != self._last_index_msg:
            self._last_index_msg = building_msg
            self.set_status(building_msg, background=True)
        return True  # re-arm

