"""Behavioral golden for ChatSidebar rendering — the U15 characterization oracle.

The original pre-split golden was a hand-written script in an ephemeral
scratchpad and never committed, so the decomposition commits'
"byte-identical golden" claims were unverifiable from the repository. This
file is the committed replacement (plan U1, KTD2): a fixed pydantic-ai
session rendered through the REAL widget tree, serialized into a
deterministic projection, and compared against in-file literals.

What the projection pins (structure and text, never geometry — the U14
precedent): widget classes and CSS classes in depth-first order, label and
buffer texts, tool-expander labels and tool names, and every ``_grc_copy_text``
accumulator. Both capture paths are covered for the same turn:

- post-render (``_render_history`` over the recorded session), and
- mid-stream (the real ``_stream_request``/``_stream_tools`` handlers driven
  through a fake node yielding canned pydantic-ai events, sampling only the
  synchronously-updated ``ctx.full_raw_text`` accumulator and copy-text
  surfaces — never throttled on-screen buffer state; the one buffered surface
  sampled is force-flushed first).

Determinism protocol: every sampled surface is updated synchronously by the
code under test; nothing sampled reads the clock. The projection is verified
byte-identical across two consecutive fresh-sidebar renders.

Seam-stability contract: this file must keep running unmodified against the
pre-split tree at 1fb1d19 (the baseline comparison in plan R3 re-runs it
there). It therefore imports only pydantic-ai message classes, GTK, pytest,
the conftest walker, and ``ChatSidebar``/``_StreamCtx`` from
``grc_agent.chat_sidebar`` — never from ``grc_agent.chat.*`` mixin modules,
which do not exist at the baseline commit.

The decomposition range shipped one copy-path defect this file pinned on
arrival (U2 audit F-02): the history render path wrapped thinking text in
``<Thinking>`` tags while the streaming accumulator appended it bare, so
mid-stream and post-render copy text diverged on reasoning turns.
``test_thinking_copy_converges_mid_stream_and_post_render`` was committed
red as ``test_known_thinking_copy_divergence`` and flipped green by U3's
source fix; the literals below pin the converged behavior.
"""

import asyncio
import contextlib
from types import SimpleNamespace

import gi
from conftest import walk_widgets

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def _settle_events() -> None:
    """Bounded drain of the pending GTK event queue (same loop pattern as
    tests/test_chat_sidebar.py; bounded because a leaked repeating source
    would otherwise keep Gtk.events_pending() true forever)."""
    n = 0
    while n < 500 and Gtk.events_pending():
        Gtk.main_iteration()
        n += 1


# ---------------------------------------------------------------------------
# The fixed recorded session. Explicit tool_call_id values are load-bearing:
# ToolCallPart/RetryPromptPart generate random ids when omitted, and the
# history renderer links returns to calls through those ids.
# ---------------------------------------------------------------------------

_LPF_ARGS = {
    "add_blocks": [
        {"block_id": "low_pass_filter", "instance_name": "lpf_0", "params": {"cutoff": "10e3"}}
    ],
    "add_connections": ["noise_source_0:0 -> lpf_0:0", "lpf_0:0 -> audio_sink:0"],
}


def _recorded_session():
    """User text, assistant text, thinking, tool calls, a failed tool, and a
    retry — the full vocabulary the transcript renderer draws."""
    return [
        ModelRequest(parts=[UserPromptPart(content="Add a low-pass filter before the audio sink.")]),
        ModelResponse(
            parts=[
                ThinkingPart(
                    content="The sink chain ends at audio_sink. I will add an LPF "
                    "block and wire noise_source_0 through it."
                ),
                ToolCallPart(tool_name="change_graph", args=_LPF_ARGS, tool_call_id="call_lpf_1"),
                TextPart(content="Added the low-pass filter and wired it into the audio sink."),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="change_graph",
                    content="Added 1 block and 2 connections.",
                    tool_call_id="call_lpf_1",
                )
            ]
        ),
        ModelRequest(parts=[UserPromptPart(content="Now check the graph and run it.")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_ins_2"),
                ToolCallPart(
                    tool_name="run_command",
                    args={"command": "python dial_tone.py"},
                    tool_call_id="call_run_3",
                ),
                TextPart(content="The graph validates; the run hit a device error."),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="inspect_graph",
                    content="5 blocks, 4 connections, flow graph is valid.",
                    tool_call_id="call_ins_2",
                ),
                RetryPromptPart(
                    content="Missing required argument: timeout.",
                    tool_name="run_command",
                    tool_call_id="call_run_3",
                ),
            ]
        ),
    ]


