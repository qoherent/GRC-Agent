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
from datetime import datetime
from pathlib import Path
from typing import Any

from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    ToolCallContent,
    ToolCallResultContent,
)

from grc_agent.catalog.loaders import build_catalog_snapshot
from grc_agent.config import AgentConfig, default_app_config
from grc_agent.domain_models import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.history import (
    GraphHistoryJournal,
    GraphSnapshot,
    lineage_key_for_session,
    operation_type_from_result,
    snapshot_session,
)
from grc_agent.runtime.change_graph import dispatch_flat_change_graph_batch
from grc_agent.runtime.clarification import (
    normalize_pending_clarification,
    resolve_pending_clarification_state,
)
from grc_agent.runtime.inspect_graph import inspect_graph as inspect_graph_wrapper
from grc_agent.runtime.model_context import (
    MVP_MODEL_TOOL_NAMES,
    MVP_TOOL_SURFACE,
    build_system_prompt,
    render_model_messages,
)
from grc_agent.runtime.tool_context import (
    compact_chat_history,
    unsafe_graph_root_for_path,
)
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime_tool_validation import (
    build_tool_schema_map,
    validate_runtime_tool_call,
)

logger = logging.getLogger(__name__)

ToolResult = dict[str, Any]
ToolCallable = Callable[..., ToolResult]


_INSTALLED_GRAPH_ROOTS = (
    Path("/usr/share/gnuradio/examples"),
    Path("/usr/local/share/gnuradio/examples"),
)
_CANONICAL_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "data"


def _catalog_version_token(catalog_root: str | None) -> str:
    """Compute a freshness token for the search cache key.

    The expensive catalog parse (``build_catalog_snapshot``) is already
    ``lru_cache``d in ``catalog.loaders``. This function only does cheap
    ``stat()`` calls over the cached file list to detect on-disk changes,
    so it is NOT cached here — caching it would freeze the mtime and
    defeat the invalidation it exists to provide.
    """
    snapshot = build_catalog_snapshot(catalog_root)
    newest_mtime = 0
    for path in [*snapshot.files.block, *snapshot.files.tree, *snapshot.files.domain]:
        try:
            newest_mtime = max(newest_mtime, path.stat().st_mtime_ns)
        except OSError:
            continue
    return f"{snapshot.root}|blocks={len(snapshot.blocks)}|mtime_ns={newest_mtime}"


