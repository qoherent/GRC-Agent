# ruff: noqa: E402
"""Streaming-render mixin for ChatSidebar.

Owns the per-turn live-streaming state (``_StreamCtx``, ``_ChunkAccumulator``)
and every handler that turns ``agent.iter()``'s node-by-node streaming events
(text/thinking deltas, tool call/result events) into GTK widgets as they
arrive. Split out of ``chat_sidebar.py`` by U15 — a GTK-owning mixin, not a
pure-function module, so it still needs a display to test against.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk
from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ThinkingPartDelta,
)
from pydantic_ai.messages import (
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)

from .constants import _SCROLL_STICK_THRESHOLD
from .format import (
    _parse_final_summary,
    _tool_args_text,
    _tool_label,
    _transcript_summary,
    _transcript_thinking_close,
    _transcript_thinking_open,
    _transcript_tool_call,
    _transcript_tool_result,
)

_log = logging.getLogger(__name__)

# Minimum interval between visible-text UI flushes. Stream chunks accumulate
# append-only and drain into Gtk.TextBuffer in batches; the final markdown
# render still replaces only this turn's temporary stream row.
_STREAM_FLUSH_INTERVAL = 0.033


class _ChunkAccumulator:
    """Append-only streamed text with O(1) chunk ingestion and delta drains."""

    __slots__ = ("_chunks", "_flushed", "_length", "_joined")

    def __init__(self, text: str = "") -> None:
        self._chunks = [text] if text else []
        self._flushed = 0
        self._length = len(text)
        self._joined: str | None = text or ""

    def append(self, text: str) -> None:
        if not text:
            return
        self._chunks.append(text)
        self._length += len(text)
        self._joined = None

    def reset(self, text: str = "") -> None:
        self._chunks = [text] if text else []
        self._flushed = 0
        self._length = len(text)
        self._joined = text or ""

    def clear(self) -> None:
        self.reset()

    def drain_new(self) -> str:
        if self._flushed >= len(self._chunks):
            return ""
        delta = "".join(self._chunks[self._flushed :])
        self._flushed = len(self._chunks)
        return delta

    def replace_chunk(self, old: str, new: str) -> bool:
        """Replace the most recent chunk equal to ``old`` with ``new``.

        Used to patch a tool-call fragment in place once its result arrives,
        without reordering the transcript around it (the call and the
        eventual result can have unrelated text streamed in between). Only
        an UNFLUSHED chunk is eligible — one already drained has already
        been sent downstream and cannot be taken back.
        """
        for i in range(len(self._chunks) - 1, self._flushed - 1, -1):
            if self._chunks[i] == old:
                self._chunks[i] = new
                self._length += len(new) - len(old)
                self._joined = None
                return True
        return False

    def __iadd__(self, text: str):
        self.append(text)
        return self

    def __len__(self) -> int:
        return self._length

    def __bool__(self) -> bool:
        return bool(self._length)

    def __str__(self) -> str:
        if self._joined is None:
            self._joined = "".join(self._chunks)
        return self._joined


@dataclass(slots=True)
class _StreamCtx:
    """Per-call mutable streaming state — held outside ``send_message``
    so the node/event handler helpers can stay small and flat.

    A pure state bag: the hand-written ``__slots__`` tuple plus an ``__init__``
    that only assigned defaults are exactly what ``@dataclass(slots=True)``
    generates, and the two could drift out of sync by hand.
    """

    box: Gtk.Box
    text_lbl: Gtk.TextView | None = None
    text_acc: _ChunkAccumulator = field(default_factory=_ChunkAccumulator)
    text_dirty: bool = False
    think_body: Any = None
    think_expander: Gtk.Expander | None = None
    think_scrolled: Gtk.ScrolledWindow | None = None
    think_acc: _ChunkAccumulator = field(default_factory=_ChunkAccumulator)
    think_dirty: bool = False
    tools: dict[str, Gtk.Expander] = field(default_factory=dict)
    # (fragment, tool_name, args_str) for each in-flight tool call, by call
    # id — so the eventual result can patch the call-only fragment into the
    # SAME combined shape the history render path produces (see
    # _record_tool_result_transcript), rather than appending a second,
    # differently-tagged fragment once the result arrives separately.
    tool_call_fragments: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    full_raw_text: _ChunkAccumulator = field(default_factory=_ChunkAccumulator)
    # Whether full_raw_text currently carries an unclosed <Thinking> region:
    # opened at the thinking part start, closed at _close_thinking — or at
    # the next thinking part start, which replaces the buffer in place.
    # Without this, mid-stream copy text carried thinking bare and diverged
    # from the post-render copy (U3/F-02).
    thinking_transcript_open: bool = False
    last_flush: float = 0.0
    last_event_ts: float = 0.0
    pending_chars: int = 0
    pending_chunks: int = 0


class StreamViewMixin:
    """Streaming-render behavior mixed into ``ChatSidebar``.

    Every method here assumes the full ``ChatSidebar`` instance attributes
    (``self._scrolled``, ``self._md``, and the transcript/tool-card builders
    still living on ``ChatSidebar`` itself) — this is an organizational
    split, not an encapsulation boundary.
    """

    async def _stream_request(self, ctx: _StreamCtx, node, run) -> None:
        async with node.stream(run.ctx) as stream:
            async for event in stream:
                if isinstance(event, PartStartEvent):
                    self._on_part_start(ctx, event)
                elif isinstance(event, PartDeltaEvent):
                    self._on_part_delta(ctx, event)
        # Force a final flush so the last throttled chunk is painted before the
        # node hands control back (and before any markdown re-render).
        self._flush_streaming(ctx, force=True)
        self._close_thinking(ctx)

    async def _stream_tools(self, ctx: _StreamCtx, node, run) -> None:
        async with node.stream(run.ctx) as stream:
            async for event in stream:
                if isinstance(event, FunctionToolCallEvent):
                    tcid = event.part.tool_call_id or ""
                    exp = ctx.tools.get(tcid)
                    if exp is not None:
                        self._set_tool_status(exp)
                elif isinstance(event, FunctionToolResultEvent):
                    tcid = event.tool_call_id or ""
                    exp = ctx.tools.get(tcid)
                    if exp is not None:
                        if isinstance(event.part, RetryPromptPart):
                            res_str = event.part.model_response()
                            name = getattr(exp, "_grc_tool_name", "?")
                            self._set_tool_body(exp, res_str)
                            exp.set_label(_tool_label(name, retry=True, result=res_str))
                        else:
                            res_str = str(event.part.content)
                            # Read the settled outcome, same as the history
                            # render path (_render_last_message_rich). Before
                            # this fix _set_tool_result always defaulted to
                            # ok=True, so a failed tool call rendered as
                            # succeeded while streaming and as failed only
                            # after a full re-render.
                            ok = getattr(event.part, "outcome", "success") != "failed"
                            self._set_tool_result(exp, res_str, ok=ok)
                        self._record_tool_result_transcript(ctx, tcid, res_str)
                        self._update_copy_text(ctx.box, ctx.full_raw_text)

    def _on_part_start(self, ctx: _StreamCtx, event: PartStartEvent) -> None:
        part = event.part
        if isinstance(part, TextPart):
            self._close_thinking(ctx)
            self._close_text(ctx)
            ctx.text_acc.reset(part.content or "")
            ctx.full_raw_text += part.content or ""
            self._ensure_text(ctx)
            ctx.text_dirty = True
            self._update_copy_text(ctx.box, ctx.full_raw_text)
            self._flush_streaming(ctx, force=True)
        elif isinstance(part, ToolCallPart):
            self._close_text(ctx)
            self._close_thinking(ctx)
            tcid = part.tool_call_id or ""
            summary = _parse_final_summary(part.args)
            if (part.tool_name or "") == "final_result" and summary is not None:
                # The model's final structured output (GrcAgentResponse)
                # arrives as a final_result tool call — render it as a
                # readable summary card, not a raw-JSON tool expander.
                # Deliberately NOT registered in ctx.tools: the
                # FunctionToolResultEvent handler would overwrite the card
                # with the "Final result processed." return.
                widget = self._make_final_summary_widget(*summary)
                ctx.box.pack_start(widget, False, False, 0)
                widget.show_all()
                ctx.full_raw_text += _transcript_summary(*summary)
                self._update_copy_text(ctx.box, ctx.full_raw_text)
                return
            exp = self._make_tool_expander(part.tool_name or "?")
            args_str = _tool_args_text(part)
            if args_str:
                self._set_tool_body(exp, args_str)
            ctx.box.pack_start(exp, False, False, 0)
            exp.show_all()
            ctx.tools[tcid] = exp
            tool_name = part.tool_name or "?"
            call_fragment = _transcript_tool_call(tool_name, args_str)
            ctx.tool_call_fragments[tcid] = (call_fragment, tool_name, args_str)
            ctx.full_raw_text += call_fragment
            self._update_copy_text(ctx.box, ctx.full_raw_text)
        elif isinstance(part, NativeToolCallPart):
            # Native tool calls (e.g. provider-native web_search/web_fetch) never
            # fire FunctionToolCallEvent/FunctionToolResultEvent — call and return
            # arrive purely as ordinary response parts, each in its own
            # PartStartEvent (no delta class exists for either).
            self._close_text(ctx)
            self._close_thinking(ctx)
            tcid = part.tool_call_id or ""
            exp = self._make_tool_expander(part.tool_name or "?")
            args_str = _tool_args_text(part)
            if args_str:
                self._set_tool_body(exp, args_str)
            ctx.box.pack_start(exp, False, False, 0)
            exp.show_all()
            ctx.tools[tcid] = exp
            tool_name = part.tool_name or "?"
            call_fragment = _transcript_tool_call(tool_name, args_str)
            ctx.tool_call_fragments[tcid] = (call_fragment, tool_name, args_str)
            ctx.full_raw_text += call_fragment
            self._update_copy_text(ctx.box, ctx.full_raw_text)
        elif isinstance(part, NativeToolReturnPart):
            tcid = part.tool_call_id or ""
            exp = ctx.tools.get(tcid)
            if exp is not None:
                res_str = str(part.content)
                self._set_tool_result(exp, res_str, ok=part.outcome != "failed")
                self._record_tool_result_transcript(ctx, tcid, res_str)
                self._update_copy_text(ctx.box, ctx.full_raw_text)
        elif isinstance(part, ThinkingPart):
            self._close_text(ctx)
            if ctx.thinking_transcript_open:
                # Consecutive thinking parts reuse the buffer in place; the
                # transcript region still needs its closer before the new
                # part's opener.
                ctx.full_raw_text += _transcript_thinking_close()
                ctx.thinking_transcript_open = False
            self._ensure_thinking(ctx)
            ctx.think_acc.reset(part.content or "")
            # A new part replaces the previous one's content: clear the buffer
            # so only this part is shown.
            ctx.think_body.get_buffer().set_text("")
            ctx.full_raw_text += _transcript_thinking_open(part.content or "")
            ctx.thinking_transcript_open = True
            ctx.think_dirty = True
            self._update_copy_text(ctx.box, ctx.full_raw_text)
            self._flush_streaming(ctx, force=True)

    def _on_part_delta(self, ctx: _StreamCtx, event: PartDeltaEvent) -> None:
        delta = event.delta
        ctx.last_event_ts = time.monotonic()
        if isinstance(delta, TextPartDelta):
            self._close_thinking(ctx)
            content = delta.content_delta or ""
            ctx.text_acc += content
            ctx.full_raw_text += content
            ctx.pending_chunks += 1
            ctx.pending_chars += len(content)
            self._ensure_text(ctx)
            ctx.text_dirty = True
            self._flush_streaming(ctx)
        elif isinstance(delta, ThinkingPartDelta):
            # content_delta is Optional on this delta type (unlike TextPartDelta):
            # a ThinkingPartDelta may carry only a signature/provider-metadata
            # update with no text at all. Codex sends those around its reasoning
            # summary parts, and appending one raised
            # "can only concatenate str (not NoneType) to str", killing the turn.
            if delta.content_delta is None:
                return
            self._close_text(ctx)
            content = delta.content_delta
            ctx.think_acc += content
            ctx.full_raw_text += content
            ctx.pending_chunks += 1
            ctx.pending_chars += len(content)
            self._ensure_thinking(ctx)
            ctx.think_dirty = True
            self._flush_streaming(ctx)

    def _flush_streaming(self, ctx: _StreamCtx, *, force: bool = False) -> None:  # noqa: C901
        """Drain append-only stream chunks into GTK text buffers at a bounded rate.

        Collapsed reasoning stays out of GTK layout until part close; expanded
        reasoning updates at 4 Hz. A forced close flush preserves every byte.
        """
        now = time.monotonic()
        if not force:
            # Reasoning is secondary and collapsed by default. Do not spend
            # the GTK thread laying out hidden streamed text; if the user
            # expands it, update at 4 Hz. The full lossless buffer is flushed
            # once when the part closes.
            thinking_only = ctx.think_dirty and not ctx.text_dirty
            if (
                thinking_only
                and ctx.think_expander is not None
                and not ctx.think_expander.get_expanded()
            ):
                return
            text_len = len(ctx.text_acc) + len(ctx.think_acc)
            if thinking_only:
                interval = 0.25
            elif text_len > 5000:
                interval = 0.066
            elif text_len > 2000:
                interval = 0.050
            else:
                interval = _STREAM_FLUSH_INTERVAL
            if (now - ctx.last_flush) < interval:
                return

        flush_start = time.monotonic()
        flushed = False
        if ctx.text_dirty and ctx.text_lbl is not None:
            self._flush_text(ctx)
            ctx.text_dirty = False
            flushed = True
        if ctx.think_dirty and ctx.think_body is not None:
            self._flush_thinking(ctx)
            flushed = True
        if flushed or force:
            flush_end = time.monotonic()
            queue_wait_ms = (flush_start - ctx.last_event_ts) * 1000.0 if ctx.last_event_ts > 0 else 0.0
            flush_duration_ms = (flush_end - flush_start) * 1000.0
            if ctx.pending_chunks > 0:
                _log.debug(
                    "stream_flush: chunks=%d chars=%d queue_wait=%.2fms duration=%.2fms",
                    ctx.pending_chunks,
                    ctx.pending_chars,
                    queue_wait_ms,
                    flush_duration_ms,
                )
                ctx.pending_chunks = 0
                ctx.pending_chars = 0
            ctx.last_flush = now
            if force:
                self._update_copy_text(ctx.box, ctx.full_raw_text)
            if flushed:
                self._scroll_to_bottom()

    def _flush_thinking(self, ctx: _StreamCtx) -> None:
        """Append only the thinking delta since the last flush and auto-scroll."""
        if ctx.think_body is None:
            return
        buffer = ctx.think_body.get_buffer()
        delta = ctx.think_acc.drain_new()
        if delta:
            adj = (
                ctx.think_scrolled.get_vadjustment()
                if ctx.think_scrolled is not None
                else None
            )
            near_bottom = (
                adj is None
                or (adj.get_upper() - adj.get_page_size() - adj.get_value())
                <= _SCROLL_STICK_THRESHOLD
            )
            buffer.insert(buffer.get_end_iter(), delta)
            if near_bottom:
                buffer.move_mark(buffer.get_insert(), buffer.get_end_iter())
                ctx.think_body.scroll_to_mark(buffer.get_insert(), 0.0, True, 0.0, 1.0)
        ctx.think_dirty = False

    def _flush_text(self, ctx: _StreamCtx) -> None:
        """Append only new visible text; markdown is rendered after the part closes."""
        if ctx.text_lbl is None:
            return
        buffer = ctx.text_lbl.get_buffer()
        delta = ctx.text_acc.drain_new()
        if delta:
            buffer.insert(buffer.get_end_iter(), delta)
        ctx.text_dirty = False

    def _close_text(self, ctx: _StreamCtx) -> None:
        if ctx.text_lbl is None:
            return
        self._flush_streaming(ctx, force=True)
        ctx.text_lbl = None
        ctx.text_acc.clear()
        ctx.text_dirty = False

    def _thinking_label(self, *, streaming: bool = False) -> str:
        if getattr(self, "_active_provider", "") == "openai_codex":
            return "Thinking (summary)..." if streaming else "Thought summary (Codex)"
        return "Thinking..." if streaming else "Thought"

    def _close_thinking(self, ctx: _StreamCtx) -> None:
        if ctx.think_body is None:
            return
        self._flush_streaming(ctx, force=True)
        if ctx.thinking_transcript_open:
            ctx.full_raw_text += _transcript_thinking_close()
            ctx.thinking_transcript_open = False
            self._update_copy_text(ctx.box, ctx.full_raw_text)
        if ctx.think_expander is not None:
            ctx.think_expander.set_label(self._thinking_label(streaming=False))
            ctx.think_expander.set_expanded(False)
        ctx.think_body = None
        ctx.think_expander = None
        ctx.think_scrolled = None
        ctx.think_acc.clear()
        ctx.think_dirty = False
        self._scrolled.check_resize()
        self._scroll_to_bottom()

    def _ensure_text(self, ctx: _StreamCtx) -> Gtk.TextView:
        if ctx.text_lbl is None:
            ctx.text_lbl = self._make_stream_textview()
            # AUTOMATIC-hscrollbar isolation: without it the stream
            # TextView's content-driven (then allocation-sticky) minimum
            # propagates row → list → HPaned and shoves the divider aside as
            # long tokens stream in. Same pattern as CodeBlock/TableBlock.
            sw = self._md.wrap_hscrollable(ctx.text_lbl) if self._md else ctx.text_lbl
            ctx.box.pack_start(sw, False, False, 0)
            sw.show_all()
        return ctx.text_lbl

    def _make_stream_textview(self) -> Gtk.TextView:
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_left_margin(0)
        tv.set_right_margin(0)
        tv.set_top_margin(0)
        tv.set_bottom_margin(0)
        tv.set_hexpand(True)
        tv.set_halign(Gtk.Align.FILL)
        tv.get_style_context().add_class("chat-agent-label")
        # Pin to the chat column: a bare TextView's preferred width follows its
        # unwrapped buffer content, so long unbroken streamed tokens (code
        # lines, URLs) used to grow the row minimum and shove the outer
        # HPaned divider aside mid-stream.
        if self._md is not None:
            self._md.pin_to_column(tv)
        return tv

    def _make_thinking_textview(self, text: str = "") -> Gtk.TextView:
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.get_style_context().add_class("chat-thinking-textview")
        # Same column pin as the stream/prose TextViews (extra: the expander's
        # arrow/spacing around this deeper-nested view) — an expanded long
        # thinking line must not shove the HPaned divider either.
        if self._md is not None:
            self._md.pin_to_column(tv, extra=24)
        tv.set_text = lambda t: tv.get_buffer().set_text(t)
        if text:
            tv.set_text(text)
        return tv

    def _make_thinking_widget(
        self,
        text: str = "",
        label: str = "Thinking...",
        *,
        expanded: bool = False,
    ) -> tuple[Gtk.Expander, Gtk.TextView]:
        exp = Gtk.Expander(label=label)
        exp.set_expanded(expanded)
        exp.get_style_context().add_class("chat-thinking-expander")
        exp.set_hexpand(True)
        exp.set_halign(Gtk.Align.FILL)
        exp.connect("notify::expanded", self._on_expander_toggled)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.NONE)
        sw.set_min_content_height(200)
        sw.set_max_content_height(750)
        sw.set_propagate_natural_height(True)
        sw.set_hexpand(True)
        sw.set_halign(Gtk.Align.FILL)

        tv = self._make_thinking_textview(text)
        tv.set_hexpand(True)
        tv.set_halign(Gtk.Align.FILL)

        sw.add(tv)
        exp.add(sw)
        exp._grc_scrolled = sw
        return exp, tv

    def _ensure_thinking(self, ctx: _StreamCtx) -> Any:
        if ctx.think_body is None:
            exp, tv = self._make_thinking_widget(
                label=self._thinking_label(streaming=True),
                expanded=True,
            )
            ctx.box.pack_start(exp, False, False, 0)
            exp.show_all()
            ctx.think_expander = exp
            ctx.think_body = tv
            ctx.think_scrolled = getattr(exp, "_grc_scrolled", None)
            self._scrolled.check_resize()
            self._scroll_to_bottom()
        return ctx.think_body

    def _record_tool_result_transcript(self, ctx: _StreamCtx, tcid: str, result: str) -> None:
        """Append the tool result to the transcript in the SAME combined
        shape the history render path produces (one <Tool Call: ...> block
        carrying its own Result: line), instead of a second, separately
        tagged <Tool Result: ...> block — the divergence
        _transcript_tool_result's own docstring used to admit rather than fix.

        The call and its result are necessarily two different streaming
        events, so the call fragment is recorded in ctx.tool_call_fragments
        when it is appended, then patched in place here. If the original
        fragment already scrolled out of the accumulator's unflushed window
        (already sent to the UI), the patch cannot land without reordering
        the transcript — fall back to the old separately-tagged form rather
        than silently dropping the result.
        """
        recorded = ctx.tool_call_fragments.pop(tcid, None)
        if recorded is not None:
            call_fragment, tool_name, args_str = recorded
            combined = _transcript_tool_call(tool_name, args_str, result)
            if ctx.full_raw_text.replace_chunk(call_fragment, combined):
                return
        ctx.full_raw_text += _transcript_tool_result(result)