# ---------------------------------------------------------------------------
# Deterministic projection.
# ---------------------------------------------------------------------------


def _project_widget(w):
    css = " ".join(w.get_style_context().list_classes())
    line = f"[{type(w).__name__}" + (f" .{css}" if css else "")
    tool_name = getattr(w, "_grc_tool_name", None)
    if tool_name is not None:
        line += f" tool={tool_name!r}"
    copy_text = getattr(w, "_grc_copy_text", None)
    if copy_text is not None:
        line += f" copy={copy_text!r}"
    # The history render path wipes the agent box it renders into, which
    # detaches the copy-button action row _start_agent_message just added —
    # the button survives only as the box's _grc_copy_btn attribute (with the
    # full copy text on it), out of the widget tree. Pre-existing at the
    # baseline commit (same wipe loop at 1fb1d19); pinned via btn_copy so the
    # full post-render copy text stays observable in the projection.
    btn = getattr(w, "_grc_copy_btn", None)
    if btn is not None and getattr(btn, "_grc_copy_text", None) is not None:
        line += f" btn_copy={btn._grc_copy_text!r}"
    if isinstance(w, Gtk.Expander):
        line += f" label={w.get_label()!r}"
    if isinstance(w, Gtk.Label):
        line += f" text={w.get_text()!r}"
    if isinstance(w, Gtk.TextView):
        buf = w.get_buffer()
        line += (
            f" buffer={buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)!r}"
        )
    return line + "]"


def _project(root):
    return "\n".join(_project_widget(w) for w in walk_widgets(root))


# ---------------------------------------------------------------------------
# Post-render capture: _render_history over the recorded session.
# ---------------------------------------------------------------------------


def _render_post_render(sidebar):
    sidebar._message_history = _recorded_session()
    sidebar._render_history()
    _settle_events()
    return _project(sidebar._listbox)


# ---------------------------------------------------------------------------
# Mid-stream capture: the real streaming handlers driven by canned events.
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        self._iter = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeNode:
    """Stands in for the pydantic-ai model-response node so the REAL
    _stream_request/_stream_tools handlers run unmodified."""

    def __init__(self, events):
        self._events = list(events)

    def stream(self, _ctx):
        # Mirrors the real node.stream(run.ctx) seam; the fake ignores the
        # run context.
        outer = self

        @contextlib.asynccontextmanager
        async def _ctx(_run_ctx):
            yield _FakeStream(outer._events)

        return _ctx(SimpleNamespace())


def _mid_stream_events():
    call = ToolCallPart(tool_name="change_graph", args=_LPF_ARGS, tool_call_id="call_lpf_1")
    request_events = [
        PartStartEvent(
            index=0,
            part=ThinkingPart(content="I should add the filter block and wire it in."),
        ),
        PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=" The sink expects floats.")),
        PartStartEvent(index=1, part=call),
        PartStartEvent(index=2, part=TextPart(content="Added the filter.")),
        PartDeltaEvent(index=2, delta=TextPartDelta(content_delta=" Wiring it now.")),
    ]
    tool_events = [
        FunctionToolCallEvent(part=call),
        FunctionToolResultEvent(
            part=ToolReturnPart(
                tool_name="change_graph",
                content="Added 1 block and 2 connections.",
                tool_call_id="call_lpf_1",
            )
        ),
    ]
    return request_events, tool_events


def _render_mid_stream(sidebar):
    from grc_agent.chat.stream_view import _StreamCtx

    request_events, tool_events = _mid_stream_events()
    ctx = _StreamCtx(sidebar._start_agent_message())
    run = SimpleNamespace(ctx=None)
    asyncio.run(sidebar._stream_request(ctx, _FakeNode(request_events), run))
    asyncio.run(sidebar._stream_tools(ctx, _FakeNode(tool_events), run))
    # Force-drain every dirty buffer so the one buffered surface this
    # projection samples is complete, then read the synchronous accumulators.
    sidebar._flush_streaming(ctx, force=True)
    copy_text = getattr(ctx.box._grc_copy_btn, "_grc_copy_text", "")
    body = f"full_raw_text={str(ctx.full_raw_text)!r}\n" + _project(ctx.box)
    return body, copy_text


# ---------------------------------------------------------------------------
# Expected literals — captured on the post-range tree; do not hand-edit.
# A changed literal must correspond to a deliberate, reviewed behavior change
# (plan KTD1: every later unit re-runs this golden against its diff).
# ---------------------------------------------------------------------------