class GrcAgent:
    """A thin integration layer between a language model and package-level owners."""

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
        self._session_snapshot: dict[str, Any] | None = None
        self.chat_session_id = str(uuid.uuid4())
        self._last_validated_state_revision: int | None = None
        self._last_validation_ok: bool | None = None
        self._reset_validation_tracking()
        self._mvp_tools = self._build_mvp_tool_registry()
        self._active_tool_surface = MVP_TOOL_SURFACE
        self._tool_schemas = build_tool_schemas(self._active_tool_surface.model_tool_names)
        self._tool_schema_map = build_tool_schema_map(self._tool_schemas)
        self._record_active_session_history(reason="initial_session")
        self._turn_user_message = ""
        self._pending_clarification: dict[str, Any] | None = None
        self._pending_clarification_revision: int | None = None
        self._last_docs_advisor_meta: dict[str, Any] = {
            "snippet_count": 0,
            "source_quality": {},
        }
        self._maybe_record_baseline_checkpoint(reason="initial_session")

    def get_system_prompt(self) -> str:
        return build_system_prompt(self.chat_session_id)

    def warmup_vector_index(self) -> None:
        """Kick off background vector-DB ingestion for ask_grc_docs.

        Production entry points (CLI, GUI) call this once after constructing
        the agent. Tests MUST NOT call it: the ingestion thread writes to the
        real DB_PATH and would be affected by test-time mocks of
        ``grc_agent.runtime.doc_answer.get_embedding``.
        """
        import threading

        from grc_agent.runtime.doc_answer import DB_PATH, initialize_vector_db_background

        threading.Thread(
            target=initialize_vector_db_background,
            args=(DB_PATH, self._llama_server_url),
            daemon=True,
        ).start()

    def reset_chat_session(self) -> None:
        """Reset the chat session history and generate a new session ID to clear KV cache matching."""
        self.chat_history.clear()
        self._session_snapshot = None
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

    def _surface_tool_gate_result(
        self,
        *,
        tool_name: str,
        model_tool_call: bool,
    ) -> ToolResult | None:
        """Reject disallowed model-driven tools for the active surface profile."""
        if not model_tool_call:
            return None
        if tool_name in self._active_tool_surface.model_tool_names:
            return None
        return self._tool_result(
            tool_name=tool_name,
            ok=False,
            message=f"Tool '{tool_name}' is not allowed for MVP model-facing execution.",
            error_type=ErrorCode.TOOL_NOT_ALLOWED_FOR_SURFACE,
            active_tool_surface=self._active_tool_surface.name,
            allowed_model_tools=list(self._active_tool_surface.model_tool_names),
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
        if tool_name == "inspect_graph":
            normalized = self._normalize_inspect_graph_args(normalized)
        if tool_name == "change_graph":
            normalized = copy.deepcopy(normalized)
        return normalized

    def _normalize_inspect_graph_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Default inspect_graph view to 'overview'; let targets through."""
        normalized = dict(kwargs)
        normalized.setdefault("view", "overview")
        normalized.setdefault("targets", [])
        return normalized

    def _unsafe_graph_root_for_path(self, path_value: str | Path) -> str | None:
        return unsafe_graph_root_for_path(
            path_value,
            installed_graph_roots=_INSTALLED_GRAPH_ROOTS,
            canonical_fixture_root=_CANONICAL_FIXTURE_ROOT,
        )

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
        validation_kwargs = {
            k: v for k, v in kwargs.items() if k not in {"view", "targets"}
        }
        validation_error = validate_runtime_tool_call(
            tool_name, validation_kwargs, self._tool_schema_map
        )
        if validation_error is None:
            return None
        return self._tool_result(tool_name=tool_name, ok=False, **validation_error)

    def resolve_pending_clarification(
        self,
        user_message: str,
        *,
        model_tool_call: bool = False,
    ) -> dict[str, Any]:
        """Resolve a pending clarification from a human user reply.

        Returns a dict with one of:
            mode="none"              — no pending clarification, proceed normally
            mode="executed"          — option executed, result in "tool_result"
            mode="expired"           — session changed since clarification, cleared
            mode="reminder"          — unrelated text while pending, compact reminder
            mode="custom"            — D / free text, cleared, proceed normally
        """
        if self._pending_clarification is None:
            return {"mode": "none"}

        resolution = resolve_pending_clarification_state(
            pending_clarification=self._pending_clarification,
            pending_revision=self._pending_clarification_revision,
            current_state_revision=self.session.state_revision,
            user_message=user_message,
        )
        mode = resolution.get("mode")
        if mode == "expired":
            self._clear_pending_clarification()
            return {
                "mode": "expired",
                "text": resolution["text"],
            }
        if mode == "selected":
            opt = resolution["option"]
            tool_call_id = self._record_clarification_option_call(resolution["raw_reply"], opt)
            result = self.execute_tool(
                opt.tool_name,
                opt.tool_args,
                model_tool_call=model_tool_call,
            )
            self._record_clarification_option_result(
                tool_call_id,
                opt.tool_name,
                result,
            )
            self._clear_pending_clarification()
            return {"mode": "executed", "tool_result": result}
        if mode == "custom":
            self._clear_pending_clarification()
            return {
                "mode": "custom",
                "text": resolution["text"],
                "custom_hint": resolution["custom_hint"],
            }
        return resolution

    def _record_clarification_option_call(
        self,
        raw_reply: str,
        option: Any,
    ) -> str:
        clarification_id = ""
        if self._pending_clarification is not None:
            clarification_id = str(self._pending_clarification.get("clarification_id") or "pending")
        tool_call_id = f"clarification_{clarification_id}_{option.label}"
        self.chat_history.add_user_message(raw_reply)
        now = datetime.now()
        clarification_message = ChatMessage(
            id=str(uuid.uuid4()),
            role=ChatMessageRole.Assistant,
            content=[
                ToolCallContent(
                    tool_call_id=tool_call_id,
                    tool_call_name=option.tool_name,
                    tool_call_arguments=option.tool_args,
                )
            ],
            created_at=now,
            updated_at=now,
            additional_fields={
                "clarification_selection": {
                    "label": option.label,
                    "clarification_id": clarification_id,
                }
            },
        )
        self.chat_history.add_message(clarification_message)
        return tool_call_id

    def _record_clarification_option_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> None:
        now = datetime.now()
        self.chat_history.add_message(
            ChatMessage(
                id=str(uuid.uuid4()),
                role=ChatMessageRole.Tool,
                content=[
                    ToolCallResultContent(
                        tool_call_result_id=str(uuid.uuid4()),
                        tool_call_id=tool_call_id,
                        tool_call_name=tool_name,
                        tool_call_result=json.dumps(result, sort_keys=True),
                    )
                ],
                created_at=now,
                updated_at=now,
            )
        )

    def _store_pending_clarification(self, payload: dict[str, Any]) -> None:
        """Store a clarification produced by a tool for user resolution."""
        stored, revision = normalize_pending_clarification(
            payload,
            current_state_revision=self.session.state_revision,
        )
        self._pending_clarification = stored
        self._pending_clarification_revision = revision

    def _clear_pending_clarification(self) -> None:
        self._pending_clarification = None
        self._pending_clarification_revision = None

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
        if tool_name in effective_allowed:
            return None
        return self._tool_result(
            tool_name=tool_name,
            ok=False,
            message=(
                f"Tool `{tool_name}` is not available through the model-facing "
                "surface for this graph session."
            ),
            error_type=ErrorCode.TOOL_NOT_ALLOWED_FOR_SURFACE,
            allowed_tools=sorted(effective_allowed),
        )

    def health_check(self) -> dict[str, Any]:
        """Return a structured health payload describing agent readiness."""
        has_session = self.session.flowgraph is not None
        has_retrieval = self.catalog_root is not None
        surface = self._active_tool_surface
        model_tool_count = len(surface.model_tool_names)
        agent_core_ready = model_tool_count > 0
        status = "ok" if agent_core_ready and has_retrieval else "not_ready"
        return {
            "status": status,
            "agent_core_ready": agent_core_ready,
            "session_loaded": has_session,
            "retrieval_ready": has_retrieval,
            "history_length": self.chat_history.get_message_count(),
            "active_tool_surface": surface.name,
            "model_facing_tools": list(surface.model_tool_names),
            "model_tool_count": model_tool_count,
            "tool_count": model_tool_count,
            "assistant_text_fallback_enabled": surface.assistant_text_fallback_enabled,
        }

    def active_session_snapshot(self) -> dict[str, Any] | None:
        """Return the compact active-session payload exposed in runtime history and CLI output."""
        if self.session.flowgraph is None:
            return None
        try:
            return self.session.active_session_snapshot()
        except ValueError:
            return None

    def get_model_messages(self, *, reminder: str | None = None) -> list[ChatMessage]:
        return render_model_messages(
            self.chat_history,
            system_prompt=self.get_system_prompt(),
            semantic_search_result_preview=lambda *_, **kw: [],
            reminder=reminder,
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
            "change_graph": self._change_graph,
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

    def _replace_session(self, session: FlowgraphSession, *, reason: str = "load_grc") -> None:
        self.session = session
        self._reset_validation_tracking()
        self._record_active_session_history(reason=reason)
        self._history_lineage_key = None
        self._maybe_record_baseline_checkpoint(reason=reason)

    def _checkpoint_before(self, tool_name: str) -> GraphSnapshot | None:
        if tool_name != "change_graph" or self.session.flowgraph is None:
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
        if tool_name != "change_graph":
            return
        if self.session.flowgraph is None:
            return
        if result.get("committed") is True:
            self._record_accepted_checkpoint(tool_name, result, before)
        elif not result.get("ok"):
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
                operation_type=operation_type_from_result(tool_name, result),
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
                operation_type=operation_type_from_result(tool_name, result),
                result=result,
            )
        except Exception:
            logger.exception("history_failure_journal_failed tool=%s", tool_name)

    def _tool_result(
        self,
        tool_name: str,
        ok: bool,
        message: str,
        *,
        include_active_session: bool | None = None,
        **extra: Any,
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
        if include_active_session is None:
            include_active_session = tool_name != "change_graph"
        if include_active_session:
            result["active_session"] = self.active_session_snapshot()
        return result

    def _payload_result(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        default_message: str | None = None,
        include_active_session: bool | None = None,
    ) -> ToolResult:
        result = dict(payload)
        if default_message is not None and "message" not in result:
            result["message"] = default_message
        if include_active_session is None:
            include_active_session = tool_name not in MVP_MODEL_TOOL_NAMES
        if include_active_session:
            result["active_session"] = self.active_session_snapshot()
        result = self._enforce_tool_output_budget(result)
        return result

    def _enforce_tool_output_budget(self, payload: ToolResult) -> ToolResult:
        """Clamp oversized wrapper payloads to a bounded JSON budget."""
        max_bytes = self._guardrails_cfg.max_tool_output_bytes
        max_list_items = self._guardrails_cfg.max_compact_list_items
        max_stderr_chars = self._guardrails_cfg.max_validation_stderr_chars
        max_validation_errors = self._guardrails_cfg.max_validation_errors
        try:
            size = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
        except Exception:
            logger.warning("enforce_tool_output_budget json_serialization_failed, bypassing budget")
            payload["output_truncated"] = True
            return payload
        if size <= max_bytes:
            return payload
        compact = dict(payload)
        for key in ("items", "results", "sources"):
            value = compact.get(key)
            if isinstance(value, list) and len(value) > max_list_items:
                original_len = len(value)
                compact[key] = value[:max_list_items]
                compact[f"{key}_truncated"] = (
                    f"... [TRUNCATED: was {original_len} items, kept {max_list_items}]"
                )
                compact["output_truncated"] = True
        validation_errors = compact.get("validation_errors")
        if isinstance(validation_errors, list) and len(validation_errors) > max_validation_errors:
            original_len = len(validation_errors)
            compact["validation_errors"] = validation_errors[:max_validation_errors]
            compact["validation_errors_truncated"] = (
                f"... [TRUNCATED: was {original_len} items, kept {max_validation_errors}]"
            )
            compact["output_truncated"] = True
        validation = compact.get("validation_result")
        if isinstance(validation, dict):
            stderr = validation.get("stderr")
            if isinstance(stderr, str) and len(stderr) > max_stderr_chars:
                validation = dict(validation)
                original_len = len(stderr)
                validation["stderr"] = (
                    stderr[: max_stderr_chars - 1].rstrip()
                    + f"… [TRUNCATED: was {original_len} chars, kept {max_stderr_chars}]"
                )
                compact["validation_result"] = validation
                compact["output_truncated"] = True
        compact["output_bytes"] = min(size, max_bytes)
        return compact

    def _record_active_session_history(self, *, reason: str) -> None:
        snapshot = self.active_session_snapshot()
        if snapshot is None:
            return
        self._session_snapshot = dict(snapshot)
        self._session_snapshot["reason"] = reason

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

    def _search_blocks_version_token(self) -> str:
        catalog_token = _catalog_version_token(self.catalog_root)
        return f"catalog={catalog_token}"

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
