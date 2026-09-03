# ruff: noqa: E402
"""Approval-gate mixin for ChatSidebar.

Owns the approval-mode toggle button (Manual/Auto/YOLO) and resolving a
run's pending approval requests into ``DeferredToolResults`` — rendering an
``ApprovalCard`` per request, the shell prefix-allow and "always accept"
shortcuts, and the YOLO/Auto auto-approve paths. Split out of
``chat_sidebar.py`` by U15 — a GTK-owning mixin, not a pure-function module,
so it still needs a display to test against.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import GLib, Gtk
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)

from ..settings import get_approval_mode, set_approval_mode
from ..ui.approval_card import ApprovalCard
from .stream_view import _StreamCtx

_log = logging.getLogger(__name__)


class ApprovalsMixin:
    """Approval-gate behavior mixed into ``ChatSidebar``.

    Every method here assumes the full ``ChatSidebar`` instance attributes
    (``self._approval_toggle``, ``self._shell_allowed_prefixes``, and the
    turn-driver state still living on ``ChatSidebar`` itself) — this is an
    organizational split, not an encapsulation boundary.
    """

    def _on_approval_mode_clicked(self, _button: Gtk.Button) -> None:
        """Cycle approval mode: Manual -> Auto -> YOLO -> Manual."""
        current = get_approval_mode()
        next_mode = {
            "manual": "auto",
            "auto": "yolo",
            "yolo": "manual",
        }.get(current, "manual")
        set_approval_mode(next_mode)
        self._update_approval_toggle()

    def _update_approval_toggle(self) -> None:
        """Sync the mode button label, styling, and tooltip to the persisted mode."""
        mode = get_approval_mode()
        ctx = self._approval_toggle.get_style_context()
        ctx.remove_class("chat-mode-manual")
        ctx.remove_class("chat-mode-auto")
        ctx.remove_class("chat-mode-yolo")

        if mode == "manual":
            self._approval_toggle.set_label("Mode: Manual")
            self._approval_toggle.set_tooltip_text(
                "Mode: Manual — ask before the agent changes the flowgraph, runs it, or runs shell commands. "
                "Click to switch to Auto (flowgraph changes apply automatically)."
            )
            ctx.add_class("chat-mode-manual")
        elif mode == "auto":
            self._approval_toggle.set_label("Mode: Auto")
            self._approval_toggle.set_tooltip_text(
                "Mode: Auto — flowgraph changes and runs apply without asking; shell commands still ask for approval. "
                "Click to switch to YOLO (all actions apply without asking)."
            )
            ctx.add_class("chat-mode-auto")
        else:  # yolo
            self._approval_toggle.set_label("Mode: YOLO")
            self._approval_toggle.set_tooltip_text(
                "Mode: YOLO — all actions (flowgraph changes, runs, and shell commands) apply without any gating or approval. "
                "Click to switch to Manual (ask before actions)."
            )
            ctx.add_class("chat-mode-yolo")

    async def _request_approvals(
        self, ctx: _StreamCtx, output: DeferredToolRequests
    ) -> DeferredToolResults:
        """Resolve a run's pending approval requests (any approval-gated
        tool: change_graph, run_flowgraph, the shell exec tools).

        - 'manual': Renders an ApprovalCard for every request (unless a shell
          command was previously prefix-allowed in this session).
        - 'auto': Auto-approves flowgraph changes and runs without UI;
          shell execution still requires user approval via ApprovalCard.
        - 'yolo': Auto-approves all requests immediately without UI.

        Returns the native DeferredToolResults consumed by the resumed
        ``agent.iter``.
        """
        approvals = [c for c in output.approvals]
        if not approvals:
            return DeferredToolResults()
        mode = get_approval_mode()
        if mode == "yolo":
            return DeferredToolResults(
                approvals={c.tool_call_id: ToolApproved() for c in approvals}
            )

        pending: dict[str, asyncio.Future] = {}
        cards: list[ApprovalCard] = []
        auto: dict[str, Any] = {}
        for call in approvals:
            is_shell = call.tool_name in ("run_command", "start_command")
            if mode == "auto" and not is_shell:
                auto[call.tool_call_id] = ToolApproved()
                continue
            if is_shell and self._shell_prefix_allowed(call):
                auto[call.tool_call_id] = ToolApproved()
                continue
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            pending[call.tool_call_id] = fut
            if is_shell:
                # Shell cards: "Always allow" means allow this command's first
                # token for the REST OF THIS SESSION (prefix-allow) — never the
                # persisted global gate-off, which stays a deliberate Mode
                # toggle away for something this powerful.
                on_always = lambda call=call: self._always_allow_command(  # noqa: E731
                    pending, cards, call
                )
            else:
                on_always = lambda: self._always_approve_all(pending, cards)  # noqa: E731
            card = ApprovalCard(
                self._md,
                call,
                on_approve=lambda cid=call.tool_call_id: self._resolve_approval(
                    pending, cid, ToolApproved()
                ),
                on_deny=lambda cid=call.tool_call_id: self._resolve_approval(
                    pending,
                    cid,
                    ToolDenied(message="The user rejected the proposed change."),
                ),
                on_always_accept=on_always,
            )
            cards.append(card)
            ctx.box.pack_start(card, False, False, 0)
            card.show_all()

        if not pending:
            return DeferredToolResults(approvals=auto)

        self._scroll_to_bottom()

        try:
            results = {cid: await fut for cid, fut in pending.items()}
        except asyncio.CancelledError:
            # Stop was pressed while waiting: remove the transient cards; the
            # CancelledError propagates to the turn's abort path, which keeps
            # the user's prompt and strips the unfulfilled tool call.
            for card in cards:
                card.destroy()
            raise
        results.update(auto)
        return DeferredToolResults(approvals=results)

    def _shell_prefix_allowed(self, call: Any) -> bool:
        """True when this shell call's first token was session-allowed.

        The set is scoped to the session it was granted in (checked against
        the active session id on every consult), so switching, loading, or
        clearing a chat starts fresh without any reset wiring at those sites.
        """
        if call.tool_name not in ("run_command", "start_command"):
            return False
        if self._shell_allowed_session != self._active_session_id:
            return False
        token = self._shell_first_token(call)
        return token is not None and token in self._shell_allowed_prefixes

    @staticmethod
    def _shell_first_token(call: Any) -> str | None:
        args = call.args_as_dict() if call.args else {}
        command = str(args.get("command") or "") if isinstance(args, dict) else ""
        tokens = command.split()
        return tokens[0] if tokens else None

    def _always_allow_command(
        self, pending: dict[str, asyncio.Future], cards: list[ApprovalCard], call: Any
    ) -> None:
        """'Always allow <tok>': remember the command's first token for this
        session, approve it (and any other pending call on the same token),
        and drop exactly those cards. The persisted gate is untouched."""
        token = self._shell_first_token(call)
        if token is None:
            return
        self._shell_allowed_prefixes.add(token)
        self._shell_allowed_session = self._active_session_id
        _log.info("Shell prefix-allow granted for %r in this session", token)

        def _matches(card: ApprovalCard) -> bool:
            other = getattr(card, "_call", None)
            return (
                other is not None
                and getattr(other, "tool_name", "") in ("run_command", "start_command")
                and self._shell_first_token(other) == token
            )

        for card in cards:
            if not _matches(card):
                continue
            cid = getattr(getattr(card, "_call", None), "tool_call_id", None)
            fut = pending.get(cid) if cid is not None else None
            if fut is not None and not fut.done():
                fut.set_result(ToolApproved())
        # Deferred to idle so a card is never destroyed from inside its own
        # click handler (same convention as _always_approve_all).
        GLib.idle_add(lambda: [c.destroy() for c in cards if _matches(c)])

    @staticmethod
    def _resolve_approval(
        pending: dict[str, asyncio.Future], cid: str, result: Any
    ) -> None:
        fut = pending.get(cid)
        if fut is not None and not fut.done():
            fut.set_result(result)

    def _always_approve_all(
        self, pending: dict[str, asyncio.Future], cards: list[ApprovalCard]
    ) -> None:
        """'Always accept': persist the gate mode to 'auto', approve every pending
        request, and drop the remaining cards (deferred to idle so a card is
        never destroyed from inside its own click handler)."""
        set_approval_mode("auto")
        self._update_approval_toggle()
        for fut in pending.values():
            if not fut.done():
                fut.set_result(ToolApproved())
        GLib.idle_add(lambda: [c.destroy() for c in cards])