EXPECTED_POST_RENDER = r"""[ListBox]
[ListBoxRow]
[Box .vertical chat-user-msg-box]
[Label text='Add a low-pass filter before the audio sink.']
[Box .horizontal]
[Button .image-button chat-copy-btn]
[Alignment]
[Box .horizontal]
[Image]
[ListBoxRow]
[Box .vertical chat-agent-msg-box btn_copy='<Thinking>\nThe sink chain ends at audio_sink. I will add an LPF block and wire noise_source_0 through it.\n</Thinking>\n<Tool Call: change_graph>\nArgs: {"add_blocks":[{"block_id":"low_pass_filter","instance_name":"lpf_0","params":{"cutoff":"10e3"}}],"add_connections":["noise_source_0:0 -> lpf_0:0","lpf_0:0 -> audio_sink:0"]}\nResult: Added 1 block and 2 connections.\nAdded the low-pass filter and wired it into the audio sink.']
[Expander .chat-thinking-expander label='Thought']
[ScrolledWindow]
[TextView .view chat-thinking-textview buffer='The sink chain ends at audio_sink. I will add an LPF block and wire noise_source_0 through it.']
[Label text='Thought']
[Expander .chat-tool-expander tool='change_graph' label='⚙ change_graph ✓']
[Label text='Added 1 block and 2 connections.']
[Label text='⚙ change_graph ✓']
[ScrolledWindow]
[TextView .view chat-agent-label buffer='Added the low-pass filter and wired it into the audio sink.\n']
[Box .horizontal chat-msg-actions]
[Button .image-button chat-copy-btn copy='<Thinking>\nThe sink chain ends at audio_sink. I will add an LPF block and wire noise_source_0 through it.\n</Thinking>\n<Tool Call: change_graph>\nArgs: {"add_blocks":[{"block_id":"low_pass_filter","instance_name":"lpf_0","params":{"cutoff":"10e3"}}],"add_connections":["noise_source_0:0 -> lpf_0:0","lpf_0:0 -> audio_sink:0"]}\nResult: Added 1 block and 2 connections.\nAdded the low-pass filter and wired it into the audio sink.']
[Alignment]
[Box .horizontal]
[Image]
[ListBoxRow]
[Box .vertical chat-user-msg-box]
[Label text='Now check the graph and run it.']
[Box .horizontal]
[Button .image-button chat-copy-btn]
[Alignment]
[Box .horizontal]
[Image]
[ListBoxRow]
[Box .vertical chat-agent-msg-box btn_copy='<Tool Call: inspect_graph>\nArgs: \nResult: 5 blocks, 4 connections, flow graph is valid.\n<Tool Call: run_command>\nArgs: {"command":"python dial_tone.py"}\nResult: Missing required argument: timeout.\n\nFix the errors and try again.\nThe graph validates; the run hit a device error.']
[Expander .chat-tool-expander tool='inspect_graph' label='⚙ inspect_graph ✓']
[Label text='5 blocks, 4 connections, flow graph is valid.']
[Label text='⚙ inspect_graph ✓']
[Expander .chat-tool-expander tool='run_command' label='⚠ run_command retry']
[Label text='Missing required argument: timeout.\n\nFix the errors and try again.']
[Label text='⚠ run_command retry']
[ScrolledWindow]
[TextView .view chat-agent-label buffer='The graph validates; the run hit a device error.\n']
[Box .horizontal chat-msg-actions]
[Button .image-button chat-copy-btn copy='<Tool Call: inspect_graph>\nArgs: \nResult: 5 blocks, 4 connections, flow graph is valid.\n<Tool Call: run_command>\nArgs: {"command":"python dial_tone.py"}\nResult: Missing required argument: timeout.\n\nFix the errors and try again.\nThe graph validates; the run hit a device error.']
[Alignment]
[Box .horizontal]
[Image]"""

