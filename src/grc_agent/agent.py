"""Thin runtime wrapper for routed package-level `.grc` tools."""

import json
import logging
from typing import Any, Callable

from grc_agent.catalog import describe_block
from grc_agent._payload import ErrorCode
from grc_agent.config import AgentConfig, default_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval.search import _search_grc_with_context
from grc_agent.runtime.prompt import build_system_prompt
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.transaction_normalization import TransactionNormalizer
from grc_agent.runtime_tool_validation import (
    build_tool_schema_map,
    validate_runtime_tool_call,
)
from grc_agent.session import get_grc_context, load_grc, summarize_graph
from grc_agent.session.insertion_suggestions import suggest_insertions
from grc_agent.transaction import apply_edit, propose_edit
from grc_agent.turn_guard import build_continuation_prompt, parse_required_actions

logger = logging.getLogger(__name__)

ToolResult = dict[str, Any]
ToolCallable = Callable[..., ToolResult]
HistoryEntry = dict[str, Any]


class GrcAgent:
    """A thin integration layer between a language model and package-level owners."""

    _RAW_YAML_EDIT_PATTERNS: tuple[tuple[str, ...], ...] = (
        ("yaml", "direct"),
        ("yaml", "manual"),
        ("yaml", "text"),
        ("yaml", "raw"),
        (".grc", "yaml", "edit"),
        (".grc", "yaml", "direct"),
        (".grc", "yaml", "raw"),
        ("raw", ".grc"),
        ("raw", "yaml"),
        ("patch", "yaml"),
        ("modify", "yaml", "text"),
        ("edit", "yaml", "remove"),
        ("edit", "yaml", "block"),
    )

    def __init__(
        self,
        session: FlowgraphSession | None = None,
        *,
        catalog_root: str | None = None,
        config: AgentConfig | None = None,
    ) -> None:
        self.session = FlowgraphSession() if session is None else session
        self.catalog_root = str(catalog_root) if catalog_root is not None else None
        self.config = config or default_app_config().agent
        self.history: list[HistoryEntry] = []
        self._last_validated_state_revision: int | None = None
        self._last_validation_ok: bool | None = None
        self._reset_validation_tracking()
        self._tools = self._build_tool_registry()
        self._tool_schemas = build_tool_schemas()
        self._tool_schema_map = build_tool_schema_map(self._tool_schemas)
        self._record_active_session_history(reason="initial_session")
        self._turn_required_actions: set[str] = set()
        self._turn_completed_actions: set[str] = set()
        self._turn_any_execution_failed = False
        self._turn_continuation_budget = 0
        self._transaction_normalizer = TransactionNormalizer(session=self.session)

    def get_system_prompt(self) -> str:
        return build_system_prompt()

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return the fixed tool schemas exposed to a chat-completions client."""
        return self._tool_schemas

    def execute_tool(self, tool_name: str, kwargs: dict[str, Any]) -> ToolResult:
        """Execute one runtime tool and return a structured result."""
        kwargs = self.normalize_tool_call_arguments(tool_name, kwargs)
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

    def normalize_tool_call_arguments(
        self,
        tool_name: str,
        kwargs: Any,
    ) -> dict[str, Any]:
        if not isinstance(kwargs, dict):
            return {}

        normalized: dict[str, Any] = {}
        for raw_key, raw_value in kwargs.items():
            key, inline_value = self._transaction_normalizer.normalize_tool_argument_key(raw_key)
            value = inline_value if inline_value is not None else raw_value
            if key == "transaction":
                value = self._transaction_normalizer.normalize_transaction_instance_names(value)
            normalized[key] = value
        return normalized

    def validate_tool_call(self, tool_name: str, kwargs: Any) -> ToolResult | None:
        """Validate one runtime tool call against the declared public schema."""
        kwargs = self.normalize_tool_call_arguments(tool_name, kwargs)
        validation_error = validate_runtime_tool_call(
            tool_name, kwargs, self._tool_schema_map
        )
        if validation_error is None:
            return None
        return self._tool_result(tool_name=tool_name, ok=False, **validation_error)

    def should_stop_batch_after_result(
        self, tool_name: str, result: dict[str, Any]
    ) -> bool:
        if not isinstance(result, dict) or result.get("ok") is not False:
            return False
        return tool_name in {"new_grc", "load_grc", "apply_edit", "validate_graph", "save_graph"}

    def check_unsupported_request(self, user_message: str) -> dict[str, Any] | None:
        """Return a refusal response if the user message requests unsupported raw YAML editing."""
        lowered = user_message.lower()
        for keywords in self._RAW_YAML_EDIT_PATTERNS:
            if all(kw in lowered for kw in keywords):
                return {
                    "ok": True,
                    "model": "guard",
                    "steps": 0,
                    "tool_rounds_used": 0,
                    "tool_calls_executed": 0,
                    "assistant_text": (
                        "Raw .grc YAML editing is unsupported. "
                        "Use validated GRC tools instead: apply_edit for mutations, "
                        "propose_edit for previews, save_graph to persist changes."
                    ),
                }
        return None

    def init_turn_requirements(self, user_message: str) -> None:
        """Parse user message and initialise turn-completion tracking."""
        self._turn_required_actions = parse_required_actions(user_message)
        self._turn_completed_actions = set()
        self._turn_any_execution_failed = False
        self._turn_continuation_budget = 1

    def record_tool_completion(self, tool_name: str, ok: bool) -> None:
        """Record a tool execution result for turn-completion tracking."""
        if ok and tool_name in self._turn_required_actions:
            self._turn_completed_actions.add(tool_name)
        if not ok:
            self._turn_any_execution_failed = True

    def check_turn_continuation(self) -> tuple[bool, str]:
        """Return (should_nudge, nudge_text) if remaining actions need a nudge."""
        remaining = self._turn_required_actions - self._turn_completed_actions
        if not remaining or self._turn_continuation_budget <= 0 or self._turn_any_execution_failed:
            return False, ""
        self._turn_continuation_budget -= 1
        return True, build_continuation_prompt(remaining)

    @staticmethod
    def looks_like_transaction_payload(payload: Any) -> bool:
        return TransactionNormalizer.looks_like_transaction_payload(payload)

    def health_check(self) -> dict[str, Any]:
        """Return a structured health payload describing agent readiness."""
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
        try:
            return self.session.active_session_snapshot()
        except ValueError:
            return None

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

    # ------------------------------------------------------------------- #
    # Compact history helpers
    # ------------------------------------------------------------------- #

    def compact_history(self) -> None:
        """Reduce history token cost before a new multi-turn conversation turn."""
        last_session_index: int | None = None
        user_indices: list[int] = []
        total_chars = 0
        for index, turn in enumerate(self.history):
            role = turn.get("role")
            if role == "session":
                last_session_index = index
            if role == "user":
                user_indices.append(index)
            total_chars += len(str(turn))

        previous_turn_start = user_indices[-2] if len(user_indices) >= 2 else None

        if last_session_index is not None or previous_turn_start is not None:
            compacted = []
            for idx, turn in enumerate(self.history):
                role = turn.get("role")
                if role == "session" and idx != last_session_index:
                    continue
                if (
                    role == "tool"
                    and previous_turn_start is not None
                    and idx < previous_turn_start
                    and isinstance(turn.get("content"), dict)
                ):
                    compacted.append(self._compact_tool_entry(turn))
                else:
                    compacted.append(turn)
            self.history = compacted
            total_chars = sum(len(str(turn)) for turn in self.history)

        self._proactive_compact_if_needed(total_chars=total_chars, user_indices=user_indices)
        logger.debug("compact_history history_len=%d", len(self.history))

    def _proactive_compact_if_needed(
        self,
        *,
        total_chars: int | None = None,
        user_indices: list[int] | None = None,
    ) -> None:
        """Drop older assistant/tool detail when history exceeds the char budget."""
        if total_chars is None:
            total_chars = sum(len(str(turn)) for turn in self.history)
        if total_chars <= self.config.history_compact_budget:
            return

        if user_indices is None:
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
            else:
                compacted.append(turn)

        self.history = compacted

    @staticmethod
    def _compact_tool_entry(turn: HistoryEntry) -> HistoryEntry:
        content = turn.get("content")
        if not isinstance(content, dict):
            return turn
        compact: dict[str, Any] = {}
        for key in (
            "ok",
            "message",
            "error_type",
            "active_session",
            "tool",
            "valid",
            "hint",
            "suggested_next_tools",
        ):
            if key in content:
                compact[key] = content[key]
        tool_name = turn.get("name")
        if tool_name == "summarize_graph":
            summary = content.get("summary")
            if isinstance(summary, str) and summary:
                compact["summary"] = summary
        if tool_name == "search_grc":
            for key in ("query", "scope"):
                value = content.get(key)
                if isinstance(value, str) and value:
                    compact[key] = value
            results_preview = GrcAgent._search_result_preview(content.get("results"))
            if results_preview:
                compact["results_preview"] = results_preview
            fallback_preview = GrcAgent._search_result_preview(
                content.get("catalog_fallback_preview")
            )
            if fallback_preview:
                compact["catalog_fallback_preview"] = fallback_preview
        if not compact:
            compact["ok"] = content.get("ok", False)
            compact["message"] = "result truncated"
        return {
            "role": turn.get("role"),
            "tool_call_id": turn.get("tool_call_id"),
            "name": turn.get("name"),
            "content": compact,
        }

    # ------------------------------------------------------------------- #
    # History content formatting
    # ------------------------------------------------------------------- #

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
        if isinstance(content, dict) and tool_name is not None:
            return self._tool_history_content_as_text(content, tool_name=tool_name)
        if isinstance(content, (dict, list)):
            return json.dumps(content, sort_keys=True)
        return str(content)

    def _tool_history_content_as_text(
        self,
        content: dict[str, Any],
        *,
        tool_name: str,
    ) -> str:
        """Render one tool result with the next-step hint made prominent for the model."""
        compact = dict(content)
        validation = compact.get("validation")
        if isinstance(validation, dict):
            compact["validation"] = {
                "status": validation.get("status"),
                "returncode": validation.get("returncode"),
            }

        active_session = compact.get("active_session")
        if isinstance(active_session, dict):
            active_validation = active_session.get("validation")
            compact["active_session"] = {
                "path": active_session.get("path"),
                "graph_id": active_session.get("graph_id"),
                "state_revision": active_session.get("state_revision"),
                "dirty": active_session.get("dirty"),
                "validation": {
                    "status": active_validation.get("status"),
                    "returncode": active_validation.get("returncode"),
                }
                if isinstance(active_validation, dict)
                else active_validation,
                "variable_preview": active_session.get("variable_preview"),
                "block_preview": active_session.get("block_preview"),
            }

        if tool_name == "search_grc":
            compact.pop("results", None)
            history_preview = self._search_result_preview(
                content.get("results"),
                include_summary=False,
            )
            if history_preview:
                compact["results_preview"] = history_preview
            fallback_preview = self._search_result_preview(
                content.get("catalog_fallback_preview"),
                include_summary=False,
            )
            if fallback_preview:
                compact["catalog_fallback_preview"] = fallback_preview

        if tool_name == "get_grc_context":
            compact.pop("nodes", None)
            target = compact.get("target")
            if isinstance(target, dict):
                compact["target"] = {
                    key: target.get(key)
                    for key in ("node_id", "label", "block_type", "incoming", "outgoing")
                    if key in target
                }

        if tool_name == "apply_edit" and compact.get("ok") is True:
            compact["message"] = "Edit applied. Internal compile check passed."

        lines = [f"{tool_name} result"]
        ok = compact.get("ok")
        if isinstance(ok, bool):
            lines[0] = f"{lines[0]}: ok={ok}"
        message = compact.get("message")
        if isinstance(message, str) and message:
            lines.append(f"message: {message}")
        hint = compact.get("hint")
        if isinstance(hint, str) and hint:
            lines.append(f"hint: {hint}")
        if tool_name == "search_grc" and (
            compact.get("results_preview") or compact.get("catalog_fallback_preview")
        ):
            lines.append(
                "next_step_note: search previews are routing only; for later follow-ups like `what does that block look like?`, call describe_block with the stored block_id, not get_grc_context."
            )
        if tool_name == "get_grc_context":
            lines.append(
                "next_step_note: inspection data is routing only; do not answer later edit or preview requests from it."
            )
        lines.append(json.dumps(compact, sort_keys=True))
        return "\n".join(lines)

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
        if reason != "turn_refresh":
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
            f"{variables_hint}{blocks_hint}"
        )

    # ------------------------------------------------------------------- #
    # Search result compacting helper
    # ------------------------------------------------------------------- #

    @staticmethod
    def _search_result_preview(
        results: Any,
        *,
        max_items: int = 3,
        include_summary: bool = True,
    ) -> list[dict[str, str]]:
        if not isinstance(results, list):
            return []
        preview: list[dict[str, str]] = []
        for item in results[:max_items]:
            if not isinstance(item, dict):
                continue
            compact: dict[str, str] = {}
            keys = ["block_id", "node_id", "label"]
            if include_summary:
                keys.append("summary")
            for key in keys:
                value = item.get(key)
                if isinstance(value, str) and value:
                    compact[key] = value
            if compact:
                preview.append(compact)
        return preview

    # ------------------------------------------------------------------- #
    # Tool registry builders
    # ------------------------------------------------------------------- #

    def _build_tool_registry(self) -> dict[str, ToolCallable]:
        return {
            "new_grc": self._new_grc,
            "load_grc": self._load_grc,
            "summarize_graph": self._summarize_graph,
            "search_grc": self._search_grc,
            "get_grc_context": self._get_grc_context,
            "describe_block": self._describe_block,
            "suggest_compatible_insertions": self._suggest_compatible_insertions,
            "insert_block_on_connection": self._insert_block_on_connection,
            "auto_insert_block": self._auto_insert_block,
            "apply_edit": self._apply_edit,
            "propose_edit": self._propose_edit,
            "validate_graph": self._validate_graph,
            "save_graph": self._save_graph,
        }

    # ------------------------------------------------------------------- #
    # Session / validation helpers
    # ------------------------------------------------------------------- #

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
        self._transaction_normalizer = TransactionNormalizer(session=session)
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

    def _missing_session_result(self, tool_name: str) -> ToolResult | None:
        if self.session.flowgraph is not None:
            return None
        return self._tool_result(
            tool_name=tool_name,
            ok=False,
            message="No flowgraph loaded.",
            error_type=ErrorCode.MISSING_SESSION,
        )

    # ================================================================= #
    # Tool handlers
    # ================================================================= #

    def _new_grc(self, profile: str = "minimal", graph_id: str | None = None) -> ToolResult:
        if profile != "minimal":
            return self._tool_result(
                "new_grc",
                ok=False,
                message=f"Unsupported profile: {profile!r}. Only 'minimal' is supported.",
                error_type=ErrorCode.INVALID_REQUEST,
            )
        resolved_id = graph_id if graph_id is not None else "new_flowgraph"
        new_session = FlowgraphSession.create(graph_id=resolved_id)
        self._replace_session(new_session)
        result = self._tool_result(
            "new_grc",
            ok=True,
            message="Empty flowgraph session created. Use apply_edit to add blocks and connections.",
        )
        result["provenance"] = self.session.session_provenance()
        return result

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
        result = self._payload_result("load_grc", payload, default_message="Graph loaded.")
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

    def _search_grc(self, query: str, scope: str = "catalog", k: int | None = None) -> ToolResult:
        session_ctx = self.session if self.session.flowgraph is not None else None
        if k is None:
            payload = _search_grc_with_context(
                query,
                scope=scope,
                session=session_ctx,
                catalog_root=self.catalog_root,
            )
        else:
            payload = _search_grc_with_context(
                query,
                scope=scope,
                k=k,
                session=session_ctx,
                catalog_root=self.catalog_root,
            )
        if payload.get("ok") and payload.get("results"):
            payload["hint"] = (
                "Use `block_id` from block results with `describe_block`, including later follow-ups like `what does that block look like?` or requests for ports and parameters. "
                "Use `node_id` with `get_grc_context` only for loaded session blocks."
            )
        elif payload.get("ok") and scope == "session" and not payload.get("results"):
            if k is None:
                fallback = _search_grc_with_context(
                    query,
                    scope="catalog",
                    session=session_ctx,
                    catalog_root=self.catalog_root,
                )
            else:
                fallback = _search_grc_with_context(
                    query,
                    scope="catalog",
                    k=k,
                    session=session_ctx,
                    catalog_root=self.catalog_root,
                )
            fallback_preview = self._search_result_preview(fallback.get("results"))
            if fallback.get("ok") and fallback_preview:
                payload["catalog_fallback_preview"] = fallback_preview
                first_block_id = next(
                    (
                        item.get("block_id")
                        for item in fallback_preview
                        if isinstance(item.get("block_id"), str)
                    ),
                    None,
                )
                if isinstance(first_block_id, str) and first_block_id:
                    payload["hint"] = (
                        "No matches in the session. Catalog fallback preview is included. "
                        f"If the user refers to the first result, call `describe_block(block_id=\"{first_block_id}\")`. "
                        'If the user still wants the search itself, rerun the same query with `scope="catalog"`.'
                    )
                else:
                    payload["hint"] = (
                        "No matches in the session. Catalog fallback preview is included. "
                        'Retry the same query with `scope="catalog"` before you answer or validate anything else, then use the returned `block_id`.'
                    )
            else:
                payload["hint"] = (
                    "No matches in the session. "
                    'Do NOT call `describe_block` with the raw query text. Retry the same query with `scope="catalog"` '
                    "before you answer or validate anything else, then use the returned `block_id`."
                )
        result = self._payload_result("search_grc", payload)
        return result

    def _get_grc_context(self, node_id: str, hops: int = 1, max_nodes: int = 20) -> ToolResult:
        resolved_node_id = self._resolve_symbol_like_name(node_id) or node_id
        payload = get_grc_context(
            self.session,
            resolved_node_id,
            hops=hops,
            max_nodes=max_nodes,
        )
        if payload.get("ok"):
            payload["hint"] = (
                "This is inspection data only. "
                "If the user also asked for a real change after inspecting, call `apply_edit` next."
            )
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
                payload["hint"] = f"Closest session matches: {', '.join(candidate_nodes)}."
            elif self.session.flowgraph is not None:
                fallback_candidates = [
                    b.instance_name for b in self.session.flowgraph.blocks[: min(5, max_nodes)]
                ]
                if fallback_candidates:
                    payload["candidate_nodes"] = fallback_candidates
                    payload["hint"] = (
                        "Use an exact loaded session name. "
                        f"Examples: {', '.join(fallback_candidates)}."
                    )
        result = self._payload_result("get_grc_context", payload)
        return result

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

    def _suggest_compatible_insertions(self, connection_id: str, k: int = 5) -> ToolResult:
        """Read-only suggestion for blocks that can be inserted into a connection."""
        result = suggest_insertions(self.session, connection_id, k)
        payload = {
            "ok": result.ok,
            "connection_id": result.connection_id,
        }
        if not result.ok:
            payload["error_type"] = result.error_type
            payload["message"] = result.message
        else:
            payload["source"] = {
                "block": result.source.block,
                "port": result.source.port,
                "dtype": result.source.dtype,
                "vlen": result.source.vlen,
                "domain": result.source.domain,
            } if result.source else None
            payload["destination"] = {
                "block": result.destination.block,
                "port": result.destination.port,
                "dtype": result.destination.dtype,
                "vlen": result.destination.vlen,
                "domain": result.destination.domain,
            } if result.destination else None
            payload["candidates"] = [
                {
                    "block_type": c.block_type,
                    "reason": c.reason,
                    "required_params": c.required_params,
                    "confidence": c.confidence,
                    "insert_tool_args": c.insert_tool_args,
                }
                for c in result.candidates
            ]
        return self._payload_result("suggest_compatible_insertions", payload)

    def _insert_block_on_connection(
        self,
        connection_id: str,
        block_type: str,
        instance_name: str,
        params: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Thin wrapper: delegates to apply_edit with op_type=insert_block_on_connection."""
        transaction = {
            "op_type": "insert_block_on_connection",
            "connection_id": connection_id,
            "block_type": block_type,
            "instance_name": instance_name,
            "params": params or {},
        }
        return self._apply_edit(transaction)

    def _auto_insert_block(
        self,
        goal: str,
        preferred_block_type: str | None = None,
        target_hint: str | None = None,
        max_candidates: int = 10,
    ) -> ToolResult:
        """Bounded agentic insert workflow: search, score, try, commit one validated candidate."""
        from grc_agent.session.auto_insert import auto_insert_block

        missing_session = self._missing_session_result("auto_insert_block")
        if missing_session is not None:
            return missing_session

        payload = auto_insert_block(
            session=self.session,
            goal=goal,
            preferred_block_type=preferred_block_type,
            target_hint=target_hint,
            max_candidates=max_candidates,
            catalog_root=self.catalog_root,
        )
        if payload.get("ok"):
            self._record_successful_validation()
        return self._payload_result("auto_insert_block", payload)

    def _propose_edit(self, transaction: Any) -> ToolResult:
        missing_session = self._missing_session_result("propose_edit")
        if missing_session is not None:
            return missing_session
        payload = propose_edit(
            self.session,
            self._transaction_normalizer.normalize_transaction_instance_names(transaction),
            self.catalog_root,
        )
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
                + TransactionNormalizer.transaction_hint()
            )
        return result

    def _state_driven_suggested_next_tools(self, *, completed_tool: str) -> list[str]:
        """Derive follow-up tool suggestions from session state, not from prompt text."""
        suggestions: list[str] = []

        if completed_tool == "apply_edit" and self.session.is_dirty:
            suggestions.append("validate_graph")

        if completed_tool == "validate_graph" and self._last_validation_ok:
            if self.session.is_dirty:
                suggestions.append("save_graph")

        if (
            completed_tool == "save_graph"
            and self.session.flowgraph is not None
            and self._last_validation_ok
        ):
            suggestions.append("summarize_graph")

        return suggestions

    def _apply_edit(self, transaction: Any) -> ToolResult:
        missing_session = self._missing_session_result("apply_edit")
        if missing_session is not None:
            return missing_session
        payload = apply_edit(
            self.session,
            self._transaction_normalizer.normalize_transaction_instance_names(transaction),
            self.catalog_root,
        )
        if payload.get("ok"):
            self._record_successful_validation()
        result = self._payload_result("apply_edit", payload)
        if result.get("ok"):
            suggested = self._state_driven_suggested_next_tools(completed_tool="apply_edit")
            result["hint"] = (
                "Edit applied. Do NOT call apply_edit again for this same change. "
                "apply_edit already ran an internal compile check; that does NOT satisfy an explicit `validate_graph` request. "
                "If the user explicitly asked to validate or confirm it works, call `validate_graph` next. "
                "Otherwise, if the user asked to save, call `save_graph`."
            )
            if suggested:
                result["suggested_next_tools"] = suggested
        else:
            errors = result.get("errors")
            if isinstance(errors, list) and any(
                isinstance(error, dict)
                and error.get("code") == "block_still_referenced"
                for error in errors
            ):
                dep_hint = self._transaction_normalizer.build_dependency_repair_hint(result)
                result["hint"] = (
                    "This block is still referenced by other blocks. "
                    "To remove it, first update every dependent parameter to use a literal value, then remove the block. "
                    + (dep_hint + " " if dep_hint else "")
                    + TransactionNormalizer.transaction_hint()
                )
            elif isinstance(errors, list) and any(
                isinstance(error, dict)
                and error.get("code") == "connected_block"
                for error in errors
            ):
                conn_hint = self._transaction_normalizer.build_connection_hints_for_remove_block(result)
                result["hint"] = (conn_hint + " " if conn_hint else "") + TransactionNormalizer.transaction_hint()
            else:
                result["hint"] = TransactionNormalizer.transaction_hint()
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
        result = self._tool_result(
            tool_name="validate_graph",
            ok=True,
            message="Graph is valid." if is_valid else "Graph is invalid.",
            valid=is_valid,
            dirty=self.session.is_dirty,
            stdout=self.session.last_validation_stdout,
            stderr=self.session.last_validation_stderr,
            returncode=self.session.last_validation_returncode,
        )
        if is_valid:
            suggested = self._state_driven_suggested_next_tools(completed_tool="validate_graph")
            result["hint"] = (
                "Validation passed. If the user also asked to save, call `save_graph`. "
                "If the user also asked for a summary, call `summarize_graph`."
            )
            if suggested:
                result["suggested_next_tools"] = suggested
        return result

    def _save_graph(self, path: str | None = None) -> ToolResult:
        missing_session = self._missing_session_result("save_graph")
        if missing_session is not None:
            return missing_session
        if path is None and self.session.path is None:
            return self._tool_result(
                tool_name="save_graph",
                ok=False,
                message="This new graph has no file path yet. Call save_graph(path=\"...\").",
                error_type="SAVE_PATH_REQUIRED",
            )
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

    # ================================================================= #
    # Block / symbol helpers
    # ================================================================= #

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

    def _resolve_symbol_like_name(self, identifier: str) -> str | None:
        return self._transaction_normalizer._resolve_symbol_like_name(identifier)
