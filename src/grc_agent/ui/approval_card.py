# ruff: noqa: E402
"""Approval cards for human-in-the-loop tool approval.

Tools registered with ``requires_approval=True`` (``change_graph``,
``run_flowgraph``, the shell exec tools) end their run with a
``DeferredToolRequests`` output; the sidebar shows one :class:`ApprovalCard`
per proposed call. The card renders the model-provided one-line ``reason``
(when the tool has one) plus a per-tool structured summary of the JSON args
(derived by :func:`format_tool_summary`) and offers Approve / Deny /
Always-accept actions.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango
from pydantic_ai.messages import ToolCallPart


def _arrow(text: str) -> str:
    # The tool's canonical 'src:port->dst:port' spelling — cosmetic
    # arrow only, no semantic transformation.
    return text.replace("->", " → ")


def _add_blocks_lines(add_blocks: list[Any] | None) -> list[str]:
    lines = []
    for b in add_blocks or []:
        b = b or {}
        name = b.get("name") or "?"
        block_id = b.get("block_id") or "?"
        params = b.get("params") or {}
        line = f"- `{name}` (`{block_id}`)"
        param_text = ", ".join(f"{k}={v}" for k, v in params.items())
        if param_text:
            line += f" — {param_text}"
        lines.append(line)
    return lines


def _add_group(
    groups: list[tuple[str, list[str]]], title: str, lines: list[str]
) -> None:
    if lines:
        groups.append((title, lines))


def format_change_summary(args: dict[str, Any]) -> str:
    """Render a change_graph JSON payload as a compact Markdown bullet list.

    One uniform rule over the tool's own schema fields — every non-empty
    field becomes a labeled bullet group; ``force`` is surfaced explicitly
    since it bypasses GRC's validation gate. Returns the empty-string-markdown
    ``_No changes in this batch._`` when nothing is present.
    """
    groups: list[tuple[str, list[str]]] = []

    _add_group(groups, "**Add blocks:**", _add_blocks_lines(args.get("add_blocks")))
    _add_group(
        groups, "**Remove blocks:**", [f"- `{n}`" for n in args.get("remove_blocks") or []]
    )
    _add_group(
        groups,
        "**Add connections:**",
        [f"- `{_arrow(str(c))}`" for c in args.get("add_connections") or []],
    )
    _add_group(
        groups,
        "**Remove connections:**",
        [f"- `{_arrow(str(c))}`" for c in args.get("remove_connections") or []],
    )
    _add_group(
        groups,
        "**Update parameters:**",
        [
            f"- `{p.get('name', '?')}.{p.get('param', '?')}` = `{p.get('value', '')}`"
            for p in args.get("update_params") or []
            if p
        ],
    )
    _add_group(
        groups,
        "**Update states:**",
        [
            f"- `{s.get('name', '?')}` → {s.get('state', '?')}"
            for s in args.get("update_states") or []
            if s
        ],
    )
    _add_group(
        groups, "", ["*(force: bypasses GRC's own validation gate)*"] if args.get("force") else []
    )

    if not groups:
        return "_No changes in this batch._"
    out: list[str] = []
    for title, items in groups:
        if title:
            out.append(title)
        out.extend(items)
    return "\n".join(out)


def format_tool_summary(tool_name: str, args: dict[str, Any]) -> str:
    """Render a deferred tool call's JSON args as a compact Markdown summary.

    One dispatch on the tool name — `change_graph` keeps its dedicated
    field-aware renderer; the run/stop and shell exec tools get a literal,
    copy-pasteable rendering (the command IS the reason for those tools);
    anything else falls back to one uniform per-field bullet list. Never
    raises on unexpected shapes: an unknown tool still gets its args shown.
    """
    if tool_name == "change_graph":
        return format_change_summary(args)
    if tool_name == "run_flowgraph":
        lines = ["- Run the active flowgraph with GRC's native Execute action."]
        if args.get("wait"):
            lines.append(
                f"- Wait up to `{args.get('timeout_seconds', 60)}s` for completion."
            )
        else:
            lines.append("- Return immediately; the run continues until stopped.")
        return "\n".join(lines)
    if tool_name in ("run_command", "start_command"):
        cmd = str(args.get("command") or "").strip() or "_(empty command)_"
        suffix = "\n\n*(runs in the background)*" if tool_name == "start_command" else ""
        return f"```sh\n{cmd}\n```{suffix}"
    # Uniform fallback: one labeled bullet per argument value.
    lines = []
    for key, value in args.items():
        text = str(value)
        if len(text) > 300:
            text = text[:300] + "…"
        lines.append(f"- `{key}`: `{text}`")
    return "\n".join(lines) if lines else "_No arguments._"


_TOOL_CARD_TITLES = {
    "change_graph": "Proposed change — requires approval",
    "run_flowgraph": "Proposed flowgraph run — requires approval",
    "run_command": "Proposed command — requires approval",
    "start_command": "Proposed background command — requires approval",
}
_DEFAULT_CARD_TITLE = "Proposed action — requires approval"


class ApprovalCard(Gtk.Box):
    """The approve/deny card for one proposed approval-gated tool call."""

    def __init__(
        self,
        md: Any,
        call: ToolCallPart,
        on_approve: Any,
        on_deny: Any,
        on_always_accept: Any,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.get_style_context().add_class("chat-approval-card")
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self._call = call

        args = call.args_as_dict() if call.args else {}
        reason = str(args.get("reason") or "") if isinstance(args, dict) else ""

        title = Gtk.Label()
        title.set_markup(
            f"<b>{_TOOL_CARD_TITLES.get(call.tool_name, _DEFAULT_CARD_TITLE)}</b>"
        )
        title.set_xalign(0.0)
        title.set_halign(Gtk.Align.START)
        self.pack_start(title, False, False, 0)

        if reason:
            reason_label = Gtk.Label(label=reason)
            reason_label.set_xalign(0.0)
            reason_label.set_halign(Gtk.Align.START)
            reason_label.set_line_wrap(True)
            reason_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            reason_label.set_selectable(True)
            reason_label.get_style_context().add_class("chat-approval-reason")
            self.pack_start(reason_label, False, False, 0)

        if isinstance(args, dict):
            summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            summary_box.set_hexpand(True)
            summary_text = format_tool_summary(call.tool_name, args)
            if md is not None:
                md.render(summary_box, summary_text, clear=False)
            else:
                fallback = Gtk.Label(label=summary_text)
                fallback.set_xalign(0.0)
                fallback.set_halign(Gtk.Align.START)
                fallback.set_line_wrap(True)
                summary_box.pack_start(fallback, False, False, 0)
            self.pack_start(summary_box, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)

        approve = Gtk.Button(label="Approve")
        approve.get_style_context().add_class(Gtk.STYLE_CLASS_SUGGESTED_ACTION)
        approve.set_tooltip_text("Apply this change once")
        approve.set_focus_on_click(False)
        approve.connect("clicked", lambda _b: on_approve())
        buttons.pack_start(approve, False, False, 0)

        deny = Gtk.Button(label="Deny")
        deny.get_style_context().add_class(Gtk.STYLE_CLASS_DESTRUCTIVE_ACTION)
        deny.set_tooltip_text("Reject this change; the agent will be told and can adjust")
        deny.set_focus_on_click(False)
        deny.connect("clicked", lambda _b: on_deny())
        buttons.pack_start(deny, False, False, 0)

        always = Gtk.Button(label="Always accept")
        if call.tool_name in ("run_command", "start_command"):
            token = str(args.get("command") or "").split()[0] if args.get("command") else ""
            if token:
                always.set_label(f"Always allow `{token}`")
                always.set_tooltip_text(
                    f"Allow `{token}` commands for the rest of this session — "
                    "other commands still ask. The global Manual/Auto gate is untouched."
                )
            else:
                always.set_tooltip_text("Allow this command for the rest of this session")
        else:
            always.set_tooltip_text(
                "Apply this change and stop asking for approval — re-enable Manual mode with the "
                "'Mode' toggle under the composer"
            )
        always.set_focus_on_click(False)
        always.connect("clicked", lambda _b: on_always_accept())
        buttons.pack_start(always, False, False, 0)

        self.pack_start(buttons, False, False, 0)
        self.show_all()