EXPECTED_MID_STREAM = r"""full_raw_text='<Thinking>\nI should add the filter block and wire it in. The sink expects floats.\n</Thinking>\n<Tool Call: change_graph>\nArgs: {"add_blocks":[{"block_id":"low_pass_filter","instance_name":"lpf_0","params":{"cutoff":"10e3"}}],"add_connections":["noise_source_0:0 -> lpf_0:0","lpf_0:0 -> audio_sink:0"]}\nResult: Added 1 block and 2 connections.\nAdded the filter. Wiring it now.'
[Box .vertical chat-agent-msg-box btn_copy='<Thinking>\nI should add the filter block and wire it in. The sink expects floats.\n</Thinking>\n<Tool Call: change_graph>\nArgs: {"add_blocks":[{"block_id":"low_pass_filter","instance_name":"lpf_0","params":{"cutoff":"10e3"}}],"add_connections":["noise_source_0:0 -> lpf_0:0","lpf_0:0 -> audio_sink:0"]}\nResult: Added 1 block and 2 connections.\nAdded the filter. Wiring it now.']
[Expander .chat-thinking-expander label='Thought']
[ScrolledWindow]
[TextView .view chat-thinking-textview buffer='I should add the filter block and wire it in. The sink expects floats.']
[Label text='Thought']
[Expander .chat-tool-expander tool='change_graph' label='⚙ change_graph ✓']
[Label text='Added 1 block and 2 connections.']
[Label text='⚙ change_graph ✓']
[ScrolledWindow]
[TextView .view chat-agent-label buffer='Added the filter. Wiring it now.']
[Box .horizontal chat-msg-actions]
[Button .image-button chat-copy-btn copy='<Thinking>\nI should add the filter block and wire it in. The sink expects floats.\n</Thinking>\n<Tool Call: change_graph>\nArgs: {"add_blocks":[{"block_id":"low_pass_filter","instance_name":"lpf_0","params":{"cutoff":"10e3"}}],"add_connections":["noise_source_0:0 -> lpf_0:0","lpf_0:0 -> audio_sink:0"]}\nResult: Added 1 block and 2 connections.\nAdded the filter. Wiring it now.']
[Alignment]
[Box .horizontal]
[Image]"""


def test_golden_post_render_matches_literal(sidebar):
    window = Gtk.OffscreenWindow()
    window.add(sidebar)
    window.show_all()
    try:
        projection = _render_post_render(sidebar)
        assert projection == EXPECTED_POST_RENDER
    finally:
        window.destroy()


def test_golden_mid_stream_matches_literal(sidebar):
    window = Gtk.OffscreenWindow()
    window.add(sidebar)
    window.show_all()
    try:
        _body, _copy = _render_mid_stream(sidebar)
        assert _body == EXPECTED_MID_STREAM
    finally:
        window.destroy()


def test_golden_projection_is_deterministic():
    from grc_agent.chat_sidebar import ChatSidebar

    projections = []
    sidebars = []
    try:
        for _ in range(2):
            sidebar = ChatSidebar()
            sidebars.append(sidebar)
            window = Gtk.OffscreenWindow()
            window.add(sidebar)
            window.show_all()
            projections.append(_render_post_render(sidebar))
            window.destroy()
    finally:
        for sidebar in sidebars:
            sidebar.destroy()
    assert projections[0] == projections[1]
    assert projections[0] == EXPECTED_POST_RENDER


def test_thinking_copy_converges_mid_stream_and_post_render(sidebar):
    """The same reasoning turn must copy identically mid-stream and after
    re-render (the origin U15 scenario the decomposition range violated).

    Historical note: the range shipped a divergence — the streaming
    accumulator appended thinking text bare while the history renderer
    wrapped it in <Thinking> tags. This test was committed red as
    ``test_known_thinking_copy_divergence_is_characterized`` so the fix had
    to flip it deliberately; U3 closed the divergence at the source with one
    wrapped-form builder in ``chat/format.py`` shared by both paths."""
    window = Gtk.OffscreenWindow()
    window.add(sidebar)
    window.show_all()
    try:
        _body, mid_copy = _render_mid_stream(sidebar)
        sidebar2_messages = [
            ModelRequest(parts=[UserPromptPart(content="Think then add the filter.")]),
            ModelResponse(
                parts=[
                    ThinkingPart(content="I should add the filter block and wire it in. The sink expects floats."),
                    ToolCallPart(tool_name="change_graph", args=_LPF_ARGS, tool_call_id="call_lpf_1"),
                    TextPart(content="Added the filter. Wiring it now."),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="change_graph",
                        content="Added 1 block and 2 connections.",
                        tool_call_id="call_lpf_1",
                    )
                ]
            ),
        ]
        sidebar._message_history = sidebar2_messages
        sidebar._render_history()
        _settle_events()
        # Read the post-render copy from the box's own button reference so the
        # assertion is about copy content, not action-row attachment (that is
        # the F-01 pin's job in test_chat_sidebar.py).
        boxes = [
            w
            for w in walk_widgets(sidebar._listbox)
            if w.get_style_context().has_class("chat-agent-msg-box")
        ]
        assert boxes, "agent message box not rendered"
        post_copy = getattr(boxes[0], "_grc_copy_btn", None)
        assert post_copy is not None, "agent box lost its copy button reference"
        post_copy = post_copy._grc_copy_text
        assert "<Thinking>" in post_copy
        assert mid_copy == post_copy
    finally:
        window.destroy()
