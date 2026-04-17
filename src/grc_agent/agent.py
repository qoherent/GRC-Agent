"""Thin runtime wrapper for routed package-level `.grc` tools."""

import json
from typing import Any, Callable

from grc_agent.catalog import describe_block
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval import search_grc
from grc_agent.retrieval.search import bind_retrieval_context
from grc_agent.runtime_tool_validation import (
    build_tool_schema_map,
    validate_runtime_tool_call,
)
from grc_agent.session import get_grc_context, load_grc, summarize_graph
from grc_agent.transaction import apply_edit, propose_edit

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
        self._sync_retrieval_context()
        self._record_active_session_history(reason="initial_session")

    def get_system_prompt(self) -> str:
        return (
            "You are a GRC (GNU Radio Companion) Agent.\n"
            "Your job is to inspect and safely modify `.grc` files using only the provided tools.\n"
            "Decision rules:\n"
            "1. The active session context tells you which `.grc` file is loaded. "
            "Use `load_grc` only when the user explicitly asks to switch files.\n"
            "2. Scope selection: if the user says my graph / this graph / current graph / in here, "
            'use `scope="session"`. If the user says find me / available / in GNU Radio / library / '
            "OFDM / PSK / QAM / equalizer / channelizer / spread spectrum / carrier / scrambler, "
            'use `scope="catalog"`.\n'
            "3. `get_grc_context` needs an exact session instance name like `blocks_throttle2_0`. "
            "`describe_block` needs a GNU block id like `blocks_throttle2` or `qtgui_time_sink_x`.\n"
            "4. After `search_grc`, block results include `block_id`. "
            "If the user asked to explain a block, call `describe_block` with that `block_id`. "
            "Never pass `catalog:block:...` or `session:block:...` into `describe_block`.\n"
            "5. If the user already names a GNU block id or a clear block family like `blocks_char_to_float`, "
            "prefer `describe_block` instead of `search_grc`.\n"
            "6. When the user asks to change, set, update, remove, add, connect, disconnect, "
            "or modify anything, ALWAYS call `apply_edit`. "
            "ONLY use `propose_edit` when the user explicitly says "
            "preview / dry-run / what-if / would it work.\n"
            "7. For parameter edits, use transactions shaped like "
            '`{"op_type": "update_params", "instance_name": "samp_rate", "params": {"value": "48000"}}`. '
            "When the user says sample rate, speed, or rate, they usually mean the `samp_rate` variable. "
            "Expand abbreviations: 48k=48000, 8k=8000, 96k=96000.\n"
            "8. Supported `op_type` values: `update_params`, `add_connection`, `remove_connection`, "
            "`remove_block`, and detached-variable `add_block`. Do not invent wrappers or new op types. "
            "Example add_block: "
            '`{"op_type": "add_block", "block_type": "variable", "instance_name": "debug_flag", '
            '"params": {"value": "0"}}`\n'
            "9. For rewires, pass all operations in one ordered transaction list. "
            "To add a second trace to the time sink: "
            '`[{"op_type": "update_params", "instance_name": "qtgui_time_sink_x_0", '
            '"params": {"nconnections": "2"}}, {"op_type": "add_connection", '
            '"src_block": "blocks_char_to_float_0", "src_port": 0, '
            '"dst_block": "qtgui_time_sink_x_0", "dst_port": 1}]`. '
            "Always expand `nconnections` before adding the connection in the same transaction. "
            "`remove_connection` needs `src_block`, `src_port`, `dst_block`, `dst_port`.\n"
            "10. Complete every requested step in order before answering. "
            "If the user said look / inspect / check / show first, you MUST call an inspection tool "
            "(summarize_graph, get_grc_context, search_grc, or describe_block) before any edit. "
            "Do not skip to apply_edit when the user asked to inspect first. "
            "If the user asked to apply and validate, call `validate_graph` after a successful edit. "
            "Only call `save_graph` after successful validation of the current dirty state.\n"
            "11. If the user asks for unsupported operations (undo, redo, export as Python, "
            "edit raw YAML, generate code), do not call a tool; answer briefly that it is unsupported.\n"
            "12. After `summarize_graph`, copy the tool summary verbatim as your final answer. "
            "After other successful flows, return one short factual sentence.\n"
            "13. When a tool returns `ok: false`, report the error message to the user. "
            "Do not silently retry with different arguments unless the error hints at a fix."
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
                "Return a bounded summary of the loaded GNU Radio graph.",
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
                "Use this when the user wants to discover or find a block "
                "(e.g. filtering, modulation, carrier recovery, scrambling, OFDM, PSK, QAM, "
                "equalizer, channelizer, spread spectrum, frequency hopping, AGC, sink, source). "
                'Use `scope="session"` for the loaded graph and `scope="catalog"` for GNU Radio discovery. '
                "Block results include `block_id` for `describe_block` and `node_id` for `get_grc_context`. "
                "Do NOT use this when the user already names a specific GNU block id — use `describe_block` instead.",
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
                "Use this when the user asks how blocks are wired, connected, routed, or linked. "
                "Pass the exact loaded session instance name (e.g. `blocks_throttle2_0`), not a catalog id. "
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
                "If you searched first, use the result's `block_id` field.",
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
                "For disconnects, include all four endpoint fields. "
                "For second-trace rewires, pass an ordered list: first update `nconnections`, then `add_connection`. "
                "For adding a detached variable, pass "
                '`{"transaction": {"op_type": "add_block", "block_type": "variable", '
                '"instance_name": "my_var", "params": {"value": "0"}}}`',
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
                "ONLY use this when the user explicitly says preview / dry-run / what-if / would it work. "
                "For all other edit requests, use `apply_edit` instead. "
                "For parameter edits, pass "
                '`{"transaction": {"op_type": "update_params", "instance_name": "samp_rate", '
                '"params": {"value": "48000"}}}`. Supported `op_type`: `update_params`, '
                "`add_connection`, `remove_connection`, `remove_block`, detached-variable `add_block`. "
                "For rewires, use ordered transaction lists when one step enables another. "
                "For adding a detached variable, pass "
                '`{"transaction": {"op_type": "add_block", "block_type": "variable", '
                '"instance_name": "my_var", "params": {"value": "0"}}}`',
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
        """Execute one runtime tool and return a structured result."""
        validation_result = self.validate_tool_call(tool_name, kwargs)
        if validation_result is not None:
            return validation_result

        func = self._tools[tool_name]
        try:
            return func(**kwargs)
        except Exception as error:
            return self._tool_result(
                tool_name=tool_name,
                ok=False,
                message=str(error),
                error_type=type(error).__name__,
            )

    def validate_tool_call(self, tool_name: str, kwargs: Any) -> ToolResult | None:
        """Validate one runtime tool call against the declared public schema."""
        validation_error = validate_runtime_tool_call(
            tool_name, kwargs, self._tool_schema_map
        )
        if validation_error is None:
            return None
        return self._tool_result(tool_name=tool_name, ok=False, **validation_error)

    def active_session_snapshot(self) -> dict[str, Any] | None:
        """Return the compact active-session payload exposed in runtime history and CLI output."""
        if self.session.flowgraph is None:
            return None
        snapshot = self.session.session_provenance()
        snapshot["state_revision"] = self.session.state_revision
        snapshot["dirty"] = self.session.is_dirty
        snapshot["validation"] = self.session.validation_state()
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
                print(f"Assistant: {action['text']}")

            if "tool" in action:
                tool_name = action["tool"]
                kwargs = action.get("kwargs", {})
                print(f"Assistant called {tool_name} with {kwargs}")

                self.history.append(
                    {
                        "role": "assistant",
                        "tool_calls": [{"name": tool_name, "arguments": kwargs}],
                    }
                )

                result = self.execute_tool(tool_name, kwargs)
                print(f"Tool {tool_name} responded: {result}")

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

    def _sync_retrieval_context(self) -> None:
        """Keep the session-search package bound to the current live session."""
        bind_retrieval_context(
            session=self.session if self.session.flowgraph is not None else None,
            catalog_root=self.catalog_root,
        )

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
        self._sync_retrieval_context()
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

    def _missing_session_result(self, tool_name: str) -> ToolResult | None:
        if self.session.flowgraph is not None:
            return None
        return self._tool_result(
            tool_name=tool_name,
            ok=False,
            message="No flowgraph loaded.",
            error_type="MissingSession",
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
        if self.session.flowgraph is not None:
            var_parts = []
            block_parts = []
            for block in self.session.flowgraph.blocks:
                if block.block_type == "variable":
                    val = block.params.get("parameters", {}).get("value", "")
                    var_parts.append(f"{block.instance_name}={val}")
                    continue
                block_parts.append(
                    f"{block.instance_name} ({block.block_type}{self._block_role_hint(block.block_type)})"
                )
            if var_parts:
                variables_hint = f" variables=[{', '.join(var_parts)}];"
            if block_parts:
                blocks_hint = f" blocks=[{', '.join(block_parts[:6])}];"
        return (
            f"{action}: path={content.get('path')}, "
            f"graph_id={content.get('graph_id')}, "
            f"state_revision={content.get('state_revision')}, "
            f"dirty={content.get('dirty')}, "
            f"validation={validation_status};"
            f"{variables_hint}{blocks_hint} "
            "Use exact session instance names for session tools. "
            "For describe_block use GNU block ids, not session:block or catalog:block prefixes."
        )

    def _load_grc(self, file_path: str) -> ToolResult:
        loaded = load_grc(file_path)
        if not isinstance(loaded, FlowgraphSession):
            return self._tool_result(
                "load_grc",
                ok=False,
                message=loaded.get("message", "Failed to load .grc file."),
                error_type=loaded.get("error_type", "FileLoadError"),
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
        self._sync_retrieval_context()
        if k is None:
            payload = search_grc(query, scope=scope)
        else:
            payload = search_grc(query, scope=scope, k=k)
        if payload.get("ok") and payload.get("results"):
            payload["hint"] = (
                "Use `block_id` from block results with describe_block. "
                "Use `node_id` with get_grc_context."
            )
        elif payload.get("ok") and scope == "session" and not payload.get("results"):
            payload["hint"] = (
                "No matches in the session. "
                'Retry with `scope="catalog"` to search the GNU Radio block library.'
            )
        return self._payload_result("search_grc", payload)

    def _get_grc_context(
        self,
        node_id: str,
        hops: int = 1,
        max_nodes: int = 20,
    ) -> ToolResult:
        payload = get_grc_context(self.session, node_id, hops=hops, max_nodes=max_nodes)
        if payload.get("ok") is False and payload.get("error_type") == "node_not_found":
            candidate_result = search_grc(node_id, scope="session", k=3)
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
                "To commit this change, call apply_edit with the same transaction."
            )
        elif result.get("ok") is False:
            result["hint"] = self._transaction_hint()
        return result

    def _apply_edit(self, transaction: Any) -> ToolResult:
        missing_session = self._missing_session_result("apply_edit")
        if missing_session is not None:
            return missing_session
        payload = apply_edit(self.session, transaction, self.catalog_root)
        if payload.get("ok"):
            self._record_successful_validation()
        self._sync_retrieval_context()
        result = self._payload_result("apply_edit", payload)
        if result.get("ok"):
            result["hint"] = (
                "If the user also asked to confirm the graph still works, call validate_graph next."
            )
        else:
            result["hint"] = self._transaction_hint()
        return result

    def _validate_graph(self) -> ToolResult:
        missing_session = self._missing_session_result("validate_graph")
        if missing_session is not None:
            return missing_session
        is_valid = self.session.validate()
        self._last_validation_ok = is_valid
        self._last_validated_state_revision = (
            self.session.state_revision if is_valid else None
        )
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
                message="Refusing to save a dirty graph before successful validation.",
                requires_validation=True,
                dirty=True,
            )

        self.session.save(path)
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
            "For disconnects include src_block, src_port, dst_block, and dst_port. "
            "For a second time-sink trace, first update nconnections, then add_connection."
        )
