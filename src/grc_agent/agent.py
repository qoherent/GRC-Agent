"""The GrcAgent: tool registry, dispatch, lifecycle, history journal.

Exposes the 3-tool MVP model surface (inspect_graph, change_graph,
query_knowledge) over a local FlowgraphSession backed by the native
grc_native_adapter.
"""

import copy
import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import ChatMessage

from grc_agent.config import AgentConfig, default_app_config
from grc_agent.domain_models import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.history import (
    GraphHistoryJournal,
    GraphSnapshot,
    lineage_key_for_session,
    snapshot_session,
)
from grc_agent.runtime.change_graph import dispatch_flat_change_graph_batch
from grc_agent.runtime.inspect_graph import inspect_graph as inspect_graph_wrapper
from grc_agent.runtime.model_context import (
    GRAPH_MUTATING_TOOL_NAME,
    MVP_MODEL_TOOL_NAMES,
    MVP_TOOL_SURFACE,
    build_system_prompt,
    render_model_messages,
)
from grc_agent.runtime.tool_context import compact_chat_history
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime_tool_validation import (
    build_tool_schema_map,
    validate_runtime_tool_call,
)

logger = logging.getLogger(__name__)

ToolResult = dict[str, Any]
ToolCallable = Callable[..., ToolResult]


