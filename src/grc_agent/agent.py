"""Thin runtime wrapper for routed package-level `.grc` tools."""

import json
import logging
from typing import Any, Callable

from grc_agent.catalog import describe_block
from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval.search import _search_grc_with_context
from grc_agent.runtime_tool_validation import (
    build_tool_schema_map,
    validate_runtime_tool_call,
)
from grc_agent.session import get_grc_context, load_grc, summarize_graph
from grc_agent.transaction import apply_edit, propose_edit

logger = logging.getLogger(__name__)

ToolResult = dict[str, Any]
ToolCallable = Callable[..., ToolResult]
HistoryEntry = dict[str, Any]

PUBLIC_TOOL_NAMES: tuple[str, ...] = (
    "load_grc",
    "summarize_graph",
    "search_grc",
    "get_grc_context",
    "describe_block",
    "apply_edit",
    "propose_edit",
    "validate_graph",
    "save_graph",
)


class GrcAgent:
    """A thin integration layer between a language model and package-level owners."""

    def __init__(
        self,
        session: FlowgraphSession | None = None,
        *,
        catalog_root: str | None = None,
    ) -> None:
        self.session = FlowgraphSession() if session is None else session
        self.catalog_root = str(catalog_root) if catalog_root is not None else None
        self.history: list[HistoryEntry] = []
        self._last_validated_state_revision: int | None = None
        self._last_validation_ok: bool | None = None
        self._reset_validation_tracking()
        self._tools = self._build_tool_registry()
        self._tool_schema_map = build_tool_schema_map(self.get_tool_schemas())
        self._record_active_session_history(reason="initial_session")

    def get_system_prompt(self) -> str:
        return (
            "You are a GRC (GNU Radio Companion) Agent.\n"
            "Your job is to inspect and safely modify `.grc` files using only the provided tools.\n"
            "Decision rules:\n"
            "1. The active session context tells you which `.grc` file is loaded. "
            "Use `load_grc` only when the user explicitly asks to switch files.\n"
            '2. Scope selection: use `scope="session"` only for my graph / this graph / current graph / '
            "in here / loaded graph, or when the user explicitly wants blocks from the active session. "
            "If the user says find / search / look up / discover a block type, or names a block family like "
            "Head / throttle2 / AGC / time sink / OFDM / PSK / QAM / equalizer / channelizer / scrambler, "
            'use `scope="catalog"` unless they explicitly say the current graph. '
            'Example: `Find the Head block` starts with `search_grc(query="Head", scope="catalog")`.\n'
            "3. `get_grc_context` needs an exact session instance name like `blocks_throttle2_0` or a variable "
            "name like `samp_rate`. `describe_block` needs a GNU block id like `blocks_throttle2` or "
            "`qtgui_time_sink_x`. If the user names one loaded block or variable and asks what uses it, "
            "what is around it, how it is wired, or says to take a quick look at it, call `get_grc_context` directly.\n"
            "4. After `search_grc`, block results include `block_id`. "
            "If the user asked to explain a block, call `describe_block` with that `block_id`. "
            "Never pass `catalog:block:...` or `session:block:...` into `describe_block`.\n"
            "5. MANDATORY: If the user says 'find', 'search', or 'look up' first, you MUST call "
            "`search_grc` before `describe_block` — even if the query looks like a known GNU block id "
            "or family such as `throttle2`. Never call `describe_block` as the first tool when the user "
            "said 'look up', 'find', or 'search' — always start with `search_grc`. "
            "Otherwise, if the user directly asks what / tell me about / explain one specific block, prefer `describe_block` "
            'even when the block might not exist yet. Examples: `Tell me about foobar_baz` => `describe_block(block_id="foobar_baz")`; '
            '`What is a QT GUI time sink?` => `describe_block(block_id="qtgui_time_sink_x")`.\n'
            "6. When the user asks to change, set, update, remove, add, connect, disconnect, "
            "or modify anything, ALWAYS call `apply_edit`. "
            "ONLY use `propose_edit` when the user explicitly says "
            "preview / dry-run / what-if / would it work / what would happen if. "
            "Do NOT use `propose_edit` just because you inspected first or want to be cautious. "
            "Removing a named block or variable like `samp_rate` is still an edit: use `apply_edit`, "
            "do not ask for clarification.\n"
            "7. For parameter edits, use transactions shaped like "
            '`{"op_type": "update_params", "instance_name": "samp_rate", "params": {"value": "48000"}}`. '
            "When the user says sample rate, speed, or rate, they usually mean the `samp_rate` variable. "
            "Expand abbreviations: 8k=8000, 32k=32000, 44.1k=44100, 48k=48000, 96k=96000.\n"
            "8. Supported `op_type` values: `update_params`, `add_connection`, `remove_connection`, "
            "`remove_block`, and detached-variable `add_block`. Do not invent wrappers or new op types. "
            "Example remove_block: "
            '`{"op_type": "remove_block", "instance_name": "samp_rate"}`. '
            "Example add_block: "
            '`{"op_type": "add_block", "block_type": "variable", "instance_name": "debug_flag", '
            '"parameters": {"value": "0"}}`. '
            "Use plain JSON keys like `nconnections`, `srate`, and `value`, not quoted key names.\n"
            "9. For rewires, pass all operations in one ordered transaction list. "
            "To add a second trace to the time sink: "
            '`[{"op_type": "update_params", "instance_name": "qtgui_time_sink_x_0", '
            '"params": {"nconnections": "2"}}, {"op_type": "add_connection", '
            '"src_block": "blocks_char_to_float_0", "src_port": 0, '
            '"dst_block": "qtgui_time_sink_x_0", "dst_port": 1}]`. '
            "A request like `Put another trace on the time sink` or `add a second trace` is an `apply_edit` request, not `propose_edit`. "
            "To remove `samp_rate` while keeping the standard graph valid: "
            '`[{"op_type": "update_params", "instance_name": "blocks_throttle2_0", '
            '"params": {"samples_per_second": "32000"}}, {"op_type": "update_params", '
            '"instance_name": "qtgui_time_sink_x_0", "params": {"srate": "32000"}}, '
            '{"op_type": "remove_block", "instance_name": "samp_rate"}]`. '
            "Always expand `nconnections` before adding the connection in the same transaction. "
            "`remove_connection` needs `src_block`, `src_port`, `dst_block`, `dst_port`.\n"
            "10. Complete every requested step in order before answering. "
            "If the user said look / inspect / check / show first, you MUST call an inspection tool "
            "(summarize_graph, get_grc_context, search_grc, or describe_block) before any edit. "
            "Do not skip to apply_edit when the user asked to inspect first. "
            "If the user names a specific loaded block or variable, prefer `get_grc_context` over `summarize_graph`. "
            "If the user asks a vague whole-graph question like what am I looking at or what is generating the signal here, prefer `summarize_graph`. "
            "If the edit is already clear and the user did NOT ask to inspect first, do not add an inspection step. "
            "If the user asked to apply and validate, call `validate_graph` after a successful edit. "
            "Phrases like save / persist / write it out mean `save_graph` on the current graph. "
            "Only call `save_graph` after successful validation of the current dirty state.\n"
            "11. If the user asks for unsupported operations (undo, redo, export as Python, "
            "edit raw YAML, generate code), do not call a tool; answer briefly that it is unsupported.\n"
            "12. After `summarize_graph`, copy the tool summary verbatim as your final answer. "
            "After other successful flows, return one short factual sentence.\n"
            "13. When a tool returns `ok: false`, report the error message to the user and stop unless the "
            "user explicitly asked you to recover or retry after that failure. Do not continue to contingent "
            "steps like `validate_graph` or `save_graph` after a failed edit or preview. You may do one corrected "
            "retry only when the user explicitly asked for recovery and the tool error or hint gives a clear fix. "
            "After a preview-only request, stop after `propose_edit` and explain the preview result; do not call "
            "`validate_graph` or `save_graph` unless the preview succeeded and the user separately asked for them.\n"
            "14. Questions about the current graph state — like is the graph dirty, what variables are "
            "in the graph, what blocks are loaded, give me a summary, what changed, show me the current "
            "state — should use `summarize_graph`. But if the user says search / find / look through the "
            "current graph for a class of blocks such as sinks or sources, use `search_grc` with "
            '`scope="session"`, not `summarize_graph`. Do NOT call `apply_edit` to answer state queries.\n'
            "15. When the user gives a follow-up message in a multi-turn conversation, do NOT repeat "
            "edits or actions that were already completed in a prior turn. Only execute the new request. "
            "If the user asks to validate after a prior edit, just call `validate_graph` — do not "
            "re-apply the edit. If the user asks to save after validation, just call `save_graph`.\n"
            "16. When the user asks to disconnect and then remove a connected block in the same request, "
            "remove every attached wire first, then `remove_block`, all in one ordered transaction list. "
            'Example for removing `blocks_throttle2_0`: `[{"op_type": "remove_connection", '
            '"src_block": "analog_random_source_x_0", "src_port": 0, "dst_block": "blocks_throttle2_0", '
            '"dst_port": 0}, {"op_type": "remove_connection", "src_block": "blocks_throttle2_0", '
            '"src_port": 0, "dst_block": "blocks_char_to_float_0", "dst_port": 0}, '
            '{"op_type": "remove_block", "instance_name": "blocks_throttle2_0"}]`.\n'
            "17. In a follow-up message, when the user says the edit is already applied and asks you to "
            "only validate or save, do NOT call `apply_edit` again. Call only the tools the user requested. "
            "If the save was previously refused because the graph needs validation, call `validate_graph` "
            "then `save_graph` — without re-editing."
        )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return the fixed tool schemas exposed to a chat-completions client."""
        return [
            self._schema(
                "load_grc",
                "Load a GNU Radio Companion .grc file into the active session.",
                {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the .grc file to load.",
                    }
                },
                required=["file_path"],
            ),
            self._schema(
                "summarize_graph",
                "Return a bounded summary of the loaded GNU Radio graph. "
                "Use this for vague whole-graph questions like what am I looking at, what is generating the signal here, "
                "give me a quick overview, what variables are in the graph, what blocks are loaded, "
                "is the graph dirty, show me the current state, or what changed. "
                "Do NOT use this when the user explicitly says to search or look through the current graph "
                "for a class of blocks like sinks or sources; use `search_grc` with `scope=\"session\"` instead.",
                {
                    "max_blocks": {
                        "type": "integer",
                        "description": "Optional maximum number of blocks to preview.",
                    }
                },
            ),
            self._schema(
                "search_grc",
                "Search for GNU Radio blocks by name, function, or domain concept. "
                "Use this when the user wants to find, search for, look up, or discover a block "
                "(e.g. filtering, modulation, carrier recovery, scrambling, OFDM, PSK, QAM, "
                "equalizer, channelizer, spread spectrum, frequency hopping, AGC, Head, throttle2, sink, source). "
                'Use `scope="session"` only for the loaded graph and `scope="catalog"` for GNU Radio discovery or block-family lookups. '
                'Example: `Find the Head block` => `search_grc(query="Head", scope="catalog")`. '
                'Example: `Look through my current graph for sink blocks` => `search_grc(query="sink", scope="session")`. '
                "Keep the user's distinguishing words together in the query: search `frequency sink`, not generic `sink`. "
                "Block results include `block_id` for `describe_block` and `node_id` for `get_grc_context`. "
                "If the user said find / search / look up first, prefer this before `describe_block` even when the query already resembles a block id. "
                "Do NOT use this when the user only wants details for a specific GNU block id — use `describe_block` instead.",
                {
                    "query": {
                        "type": "string",
                        "description": "Search text to look up.",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["catalog", "session"],
                        "description": "Whether to search the installed GNU catalog or the active session.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Optional maximum number of results to return.",
                    },
                },
                required=["query"],
            ),
            self._schema(
                "get_grc_context",
                "Show the connections and neighborhood around a specific block in the session. "
                "Use this when the user asks how blocks are wired, connected, routed, linked, used, or what is around a named block or variable. "
                "Pass the exact loaded session instance name (e.g. `blocks_throttle2_0` or `samp_rate`), not a catalog id. "
                "If the name is not found, close matches will be suggested.",
                {
                    "node_id": {
                        "type": "string",
                        "description": "Loaded session block instance name.",
                    },
                    "hops": {
                        "type": "integer",
                        "description": "Optional neighborhood depth.",
                    },
                    "max_nodes": {
                        "type": "integer",
                        "description": "Optional maximum number of nodes to include.",
                    },
                },
                required=["node_id"],
            ),
            self._schema(
                "describe_block",
                "Return the full parameter list, port types, and documentation for one GNU Radio block. "
                "Pass a GNU block id such as `blocks_throttle2` or `qtgui_time_sink_x`. "
                "If you searched first, use the result's `block_id` field. "
                "NEVER call this first if the user said 'find', 'look up', or 'search' — call `search_grc` first in those cases. "
                "If the user directly asks about one named block, you can call this even when the block may not exist; the tool will report not found.",
                {
                    "block_id": {
                        "type": "string",
                        "description": "GNU Radio block id to describe.",
                    }
                },
                required=["block_id"],
            ),
            self._schema(
                "apply_edit",
                "Apply a transaction to the live graph. This is the DEFAULT edit tool — "
                "use it whenever the user asks to change, set, update, remove, add, connect, disconnect, or modify something. "
                "For parameter edits, pass "
                '`{"transaction": {"op_type": "update_params", "instance_name": "samp_rate", '
                '"params": {"value": "48000"}}}`. Supported `op_type`: `update_params`, '
                "`add_connection`, `remove_connection`, `remove_block`, detached-variable `add_block`. "
                "For remove_block, pass "
                '`{"transaction": {"op_type": "remove_block", "instance_name": "samp_rate"}}`. '
                "For disconnects, include all four endpoint fields. "
                "To remove a connected block, remove every attached wire first in the same ordered transaction, then `remove_block`. "
                "For second-trace rewires, pass an ordered list: first update `nconnections`, then `add_connection`. "
                "For adding a detached variable, pass "
                '`{"transaction": {"op_type": "add_block", "block_type": "variable", '
                '"instance_name": "my_var", "parameters": {"value": "0"}}}`. '
                "Use plain JSON parameter names like `nconnections`, `srate`, and `value`. "
                "If the user wants to remove `samp_rate` but keep the graph working, use one ordered repair transaction that updates dependent params to literals before `remove_block`.",
                {
                    "transaction": {
                        "type": ["object", "array"],
                        "description": "One supported operation object or an ordered list of operation objects using the narrow phase-5 transaction shape.",
                    }
                },
                required=["transaction"],
            ),
            self._schema(
                "propose_edit",
                "Preview whether a transaction would succeed. This does NOT modify the graph. "
                "ONLY use this when the user explicitly says preview / dry-run / what-if / would it work / what would happen if. "
                "For all other edit requests, use `apply_edit` instead. "
                "Do NOT use this after an inspect-first edit request unless the user explicitly asked for a preview. "
                "For parameter edits, pass "
                '`{"transaction": {"op_type": "update_params", "instance_name": "samp_rate", '
                '"params": {"value": "48000"}}}`. Supported `op_type`: `update_params`, '
                "`add_connection`, `remove_connection`, `remove_block`, detached-variable `add_block`. "
                "For remove_block, pass "
                '`{"transaction": {"op_type": "remove_block", "instance_name": "samp_rate"}}`. '
                "For rewires, use ordered transaction lists when one step enables another. "
                "For adding a detached variable, pass "
                '`{"transaction": {"op_type": "add_block", "block_type": "variable", '
                '"instance_name": "my_var", "parameters": {"value": "0"}}}`. '
                "If the preview fails and the user only asked for a preview, explain the failure and stop.",
                {
                    "transaction": {
                        "type": ["object", "array"],
                        "description": "One supported operation object or an ordered list of operation objects using the narrow phase-5 transaction shape.",
                    }
                },
                required=["transaction"],
            ),
            self._schema(
                "validate_graph",
                "Compile-check the current graph. Use this to verify the graph is valid, "
                "will compile, or will run correctly.",
                {},
            ),
            self._schema(
                "save_graph",
                "Write the current graph to disk. Use this to save, persist, or write out the flowgraph. "
                "Phrases like `write it out` or `write this out` mean the current loaded graph. "
                "Allowed only after the latest dirty state has validated successfully. "
                "Pass an optional `path` to save to a specific destination.",
                {
                    "path": {
                        "type": "string",
                        "description": "Optional destination path for the saved .grc file.",
                    }
                },
            ),
        ]

    def get_model_messages(self) -> list[HistoryEntry]:
        """Render the current runtime history into chat-completions messages."""
        messages: list[HistoryEntry] = [
            {
                "role": "system",
                "content": self.get_system_prompt(),
            }
        ]

        for index, turn in enumerate(self.history):
            role = turn.get("role")

            if role == "session":
                messages.append(
                    {
                        "role": "system",
                        "content": self._session_history_content_as_text(
                            turn.get("content"),
                            reason=turn.get("reason"),
                        ),
                    }
                )
                continue

            if role == "tool":
                tool_name = turn.get("name")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(
                            turn.get("tool_call_id") or f"tool_call_{index}"
                        ),
                        "name": tool_name,
                        "content": self._history_content_as_text(
                            turn.get("content"),
                            tool_name=tool_name,
                        ),
                    }
                )
                continue

            if role == "reminder":
                messages.append(
                    {
                        "role": "system",
                        "content": str(turn.get("content") or ""),
                    }
                )
                continue

            if role not in {"user", "assistant"}:
                continue

            message: HistoryEntry = {
                "role": role,
                "content": turn.get("content"),
            }
            if role == "assistant" and "tool_calls" in turn:
                message["tool_calls"] = turn["tool_calls"]
            messages.append(message)

        return messages

    def execute_tool(self, tool_name: str, kwargs: dict[str, Any]) -> ToolResult:
        """Execute one runtime tool and return a structured result.

        Validation is intentionally performed here even though the llama loop
        (``run_bounded_llama_turn``) also validates before calling this method.
        The check here is the authoritative gate for direct callers (CLI ``run``
        command, tests, and any future callers that bypass the loop).  The
        loop-level pre-check exists to keep ``tool_calls_executed`` accurate:
        schema-rejected calls are never counted as executed.
        """
        validation_result = self.validate_tool_call(tool_name, kwargs)
        if validation_result is not None:
            logger.info("tool_call_rejected tool=%s error_type=%s", tool_name, validation_result.get("error_type"))
            return validation_result

        func = self._tools[tool_name]
        try:
            result = func(**kwargs)
            logger.info("tool_executed tool=%s ok=%s", tool_name, result.get("ok"))
            return result
        except Exception as error:
            logger.exception("tool_exception tool=%s error=%s", tool_name, error)
            return self._tool_result(
                tool_name=tool_name,
                ok=False,
                message=str(error),
                error_type=ErrorCode.INTERNAL_ERROR,
            )
    def validate_tool_call(self, tool_name: str, kwargs: Any) -> ToolResult | None:
        """Validate one runtime tool call against the declared public schema."""
        validation_error = validate_runtime_tool_call(
            tool_name, kwargs, self._tool_schema_map
        )
        if validation_error is None:
            return None
        return self._tool_result(tool_name=tool_name, ok=False, **validation_error)

    def health_check(self) -> dict[str, Any]:
        """Return a structured health payload describing agent readiness.

        ``status`` is ``"ok"`` when the agent has tools registered and retrieval
        is available.  Whether a session is loaded is reported separately and
        does *not* affect the status — loading a file is a user action, not a
        runtime health concern.
        """
        has_session = self.session.flowgraph is not None
        has_retrieval = self.catalog_root is not None
        tool_count = len(self._tools)
        status = "ok" if tool_count > 0 and has_retrieval else "not_ready"
        return {
            "status": status,
            "session_loaded": has_session,
            "retrieval_ready": has_retrieval,
            "history_length": len(self.history),
            "tool_count": tool_count,
        }

    def active_session_snapshot(self) -> dict[str, Any] | None:
        """Return the compact active-session payload exposed in runtime history and CLI output."""
        if self.session.flowgraph is None:
            return None
        snapshot = self.session.session_provenance()
        snapshot["state_revision"] = self.session.state_revision
        snapshot["dirty"] = self.session.is_dirty
        snapshot["validation"] = self.session.validation_state()
        variable_preview: list[str] = []
        block_preview: list[str] = []
        for block in self.session.flowgraph.blocks:
            if block.block_type == "variable":
                value = block.params.get("parameters", {}).get("value", "")
                variable_preview.append(f"{block.instance_name}={value}")
                continue
            block_preview.append(
                f"{block.instance_name} ({block.block_type}{self._block_role_hint(block.block_type)})"
            )
        if variable_preview:
            snapshot["variable_preview"] = variable_preview
        if block_preview:
            snapshot["block_preview"] = block_preview[:6]
        return snapshot

    def run_step_fake(
        self, user_msg: str, fake_assistant_actions: list[HistoryEntry]
    ) -> None:
        """
        A fake loop step to test the plumbing.
        fake_assistant_actions is a list of dicts.
        If it has 'tool', it's a tool call. If it has 'text', it's a message.
        """
        self.history.append({"role": "user", "content": user_msg})

        for action in fake_assistant_actions:
            if "text" in action:
                self.history.append({"role": "assistant", "content": action["text"]})

            if "tool" in action:
                tool_name = action["tool"]
                kwargs = action.get("kwargs", {})

                self.history.append(
                    {
                        "role": "assistant",
                        "tool_calls": [{"name": tool_name, "arguments": kwargs}],
                    }
                )

                result = self.execute_tool(tool_name, kwargs)

                self.history.append(
                    {
                        "role": "tool",
                        "name": tool_name,
                        "content": result,
                    }
                )

    @staticmethod
    def _schema(
        name: str,
        description: str,
        properties: dict[str, Any],
        *,
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required or [],
                    "additionalProperties": False,
                },
            },
        }

    def _build_tool_registry(self) -> dict[str, ToolCallable]:
        return {
            "load_grc": self._load_grc,
            "summarize_graph": self._summarize_graph,
            "search_grc": self._search_grc,
            "get_grc_context": self._get_grc_context,
            "describe_block": self._describe_block,
            "apply_edit": self._apply_edit,
            "propose_edit": self._propose_edit,
            "validate_graph": self._validate_graph,
            "save_graph": self._save_graph,
        }

    def _reset_validation_tracking(self) -> None:
        """Align save gating with the current live session state."""
        self._last_validation_ok = self.session.last_validation_ok
        self._last_validated_state_revision = None
        if self.session.last_validation_ok:
            self._last_validated_state_revision = self.session.state_revision
        elif not self.session.is_dirty:
            self._last_validated_state_revision = self.session.state_revision

    def _record_successful_validation(self) -> None:
        self._last_validation_ok = True
        self._last_validated_state_revision = self.session.state_revision

    def _replace_session(self, session: FlowgraphSession) -> None:
        self.session = session
        self._reset_validation_tracking()
        self._record_active_session_history(reason="load_grc")

    def _tool_result(
        self, tool_name: str, ok: bool, message: str, **extra: Any
    ) -> ToolResult:
        """Build the common structured result payload returned by every tool."""
        result: ToolResult = {
            "tool": tool_name,
            "ok": ok,
            "message": message,
        }
        result.update(extra)
        if not ok and "error_type" not in result:
            result["error_type"] = ErrorCode.INTERNAL_ERROR
        result["active_session"] = self.active_session_snapshot()
        return result

    def _payload_result(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        default_message: str | None = None,
    ) -> ToolResult:
        result = dict(payload)
        result["tool"] = tool_name
        if default_message is not None and "message" not in result:
            result["message"] = default_message
        result["active_session"] = self.active_session_snapshot()
        return result

    def _record_active_session_history(self, *, reason: str) -> None:
        snapshot = self.active_session_snapshot()
        if snapshot is None:
            return
        self.history.append(
            {
                "role": "session",
                "reason": reason,
                "content": snapshot,
            }
        )

    _PROACTIVE_COMPACT_CHAR_BUDGET = 60000

    def compact_history(self) -> None:
        """Reduce history token cost before a new multi-turn conversation turn.

        1. Keep only the last ``role="session"`` entry.
        2. For ``role="tool"`` entries older than the previous turn boundary
           (a turn boundary is a ``role="user"`` entry), truncate content to
           the small set of fields needed for the model to understand past
           outcomes without repeating large payloads.
        3. Proactively compact when the total history char budget is exceeded,
           keeping only the latest session, latest user message, and compacted
           older tool results.
        """
        last_session_index: int | None = None
        for index, turn in enumerate(self.history):
            if turn.get("role") == "session":
                last_session_index = index

        if last_session_index is not None and last_session_index > 0:
            self.history = [
                turn
                for idx, turn in enumerate(self.history)
                if turn.get("role") != "session" or idx == last_session_index
            ]

        user_indices = [
            idx for idx, turn in enumerate(self.history) if turn.get("role") == "user"
        ]
        previous_turn_start = user_indices[-2] if len(user_indices) >= 2 else None

        if previous_turn_start is not None:
            compacted = []
            for idx, turn in enumerate(self.history):
                if (
                    turn.get("role") == "tool"
                    and idx < previous_turn_start
                    and isinstance(turn.get("content"), dict)
                ):
                    compacted.append(self._compact_tool_entry(turn))
                else:
                    compacted.append(turn)
            self.history = compacted

        self._proactive_compact_if_needed()
        logger.debug("compact_history history_len=%d", len(self.history))

    def _proactive_compact_if_needed(self) -> None:
        """Drop older assistant/tool detail when history exceeds the char budget."""
        total_chars = sum(
            len(str(turn)) for turn in self.history
        )
        if total_chars <= self._PROACTIVE_COMPACT_CHAR_BUDGET:
            return

        user_indices = [
            idx for idx, turn in enumerate(self.history) if turn.get("role") == "user"
        ]
        if len(user_indices) < 2:
            return

        cutoff = user_indices[-1]
        compacted = []
        for idx, turn in enumerate(self.history):
            if idx >= cutoff:
                compacted.append(turn)
                continue
            role = turn.get("role")
            if role == "session":
                continue
            if role == "assistant":
                continue
            if role == "tool" and isinstance(turn.get("content"), dict):
                compacted.append(self._compact_tool_entry(turn))
            elif role not in ("user", "reminder"):
                compacted.append(turn)
            else:
                compacted.append(turn)

        self.history = compacted

    @staticmethod
    def _compact_tool_entry(turn: HistoryEntry) -> HistoryEntry:
        content = turn.get("content")
        if not isinstance(content, dict):
            return turn
        compact: dict[str, Any] = {}
        for key in ("ok", "message", "error_type", "active_session", "tool", "valid", "hint"):
            if key in content:
                compact[key] = content[key]
        if not compact:
            compact["ok"] = content.get("ok", False)
            compact["message"] = "result truncated"
        return {
            "role": turn.get("role"),
            "tool_call_id": turn.get("tool_call_id"),
            "name": turn.get("name"),
            "content": compact,
        }

    def _missing_session_result(self, tool_name: str) -> ToolResult | None:
        if self.session.flowgraph is not None:
            return None
        return self._tool_result(
            tool_name=tool_name,
            ok=False,
            message="No flowgraph loaded.",
            error_type=ErrorCode.MISSING_SESSION,
        )

    def _history_content_as_text(
        self, content: Any, *, tool_name: str | None = None
    ) -> str:
        """Normalize stored history content into the string form chat APIs expect."""
        if (
            tool_name == "summarize_graph"
            and isinstance(content, dict)
            and isinstance(content.get("summary"), str)
        ):
            return content["summary"]
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        if isinstance(content, (dict, list)):
            return json.dumps(content, sort_keys=True)
        return str(content)

    def _session_history_content_as_text(
        self, content: Any, *, reason: Any = None
    ) -> str:
        """Render bound active-session state into a deterministic model-visible message."""
        if not isinstance(content, dict):
            return "No active session context is available."
        action = "Switched active session" if reason == "load_grc" else "Active session"
        validation = content.get("validation")
        validation_status = (
            validation.get("status")
            if isinstance(validation, dict)
            and isinstance(validation.get("status"), str)
            else "unknown"
        )
        variables_hint = ""
        blocks_hint = ""
        variable_preview = content.get("variable_preview")
        if isinstance(variable_preview, list) and variable_preview:
            variables_hint = f" variables=[{', '.join(str(item) for item in variable_preview)}];"
        block_preview = content.get("block_preview")
        if isinstance(block_preview, list) and block_preview:
            blocks_hint = f" blocks=[{', '.join(str(item) for item in block_preview[:6])}];"
        return (
            f"{action}: path={content.get('path')}, "
            f"graph_id={content.get('graph_id')}, "
            f"state_revision={content.get('state_revision')}, "
            f"dirty={content.get('dirty')}, "
            f"validation={validation_status};"
            f"{variables_hint}{blocks_hint} "
            "Use exact session instance names for session tools. Variable names like `samp_rate` are exact session instance names. "
            "Use named loaded blocks or variables with `get_grc_context`, and use `remove_block` with their `instance_name`. "
            "For describe_block use GNU block ids, not session:block or catalog:block prefixes."
        )

    def _load_grc(self, file_path: str) -> ToolResult:
        loaded = load_grc(file_path)
        if not isinstance(loaded, FlowgraphSession):
            return self._tool_result(
                "load_grc",
                ok=False,
                message=loaded.get("message", "Failed to load .grc file."),
                error_type=loaded.get("error_type", ErrorCode.FILE_LOAD_ERROR),
            )
        self._replace_session(loaded)
        payload = summarize_graph(self.session)
        result = self._payload_result(
            "load_grc",
            payload,
            default_message="Graph loaded.",
        )
        result["provenance"] = self.session.session_provenance()
        return result

    def _summarize_graph(self, max_blocks: int | None = None) -> ToolResult:
        if max_blocks is None:
            payload = summarize_graph(self.session)
        else:
            payload = summarize_graph(self.session, max_blocks=max_blocks)
        return self._payload_result(
            "summarize_graph",
            payload,
            default_message="Graph summary generated.",
        )

    def _search_grc(
        self,
        query: str,
        scope: str = "catalog",
        k: int | None = None,
    ) -> ToolResult:
        session = self.session if self.session.flowgraph is not None else None
        if k is None:
            payload = _search_grc_with_context(
                query,
                scope=scope,
                session=session,
                catalog_root=self.catalog_root,
            )
        else:
            payload = _search_grc_with_context(
                query,
                scope=scope,
                k=k,
                session=session,
                catalog_root=self.catalog_root,
            )
        if payload.get("ok") and payload.get("results"):
            payload["hint"] = (
                "Use `block_id` from block results with `describe_block`. "
                "Use `node_id` with `get_grc_context`."
            )
        elif payload.get("ok") and scope == "session" and not payload.get("results"):
            payload["hint"] = (
                "No matches in the session. "
                'Do NOT call `describe_block` with the raw query text. Retry the same query with `scope="catalog"` '
                "before you answer or validate anything else, then use the returned `block_id`."
            )
        return self._payload_result("search_grc", payload)

    def _get_grc_context(
        self,
        node_id: str,
        hops: int = 1,
        max_nodes: int = 20,
    ) -> ToolResult:
        payload = get_grc_context(self.session, node_id, hops=hops, max_nodes=max_nodes)
        if payload.get("ok"):
            payload["hint"] = (
                "This is inspection data only. "
                "If the user also asked for a real change after inspecting, call `apply_edit` next."
            )
            target = payload.get("target")
            edges = payload.get("edges")
            if (
                isinstance(target, dict)
                and target.get("block_type") == "qtgui_time_sink_x"
                and isinstance(target.get("node_id"), str)
                and isinstance(edges, list)
            ):
                for edge in edges:
                    if (
                        isinstance(edge, dict)
                        and edge.get("target") == target["node_id"]
                        and isinstance(edge.get("source"), str)
                    ):
                        payload["hint"] = (
                            "This is inspection data only. If the user asked to add a second trace to this time sink, "
                            "call `apply_edit` with one ordered transaction: first update `nconnections` to `2` on "
                            f"`{target['node_id']}`, then add_connection from `{edge['source']}` port "
                            f"{edge.get('source_port', 0)} to `{target['node_id']}` port `1`. Do not use `propose_edit` "
                            "unless the user explicitly asked for a preview."
                        )
                        break
        if payload.get("ok") is False and payload.get("error_type") == ErrorCode.BLOCK_NOT_FOUND:
            candidate_nodes: list[str] = []
            candidate_result = _search_grc_with_context(
                node_id,
                scope="session",
                k=3,
                session=self.session if self.session.flowgraph is not None else None,
                catalog_root=self.catalog_root,
            )
            if candidate_result.get("ok") and candidate_result.get("results"):
                candidate_nodes = [
                    str(result.get("node_id")).removeprefix("session:block:")
                    for result in candidate_result["results"]
                    if isinstance(result, dict)
                    and isinstance(result.get("node_id"), str)
                    and str(result.get("node_id")).startswith("session:block:")
                ]
            if candidate_nodes:
                payload["candidate_nodes"] = candidate_nodes
                payload["hint"] = (
                    "Use an exact session instance name for get_grc_context. "
                    f"Closest session matches: {', '.join(candidate_nodes)}."
                )
            elif self.session.flowgraph is not None:
                all_blocks = self.session.flowgraph.blocks
                all_entries = [
                    f"{b.instance_name} ({b.block_type})"
                    for b in all_blocks[:max_nodes]
                ]
                if all_entries:
                    payload["candidate_nodes"] = [b.instance_name for b in all_blocks[:max_nodes]]
                    payload["hint"] = (
                        "Use an exact session instance name for get_grc_context. "
                        f"All loaded session blocks: {', '.join(all_entries)}."
                    )
        return self._payload_result("get_grc_context", payload)

    def _describe_block(self, block_id: str) -> ToolResult:
        normalized_block_id = self._normalize_describe_block_id(block_id)
        payload = describe_block(normalized_block_id)
        result = self._payload_result("describe_block", payload)
        if normalized_block_id != block_id:
            result["requested_block_id"] = block_id
            result["resolved_block_id"] = normalized_block_id
        if result.get("ok") is False and str(block_id).startswith(
            ("catalog:block:", "session:block:")
        ):
            result["hint"] = (
                "describe_block expects a GNU block id such as `blocks_throttle2`. "
                "Use a search result's `block_id`, not its `node_id`."
            )
        return result

    def _propose_edit(self, transaction: Any) -> ToolResult:
        missing_session = self._missing_session_result("propose_edit")
        if missing_session is not None:
            return missing_session
        payload = propose_edit(self.session, transaction, self.catalog_root)
        result = self._payload_result("propose_edit", payload)
        if result.get("ok") and result.get("error_count", 0) == 0:
            result["hint"] = (
                "This was only a preview and did NOT modify the graph. "
                "If the user asked for the actual change, call `apply_edit` next with the same or fuller transaction."
            )
        elif result.get("ok") is False:
            result["hint"] = (
                "Preview failed. Explain the preflight errors to the user. "
                "If the user asked only for a preview, stop after explaining; do not call validate_graph. "
                + self._transaction_hint()
            )
        return result

    def _apply_edit(self, transaction: Any) -> ToolResult:
        missing_session = self._missing_session_result("apply_edit")
        if missing_session is not None:
            return missing_session
        payload = apply_edit(self.session, transaction, self.catalog_root)
        if payload.get("ok"):
            self._record_successful_validation()
        result = self._payload_result("apply_edit", payload)
        if result.get("ok"):
            result["hint"] = (
                "Edit applied and validated. Do NOT call apply_edit again for this same change. "
                "If the user explicitly asked you to validate or confirm it works, call validate_graph next. "
                "Otherwise, if the user asked to save, call save_graph."
            )
        else:
            errors = result.get("errors")
            if isinstance(errors, list) and any(
                isinstance(error, dict)
                and error.get("code") == "block_still_referenced"
                for error in errors
            ):
                result["hint"] = (
                    "This block is still referenced elsewhere. "
                    "If the user asked to keep the graph working, call apply_edit again with one repair transaction that first replaces references with literal values, then removes the block. "
                    "Do not switch to propose_edit unless the user explicitly asked for a preview. "
                    + self._transaction_hint()
                )
            elif isinstance(errors, list) and any(
                isinstance(error, dict)
                and error.get("code") == "connected_block"
                for error in errors
            ):
                conn_hint = self._build_connection_hints_for_remove_block(result)
                result["hint"] = (conn_hint + " " if conn_hint else "") + self._transaction_hint()
            else:
                result["hint"] = self._transaction_hint()
        return result

    def _validate_graph(self) -> ToolResult:
        missing_session = self._missing_session_result("validate_graph")
        if missing_session is not None:
            return missing_session
        is_valid = self.session.validate()
        if self.session.last_validation_returncode == -2:
            self._last_validation_ok = False
            self._last_validated_state_revision = None
            return self._tool_result(
                tool_name="validate_graph",
                ok=False,
                message="Graph validation timed out. Try again or simplify the graph.",
                error_type=ErrorCode.VALIDATION_TIMEOUT,
                stderr=self.session.last_validation_stderr,
            )
        if is_valid:
            self._record_successful_validation()
        else:
            self._last_validation_ok = False
            self._last_validated_state_revision = None
        return self._tool_result(
            tool_name="validate_graph",
            ok=True,
            message="Graph is valid." if is_valid else "Graph is invalid.",
            valid=is_valid,
            dirty=self.session.is_dirty,
            stdout=self.session.last_validation_stdout,
            stderr=self.session.last_validation_stderr,
            returncode=self.session.last_validation_returncode,
        )

    def _save_graph(self, path: str | None = None) -> ToolResult:
        missing_session = self._missing_session_result("save_graph")
        if missing_session is not None:
            return missing_session
        if self.session.is_dirty and (
            not self._last_validation_ok
            or self._last_validated_state_revision != self.session.state_revision
        ):
            return self._tool_result(
                tool_name="save_graph",
                ok=False,
                message=(
                    "Refusing to save a dirty graph before successful validation. "
                    "Next step: call validate_graph, then save_graph. "
                    "Do NOT call apply_edit again."
                ),
                error_type=ErrorCode.SAVE_REFUSED,
                requires_validation=True,
                dirty=True,
            )

        try:
            self.session.save(path)
        except Exception as exc:
            return self._tool_result(
                tool_name="save_graph",
                ok=False,
                message=f"Failed to save graph: {exc}",
                error_type=ErrorCode.INTERNAL_ERROR,
            )
        self._reset_validation_tracking()
        saved_path = str(self.session.path) if self.session.path is not None else None
        return self._tool_result(
            tool_name="save_graph",
            ok=True,
            message="Graph saved.",
            path=saved_path,
            dirty=self.session.is_dirty,
        )

    def _normalize_describe_block_id(self, block_id: str) -> str:
        if not isinstance(block_id, str):
            return str(block_id)
        if block_id.startswith("catalog:block:"):
            return block_id.removeprefix("catalog:block:")
        if block_id.startswith("session:block:"):
            instance_name = block_id.removeprefix("session:block:")
            resolved = self._session_instance_to_block_id(instance_name)
            return resolved if resolved is not None else instance_name
        resolved = self._session_instance_to_block_id(block_id)
        if resolved is not None:
            return resolved
        return block_id

    def _session_instance_to_block_id(self, instance_name: str) -> str | None:
        if self.session.flowgraph is None:
            return None
        for block in self.session.flowgraph.blocks:
            if block.instance_name == instance_name:
                return block.block_type
        return None

    @staticmethod
    def _block_role_hint(block_type: str) -> str:
        lowered = block_type.lower()
        if "source" in lowered:
            return "; source"
        if "sink" in lowered:
            return "; sink"
        if "throttle" in lowered:
            return "; throttle"
        if "char_to_float" in lowered:
            return "; converter"
        return ""

    @staticmethod
    def _transaction_hint() -> str:
        return (
            "Use supported transactions only. "
            "For parameter edits use update_params with instance_name and params. "
            "For remove_block use instance_name, not block_id. "
            "For detached variable add_block use block_type, instance_name, and parameters (not params). "
            "Use bare parameter keys like nconnections, srate, and value. "
            "For disconnects include src_block, src_port, dst_block, and dst_port. "
            "For a second time-sink trace, first update nconnections, then add_connection. "
            "For removing samp_rate while keeping the graph valid, replace dependent params with literals before remove_block."
        )

    def _build_connection_hints_for_remove_block(self, result: dict[str, Any]) -> str:
        """Return a concrete remove_connection + remove_block transaction hint for connected blocks."""
        if self.session.flowgraph is None:
            return ""
        normalized_ops = result.get("normalized_operations")
        if not isinstance(normalized_ops, list):
            return ""
        parts = []
        for op in normalized_ops:
            if not isinstance(op, dict) or op.get("op_type") != "remove_block":
                continue
            instance_name = op.get("instance_name")
            if not isinstance(instance_name, str):
                continue
            conns = [
                c
                for c in self.session.flowgraph.connections
                if c.src_block == instance_name or c.dst_block == instance_name
            ]
            if not conns:
                continue
            remove_conn_ops = ", ".join(
                f'{{"op_type": "remove_connection", "src_block": "{c.src_block}", '
                f'"src_port": {c.src_port}, "dst_block": "{c.dst_block}", "dst_port": {c.dst_port}}}'
                for c in conns
            )
            parts.append(
                f"`{instance_name}` is still connected. "
                f"To disconnect and remove it, use this ordered transaction: "
                f"[{remove_conn_ops}, "
                f'{{"op_type": "remove_block", "instance_name": "{instance_name}"}}]'
            )
        return " ".join(parts)
