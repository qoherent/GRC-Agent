"""Tool-return image rendering for live streams and persisted transcripts."""

import asyncio
import json


def _write_png(path, rgba: int) -> bytes:
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 2, 2)
    pixbuf.fill(rgba)
    pixbuf.savev(str(path), "png", [], [])
    return path.read_bytes()


def _iter_widgets(widget):
    from gi.repository import Gtk

    stack = [widget]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, Gtk.Container):
            stack.extend(current.get_children())


def _contains_binary_content(value):
    from collections.abc import Mapping, Sequence

    from pydantic_ai import BinaryContent

    if isinstance(value, BinaryContent):
        return True
    if isinstance(value, Mapping):
        return any(_contains_binary_content(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_contains_binary_content(item) for item in value)
    for attr in ("parts", "content", "metadata"):
        nested = getattr(value, attr, None)
        if nested is not None and _contains_binary_content(nested):
            return True
    return False


def test_tool_return_images_render_live_and_from_native_history(tmp_path):
    from gi.repository import Gtk
    from pydantic_ai import Agent, BinaryContent, ModelMessagesTypeAdapter, ToolReturn
    from pydantic_ai.messages import (
        ModelResponse,
        TextPart,
        ToolReturnPart,
        UserPromptPart,
    )
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_graph import End

    from grc_agent.chat.stream_view import _StreamCtx
    from grc_agent.chat_sidebar import ChatSidebar

    user_png = _write_png(tmp_path / "user.png", 0xFF0000FF)
    tool_png = _write_png(tmp_path / "tool.png", 0x0000FFFF)
    user_image = BinaryContent(data=user_png, media_type="image/png")
    tool_image = BinaryContent(data=tool_png, media_type="image/png")
    model_calls = 0

    def respond(_messages, _info):
        return ModelResponse(parts=[TextPart(content="done")])

    async def stream(_messages, _info):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            yield {
                0: DeltaToolCall(
                    name="observe",
                    json_args=json.dumps({}),
                    tool_call_id="observe-1",
                ),
                1: DeltaToolCall(
                    name="observe_secondary",
                    json_args=json.dumps({}),
                    tool_call_id="observe-secondary-1",
                )
            }
        else:
            yield "done"

    agent = Agent(FunctionModel(respond, stream_function=stream))

    @agent.tool_plain
    def observe():
        return ToolReturn(
            return_value={"samples": 4},
            content=["capture context", tool_image],
        )

    @agent.tool_plain
    def observe_secondary():
        return {"status": "complete"}

    sidebar = ChatSidebar()
    live_ctx = _StreamCtx(sidebar._start_agent_message())
    observed_tool_results = []
    set_tool_result = sidebar._set_tool_result

    def capture_tool_result(exp, result, **kwargs):
        observed_tool_results.append((result, kwargs.get("tool_content")))
        set_tool_result(exp, result, **kwargs)

    sidebar._set_tool_result = capture_tool_result

    async def run_live():
        async with agent.iter(["actual user prompt", user_image]) as run:
            node = run.next_node
            while node is not None and not isinstance(node, End):
                if Agent.is_model_request_node(node):
                    await sidebar._stream_request(live_ctx, node, run)
                elif Agent.is_call_tools_node(node):
                    await sidebar._stream_tools(live_ctx, node, run)
                node = await run.next(node)
            return run.result.all_messages()

    messages = asyncio.run(run_live())

    assert len(observed_tool_results) == 2
    assert ({"samples": 4}, ["capture context", tool_image]) in observed_tool_results
    live_tool_expander = next(
        widget
        for widget in _iter_widgets(live_ctx.box)
        if isinstance(widget, Gtk.Expander)
        and getattr(widget, "_grc_tool_name", None) == "observe"
    )
    live_tool_images = [widget for widget in _iter_widgets(live_tool_expander) if isinstance(widget, Gtk.Image)]
    assert len(live_tool_images) == 1
    assert any(
        isinstance(widget, Gtk.Label) and widget.get_text() == "capture context"
        for widget in _iter_widgets(live_tool_expander)
    )
    assert "b'\\x89PNG" not in live_ctx.box._grc_copy_btn._grc_copy_text

    tool_request = next(
        message
        for message in messages
        if any(isinstance(part, ToolReturnPart) for part in message.parts)
    )
    return_parts = [
        part for part in tool_request.parts if isinstance(part, ToolReturnPart)
    ]
    synthetic_prompt = next(part for part in tool_request.parts if isinstance(part, UserPromptPart))
    assert len(return_parts) == 2
    assert {part.content["samples"] for part in return_parts if "samples" in part.content} == {4}
    assert synthetic_prompt.content == ["capture context", tool_image]

    # Replay from PydanticAI's own serialized history, with no image side store.
    sidebar._message_history = ModelMessagesTypeAdapter.validate_json(
        ModelMessagesTypeAdapter.dump_json(messages)
    )
    sidebar._render_history()

    user_boxes = [
        widget
        for widget in _iter_widgets(sidebar._listbox)
        if isinstance(widget, Gtk.Box)
        and widget.get_style_context().has_class("chat-user-msg-box")
    ]
    assert len(user_boxes) == 1
    assert any(
        isinstance(widget, Gtk.Label) and widget.get_text() == "actual user prompt"
        for widget in _iter_widgets(user_boxes[0])
    )
    user_image_rows = [
        widget
        for widget in _iter_widgets(user_boxes[0])
        if isinstance(widget, Gtk.Box)
        and widget.get_style_context().has_class("chat-user-msg-images")
    ]
    assert len(user_image_rows) == 1
    assert sum(
        isinstance(widget, Gtk.Image) for widget in _iter_widgets(user_image_rows[0])
    ) == 1

    tool_expanders = [
        widget
        for widget in _iter_widgets(sidebar._listbox)
        if isinstance(widget, Gtk.Expander)
        and widget.get_style_context().has_class("chat-tool-expander")
    ]
    assert len(tool_expanders) == 2
    replay_tool_content = [
        widget
        for widget in _iter_widgets(sidebar._listbox)
        if isinstance(widget, Gtk.Box)
        and widget.get_style_context().has_class("chat-tool-result-content")
    ]
    assert len(replay_tool_content) == 1
    assert sum(
        isinstance(widget, Gtk.Image) for widget in _iter_widgets(replay_tool_content[0])
    ) == 1
    assistant_content_labels = [
        widget.get_text()
        for widget in _iter_widgets(replay_tool_content[0])
        if isinstance(widget, Gtk.Label)
    ]
    assert "capture context" in assistant_content_labels
    assert "b'\\x89PNG" not in tool_expanders[0]._grc_tool_body.get_text()



def test_numeric_tool_metadata_image_is_local_display_only(tmp_path):
    from gi.repository import Gtk
    from pydantic_ai import Agent, BinaryContent, ModelMessagesTypeAdapter, ToolReturn
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolReturnPart,
        UserPromptPart,
    )
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_graph import End

    from grc_agent.chat.stream_view import _StreamCtx
    from grc_agent.chat_sidebar import ChatSidebar

    image_data = _write_png(tmp_path / "metadata.png", 0x20C060FF)
    image_uri = BinaryContent(data=image_data, media_type="image/png").data_uri
    model_requests = []
    model_calls = 0

    def respond(_messages, _info):
        return ModelResponse(parts=[TextPart(content="measured")])

    async def stream(messages, _info):
        nonlocal model_calls
        model_calls += 1
        model_requests.append(messages)
        if model_calls == 1:
            yield {
                0: DeltaToolCall(
                    name="measure_run",
                    json_args=json.dumps({}),
                    tool_call_id="measure-run-1",
                )
            }
        else:
            yield "measured"

    agent = Agent(FunctionModel(respond, stream_function=stream))

    @agent.tool_plain
    def measure_run():
        return ToolReturn(
            return_value={"rms": 0.25, "peak": 0.5},
            metadata={"display_images": [image_uri]},
        )

    sidebar = ChatSidebar()
    live_ctx = _StreamCtx(sidebar._start_agent_message())

    async def run_live():
        async with agent.iter("measure this run") as run:
            node = run.next_node
            while node is not None and not isinstance(node, End):
                if Agent.is_model_request_node(node):
                    await sidebar._stream_request(live_ctx, node, run)
                elif Agent.is_call_tools_node(node):
                    await sidebar._stream_tools(live_ctx, node, run)
                node = await run.next(node)
            return run.result.all_messages()

    messages = asyncio.run(run_live())
    assert len(model_requests) == 2
    next_request = model_requests[1]
    assert not _contains_binary_content(next_request)
    assert all(
        not isinstance(part, UserPromptPart)
        or not (
            isinstance(part.content, list)
            and any(isinstance(item, BinaryContent) for item in part.content)
        )
        for message in next_request
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
    tool_request = next(
        message
        for message in messages
        if isinstance(message, ModelRequest)
        and any(isinstance(part, ToolReturnPart) for part in message.parts)
    )
    assert not any(isinstance(part, UserPromptPart) for part in tool_request.parts)
    tool_return = next(part for part in tool_request.parts if isinstance(part, ToolReturnPart))
    assert tool_return.content == {"rms": 0.25, "peak": 0.5}
    assert tool_return.metadata == {"display_images": [image_uri]}

    live_expander = next(
        widget
        for widget in _iter_widgets(live_ctx.box)
        if isinstance(widget, Gtk.Expander)
        and getattr(widget, "_grc_tool_name", None) == "measure_run"
    )
    assert sum(isinstance(widget, Gtk.Image) for widget in _iter_widgets(live_expander)) == 1
    assert "data:image/png" not in live_expander._grc_tool_body.get_text()

    serialized = ModelMessagesTypeAdapter.dump_json(messages)
    assert image_uri.encode() in serialized
    restored = ModelMessagesTypeAdapter.validate_json(serialized)
    sidebar._message_history = restored
    sidebar._render_history()

    history_expander = next(
        widget
        for widget in _iter_widgets(sidebar._listbox)
        if isinstance(widget, Gtk.Expander)
        and getattr(widget, "_grc_tool_name", None) == "measure_run"
    )
    assert sum(isinstance(widget, Gtk.Image) for widget in _iter_widgets(history_expander)) == 1
    assert "data:image/png" not in history_expander._grc_tool_body.get_text()