class GrcAgent:
    """A thin integration layer between a language model and package-level owners."""
    _MUTATING_TOOLS = {GRAPH_MUTATING_TOOL_NAME}

    def __init__(
        self,
        session: FlowgraphSession | None = None,
        *,
        catalog_root: str | None = None,
        config: AgentConfig | None = None,
        history_journal_path: str | Path | None = None,
        llama_server_url: str | None = None,
        llama_model: str | None = None,
        llama_request_timeout_seconds: float | None = None,
    ) -> None:
        self.session = FlowgraphSession() if session is None else session
        self.catalog_root = str(catalog_root) if catalog_root is not None else None
        self.config = config or default_app_config().agent
        self._retrieval_cfg = self.config.retrieval
        self._guardrails_cfg = self.config.guardrails
        llama_defaults = default_app_config().llama
        self._llama_server_url = (
            llama_server_url
            if isinstance(llama_server_url, str) and llama_server_url.strip()
            else llama_defaults.server_url
        )
        self._llama_model = (
            llama_model
            if isinstance(llama_model, str) and llama_model.strip()
            else llama_defaults.model
        )
        self._embedding_model = llama_defaults.embedding_model
        self._llama_request_timeout_seconds = (
            float(llama_request_timeout_seconds)
            if isinstance(llama_request_timeout_seconds, int | float)
            and not isinstance(llama_request_timeout_seconds, bool)
            and float(llama_request_timeout_seconds) > 0
            else float(llama_defaults.request_timeout_seconds)
        )
        self._history_journal = GraphHistoryJournal(
            history_journal_path,
            accepted_retention=self.config.history.checkpoint_retention,
        )
        self._history_lineage_key: str | None = None
        self.chat_history: ChatHistory = ChatHistory()
        self.chat_session_id = str(uuid.uuid4())
        self._mvp_tools = self._build_mvp_tool_registry()
        self._active_tool_surface = MVP_TOOL_SURFACE
        self._tool_schemas = build_tool_schemas(self._active_tool_surface.model_tool_names)
        self._tool_schema_map = build_tool_schema_map(self._tool_schemas)
        self._turn_user_message = ""
        self._maybe_record_baseline_checkpoint(reason="initial_session")

    def get_system_prompt(self) -> str:
        return build_system_prompt(self.chat_session_id)

    def warmup_vector_index(self) -> None:
        """Kick off background ingestion for both vector indexes (docs + catalog).

        Production entry points (CLI, GUI) call this once after constructing
        the agent so the indexes auto-create on first boot if missing. Both
        stores are idempotent (no-op once populated), so this is safe to call
        every boot. Tests MUST NOT call it: ingestion writes to the real DB
        paths and would be affected by test-time mocks of the embedding
        function.
        """
        import threading

        from grc_agent.retrieval import warmup_catalog_vector_index
        from grc_agent.runtime.doc_answer import DB_PATH, initialize_vector_db_background

        server_url = self._llama_server_url

        def _warm() -> None:
            try:
                initialize_vector_db_background(DB_PATH, server_url)
            except Exception:
                logger.exception("docs vector index warmup failed")
            try:
                warmup_catalog_vector_index(server_url=server_url)
            except Exception:
                logger.exception("catalog vector index warmup failed")

        threading.Thread(target=_warm, daemon=True).start()

    def reset_chat_session(self) -> None:
        """Reset the chat session history and generate a new session ID to clear KV cache matching."""
        self.chat_history.clear()
        self.chat_session_id = str(uuid.uuid4())

    def get_tool_schemas_for_turn(
        self,
        allowed_tool_names: set[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Return model-facing schemas, optionally filtered by an explicit allow-list."""
        if allowed_tool_names is None:
            allowed_order = tuple(self._active_tool_surface.model_tool_names)
        elif isinstance(allowed_tool_names, set):
            allowed_order = tuple(
                name for name in self._active_tool_surface.model_tool_names
                if name in allowed_tool_names
            )
        else:
            allowed_order = tuple(allowed_tool_names)
        schemas_by_name = {schema["function"]["name"]: schema for schema in self._tool_schemas}
        return [schemas_by_name[name] for name in allowed_order if name in schemas_by_name]

    def _reject_outside_surface(
        self,
        tool_name: str,
        allowed: set[str] | tuple[str, ...],
    ) -> ToolResult | None:
        """Canonical surface gate: reject a tool call outside ``allowed``.

        One uniform rule for both execution paths (``execute_tool`` and the
        ToolAgents delegate). Returns ``None`` when the tool is allowed.
        """
        if tool_name in allowed:
            return None
        return self._tool_result(
            tool_name=tool_name,
            ok=False,
            message=(
                f"Tool '{tool_name}' is not available through the model-facing "
                "surface for this graph session."
            ),
            error_type=ErrorCode.TOOL_NOT_ALLOWED_FOR_SURFACE,
            active_tool_surface=self._active_tool_surface.name,
            allowed_tools=sorted(allowed),
        )

    def _surface_tool_gate_result(
        self,
        *,
        tool_name: str,
        model_tool_call: bool,
    ) -> ToolResult | None:
        """Reject disallowed model-driven tools for the active surface profile."""
        if not model_tool_call:
            return None
        return self._reject_outside_surface(
            tool_name, self._active_tool_surface.model_tool_names
        )

    def execute_tool(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        *,
        model_tool_call: bool = False,
    ) -> ToolResult:
        """Execute one runtime tool and return a structured result."""
        kwargs = self.normalize_tool_call_arguments(
            tool_name,
            kwargs,
            model_tool_call=model_tool_call,
        )
        before_snapshot = self._checkpoint_before(tool_name)
        surface_gate = self._surface_tool_gate_result(
            tool_name=tool_name,
            model_tool_call=model_tool_call,
        )
        if surface_gate is not None:
            logger.info(
                "tool_call_rejected tool=%s error_type=%s",
                tool_name,
                surface_gate.get("error_type"),
            )
            self._record_tool_journal(
                tool_name=tool_name,
                result=surface_gate,
                before=before_snapshot,
            )
            return surface_gate
        validation_result = self.validate_tool_call(
            tool_name,
            kwargs,
            model_tool_call=model_tool_call,
        )
        if validation_result is not None:
            logger.info(
                "tool_call_rejected tool=%s error_type=%s",
                tool_name,
                validation_result.get("error_type"),
            )
            self._record_tool_journal(
                tool_name=tool_name,
                result=validation_result,
                before=before_snapshot,
            )
            return validation_result

        func = self._mvp_tools.get(tool_name)
        if func is None:
            result = self._tool_result(
                tool_name=tool_name,
                ok=False,
                message=f"Unknown tool: {tool_name}",
                error_type=ErrorCode.TOOL_CALL_INVALID,
            )
            self._record_tool_journal(
                tool_name=tool_name,
                result=result,
                before=before_snapshot,
            )
            return result
        try:
            result = func(**kwargs)
            logger.info("tool_executed tool=%s ok=%s", tool_name, result.get("ok"))
            self._record_tool_journal(
                tool_name=tool_name,
                result=result,
                before=before_snapshot,
            )
            return result
        except Exception as error:
            logger.exception("tool_exception tool=%s error=%s", tool_name, error)
            result = self._tool_result(
                tool_name=tool_name,
                ok=False,
                message=str(error),
                error_type=ErrorCode.INTERNAL_ERROR,
            )
            self._record_tool_journal(
                tool_name=tool_name,
                result=result,
                before=before_snapshot,
            )
            return result

    def normalize_tool_call_arguments(
        self,
        tool_name: str,
        kwargs: Any,
        *,
        model_tool_call: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(kwargs, dict):
            return {}

        normalized = dict(kwargs)
        if tool_name == GRAPH_MUTATING_TOOL_NAME:
            normalized = copy.deepcopy(normalized)
        return normalized

    def validate_tool_call(
        self,
        tool_name: str,
        kwargs: Any,
        *,
        model_tool_call: bool = False,
    ) -> ToolResult | None:
        """Validate one runtime tool call against the declared public schema.

        Callers (``execute_tool`` and ``ToolAgentsToolDelegate.invoke``)
        normalize kwargs and run the surface gate before calling this,
        so neither is repeated here.
        """
        if (
            model_tool_call
            and tool_name in MVP_MODEL_TOOL_NAMES
            and isinstance(kwargs, dict)
            and "debug" in kwargs
        ):
            return self._tool_result(
                tool_name=tool_name,
                ok=False,
                message="Debug telemetry is not available through the model-facing tool surface.",
                error_type=ErrorCode.INVALID_REQUEST,
            )
        validation_error = validate_runtime_tool_call(
            tool_name, kwargs, self._tool_schema_map
        )
        if validation_error is None:
            return None
        return self._tool_result(tool_name=tool_name, ok=False, **validation_error)

    def validate_turn_route(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        *,
        allowed_tool_names: set[str] | tuple[str, ...] | None = None,
    ) -> ToolResult | None:
        """Reject model tool calls outside the current model-facing surface."""
        del kwargs
        effective_allowed = (
            set(self._active_tool_surface.model_tool_names)
            if allowed_tool_names is None
            else set(allowed_tool_names)
        )
        return self._reject_outside_surface(tool_name, effective_allowed)

    def get_model_messages(
        self,
        *,
        reminder: str | None = None,
        system_salt: str | None = None,
    ) -> list[ChatMessage]:
        return render_model_messages(
            self.chat_history,
            system_prompt=self.get_system_prompt(),
            semantic_search_result_preview=lambda *_, **kw: [],
            reminder=reminder,
            system_salt=system_salt,
        )

    # ------------------------------------------------------------------- #
    # Compact history helpers
    # ------------------------------------------------------------------- #

    def compact_history(self) -> None:
        """Reduce history token cost before a new multi-turn conversation turn.

        The per-payload cap is sourced from
        ``self.config.max_tool_result_chars`` so the compactor never
        starves the model of catalog lookups (a single GNU Radio
        block definition can easily exceed 800 chars; 4000 is the
        default in :class:`AgentConfig`).
        """
        compact_chat_history(
            self.chat_history,
            budget_chars=self.config.history_compact_budget,
            max_tool_result_chars=self.config.max_tool_result_chars,
        )
        logger.debug("compact_history history_len=%d", self.chat_history.get_message_count())

    # ------------------------------------------------------------------- #
    # History content formatting
    # ------------------------------------------------------------------- #

    # ------------------------------------------------------------------- #
    # Tool registry builder
    # ------------------------------------------------------------------- #

    def _build_mvp_tool_registry(self) -> dict[str, ToolCallable]:
        """Return the simplified model-facing MVP tool surface."""
        return {
            "inspect_graph": self._inspect_graph,
            "query_knowledge": self._query_knowledge,
            "web_search": self._web_search,
            "web_fetch": self._web_fetch,
            "change_graph": self._change_graph,
        }

    # ------------------------------------------------------------------- #
    # Session / validation helpers
    # ------------------------------------------------------------------- #

    def _checkpoint_before(self, tool_name: str) -> GraphSnapshot | None:
        if tool_name not in self._MUTATING_TOOLS or self.session.flowgraph is None:
            return None
        try:
            return snapshot_session(self.session)
        except Exception:
            logger.exception("history_checkpoint_capture_failed tool=%s", tool_name)
            return None

    def _ensure_history_lineage_key(self) -> str:
        if self._history_lineage_key is None:
            self._history_lineage_key = lineage_key_for_session(self.session)
        return self._history_lineage_key

    def _maybe_record_baseline_checkpoint(self, *, reason: str) -> None:
        if self.session.flowgraph is None:
            return
        try:
            self._history_journal.record_checkpoint(
                lineage_key=self._ensure_history_lineage_key(),
                session=self.session,
                before=None,
                request_text=self._turn_user_message,
                tool_name=reason,
                operation_type="load",
                validation_result=self.session.validation_state(),
            )
        except Exception:
            logger.exception("history_baseline_checkpoint_failed reason=%s", reason)

    def _record_tool_journal(
        self,
        *,
        tool_name: str,
        result: dict[str, Any],
        before: GraphSnapshot | None,
    ) -> None:
        if tool_name not in self._MUTATING_TOOLS:
            return
        if self.session.flowgraph is None:
            return
        if result.get("ok") is True:
            self._record_accepted_checkpoint(tool_name, result, before)
        elif result.get("ok") is False:
            self._record_failure_journal(tool_name, result, before)

    def _record_accepted_checkpoint(
        self,
        tool_name: str,
        result: dict[str, Any],
        before: GraphSnapshot | None,
    ) -> None:
        try:
            self._history_journal.record_checkpoint(
                lineage_key=self._ensure_history_lineage_key(),
                session=self.session,
                before=before,
                request_text=self._turn_user_message,
                tool_name=tool_name,
                operation_type=tool_name,
                validation_result=self.session.validation_state(),
            )
        except Exception:
            logger.exception("history_accepted_checkpoint_failed tool=%s", tool_name)

    def _record_failure_journal(
        self,
        tool_name: str,
        result: dict[str, Any],
        before: GraphSnapshot | None,
    ) -> None:
        try:
            self._history_journal.record_failure(
                lineage_key=self._ensure_history_lineage_key(),
                session=self.session,
                before=before,
                request_text=self._turn_user_message,
                tool_name=tool_name,
                operation_type=tool_name,
                result=result,
            )
        except Exception:
            logger.exception("history_failure_journal_failed tool=%s", tool_name)

    def _tool_result(
        self,
        tool_name: str,
        ok: bool,
        message: str,
        **extra: Any,
    ) -> ToolResult:
        """Build the common structured result payload returned by every tool."""
        result: ToolResult = {
            "tool": tool_name,
            "ok": ok,
            "message": message,
        }
        result.update(extra)
        if not ok:
            if "error_type" not in result:
                result["error_type"] = ErrorCode.INTERNAL_ERROR
            if "errors" not in result:
                result["errors"] = [{"code": result["error_type"], "message": message}]
        return result

    def _payload_result(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        default_message: str | None = None,
    ) -> ToolResult:
        result = dict(payload)
        if default_message is not None and "message" not in result:
            result["message"] = default_message
        result = self._enforce_tool_output_budget(result)
        return result

    def _enforce_tool_output_budget(self, payload: ToolResult) -> ToolResult:
        """Clamp oversized wrapper payloads to a bounded JSON budget."""
        max_bytes = self._guardrails_cfg.max_tool_output_bytes
        max_list_items = self._guardrails_cfg.max_compact_list_items
        try:
            size = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
        except Exception:
            logger.warning("enforce_tool_output_budget json_serialization_failed, bypassing budget")
            payload["output_truncated"] = True
            return payload
        if size <= max_bytes:
            return payload
        compact = dict(payload)
        for key, value in list(compact.items()):
            if isinstance(value, list) and len(value) > max_list_items:
                original_len = len(value)
                compact[key] = value[:max_list_items]
                compact[f"{key}_truncated"] = (
                    f"... [TRUNCATED: was {original_len} items, kept {max_list_items}]"
                )
                compact["output_truncated"] = True
        compact["output_bytes"] = min(size, max_bytes)
        return compact

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

    def _inspect_graph(
        self,
        view: str = "overview",
        targets: list[str] | None = None,
    ) -> ToolResult:
        return inspect_graph_wrapper(
            self,
            view=view,
            targets=targets or [],
        )

    def _query_knowledge(
        self,
        query: str,
        domain: str,
    ) -> ToolResult:
        from grc_agent.runtime.inspect_graph import query_knowledge as _qk

        return _qk(self, query=query, domain=domain)

    def _change_graph(
        self,
        add_blocks: list[Any] | None = None,
        remove_blocks: list[Any] | None = None,
        update_params: list[Any] | None = None,
        update_states: list[Any] | None = None,
        add_connections: list[Any] | None = None,
        remove_connections: list[Any] | None = None,
        force: bool = False,
    ) -> ToolResult:
        return dispatch_flat_change_graph_batch(
            self,
            add_blocks=add_blocks,
            remove_blocks=remove_blocks,
            update_params=update_params,
            update_states=update_states,
            add_connections=add_connections,
            remove_connections=remove_connections,
            force=bool(force),
        )

    def _web_search(
        self,
        query: str,
        max_results: int = 5,
    ) -> ToolResult:
        from grc_agent.runtime.web_search import web_search as _ws

        raw = _ws(query=query, max_results=max_results)
        if not raw.get("ok"):
            return self._payload_result("web_search", raw, default_message="Web search failed.")

        results = raw.get("results", [])
        if not results:
            return self._payload_result(
                "web_search", raw, default_message="Web search returned no results."
            )

        from grc_agent.runtime.web_answer import summarize_web_search

        try:
            answer = summarize_web_search(self, query=query, results=results)
        except Exception as exc:
            return self._tool_result(
                "web_search",
                ok=False,
                message=f"Web search summarization failed: {exc}",
                error_type=ErrorCode.INTERNAL_ERROR,
            )

        payload = {
            "ok": True,
            "query": query,
            "answer": answer,
            "sources": [{"title": r.get("title", ""), "url": r.get("url", "")} for r in results],
        }
        return self._payload_result("web_search", payload, default_message="Web search complete.")

    def _web_fetch(self, url: str) -> ToolResult:
        from grc_agent.runtime.web_search import web_fetch as _wf

        raw = _wf(url=url)
        if not raw.get("ok"):
            return self._payload_result("web_fetch", raw, default_message="Web fetch failed.")

        title = raw.get("title", "")
        content = raw.get("content", "")
        if not content:
            return self._payload_result(
                "web_fetch",
                {"ok": True, "url": url, "title": title, "links": raw.get("links", [])},
                default_message="Fetched page has no content.",
            )

        from grc_agent.runtime.web_answer import summarize_web_fetch

        try:
            summary = summarize_web_fetch(
                self, url=url, title=title, content=content, context_question=self._turn_user_message
            )
        except Exception as exc:
            return self._tool_result(
                "web_fetch",
                ok=False,
                message=f"Web fetch summarization failed: {exc}",
                error_type=ErrorCode.INTERNAL_ERROR,
            )

        payload = {
            "ok": True,
            "url": url,
            "title": title,
            "summary": summary,
            "links": raw.get("links", []),
        }
        return self._payload_result("web_fetch", payload, default_message="Fetched page.")
