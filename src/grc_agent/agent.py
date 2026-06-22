"""Thin runtime wrapper for routed package-level `.grc` tools."""

import copy
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    TextContent,
    ToolCallContent,
    ToolCallResultContent,
)

from grc_agent.catalog.loaders import build_catalog_snapshot, describe_block
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
from grc_agent.runtime.change_graph import (
    connection_endpoint_candidates as connection_endpoint_candidates_wrapper,
)
from grc_agent.runtime.change_graph import (
    dispatch_flat_change_graph_batch,
    has_endpoint_value,
    resolve_disconnect_connection_id,
)
from grc_agent.runtime.change_graph import (
    loaded_block_by_name as loaded_block_by_name_wrapper,
)
from grc_agent.runtime.change_graph import (
    loaded_block_has_port as loaded_block_has_port_wrapper,
)
from grc_agent.runtime.change_graph import (
    rewire_candidate_passes_preflight as rewire_candidate_passes_preflight_wrapper,
)
from grc_agent.runtime.change_graph import (
    rewire_new_endpoint_candidates as rewire_new_endpoint_candidates_wrapper,
)
from grc_agent.runtime.change_graph import (
    rewire_new_endpoint_is_exact as rewire_new_endpoint_is_exact_wrapper,
)
from grc_agent.runtime.clarification import (
    connection_clarification_payload as connection_clarification_payload_wrapper,
)
from grc_agent.runtime.clarification import (
    duplicate_block_clarification_payload as duplicate_block_clarification_payload_wrapper,
)
from grc_agent.runtime.clarification import (
    normalize_pending_clarification,
    resolve_pending_clarification_state,
)
from grc_agent.runtime.clarification import (
    rewire_clarification_payload as rewire_clarification_payload_wrapper,
)
from grc_agent.runtime.clarification import (
    rewire_new_endpoint_clarification_payload as rewire_new_endpoint_clarification_payload_wrapper,
)
from grc_agent.runtime.doc_answer import (
    ask_grc_docs as ask_grc_docs_wrapper,
)
from grc_agent.runtime.inspect_graph import (
    get_grc_context_internal as get_grc_context_internal_wrapper,
)
from grc_agent.runtime.inspect_graph import inspect_graph as inspect_graph_wrapper
from grc_agent.runtime.integrity import compact_file_integrity
from grc_agent.runtime.model_context import (
    MODEL_TOOL_NAMES_ORDERED,
    MVP_MODEL_TOOL_NAMES,
    MVP_TOOL_SURFACE,
    build_system_prompt,
    render_model_messages,
)
from grc_agent.runtime.search_blocks import (
    search_blocks as search_blocks_wrapper,
)
from grc_agent.runtime.tool_context import (
    compact_chat_history,
    unsafe_graph_root_for_path,
)
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.transaction_normalization import TransactionNormalizer
from grc_agent.runtime_tool_validation import (
    build_tool_schema_map,
    validate_runtime_tool_call,
)
from grc_agent.session import get_grc_context, load_grc, suggest_insertions, summarize_graph
from grc_agent.transaction import apply_edit, propose_edit

logger = logging.getLogger(__name__)

ToolResult = dict[str, Any]
ToolCallable = Callable[..., ToolResult]


def _user_text_of(message: ChatMessage) -> str:
    parts: list[str] = []
    for item in message.content:
        if isinstance(item, TextContent) and isinstance(item.content, str):
            parts.append(item.content)
    return "\n".join(p for p in parts if p)


# Display cap for catalog block summaries shown in search_blocks results.
# Not a param filter — just a prose truncation to keep the discovery view compact.
_SEARCH_BLOCK_SUMMARY_MAX_CHARS = 120
_INSTALLED_GRAPH_ROOTS = (
    Path("/usr/share/gnuradio/examples"),
    Path("/usr/local/share/gnuradio/examples"),
)
_CANONICAL_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "data"
_JOURNALED_MUTATION_TOOLS = {
    "apply_edit",
    "remove_connection",
    "rewire_connection",
    "insert_block_on_connection",
    "auto_insert_block",
    "change_graph",
}


def _normalize_alias_key(value: str) -> str:
    """Canonical alias normalizer: whitespace + lowercase + alphanumeric-only."""
    from grc_agent.runtime.text_utils import compact_whitespace, tokenize_identifier

    return compact_whitespace(" ".join(tokenize_identifier(value)))


def _compact_block_summary(summary: Any) -> str:
    if not isinstance(summary, str):
        return ""
    compact = " ".join(summary.split())
    original_len = len(compact)
    if original_len <= _SEARCH_BLOCK_SUMMARY_MAX_CHARS:
        return compact
    kept_len = _SEARCH_BLOCK_SUMMARY_MAX_CHARS - 1
    return (
        compact[:kept_len].rstrip()
        + f"... [TRUNCATED block-summary: was {original_len} chars, kept {kept_len}]"
    )


def _compact_save_file_integrity(file_integrity: dict[str, Any]) -> dict[str, Any]:
    """Thin wrapper for the unified integrity compactor (legacy name)."""
    return compact_file_integrity(file_integrity)


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
        self._docs_answer_cfg = self.config.docs_answer
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
        self._tools = self._build_tool_registry()
        self._mvp_tools = self._build_mvp_tool_registry()
        self._active_tool_surface = MVP_TOOL_SURFACE
        self._tool_schemas = build_tool_schemas(self._active_tool_surface.model_tool_names)
        self._all_tool_schemas = build_tool_schemas(MODEL_TOOL_NAMES_ORDERED)
        self._tool_schema_map = build_tool_schema_map(self._all_tool_schemas)
        self._record_active_session_history(reason="initial_session")
        self._turn_user_message = ""
        self._transaction_normalizer = TransactionNormalizer(session=self.session)
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

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return model-facing tool schemas for the active ToolSurface."""
        return self._tool_schemas

    def get_all_tool_schemas(self) -> list[dict[str, Any]]:
        """Return the full internal schema catalog used for validation."""
        return self._all_tool_schemas

    def get_tool_schemas_for_turn(
        self,
        allowed_tool_names: set[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Return model-facing schemas, optionally filtered by an explicit allow-list."""
        if allowed_tool_names is None:
            allowed_order = tuple(self._active_tool_surface.model_tool_names)
        elif isinstance(allowed_tool_names, set):
            allowed_order = tuple(
                name for name in MODEL_TOOL_NAMES_ORDERED if name in allowed_tool_names
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

        func = self._tools.get(tool_name) or self._mvp_tools.get(tool_name)
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

        normalized: dict[str, Any] = {}
        for raw_key, raw_value in kwargs.items():
            key, inline_value = self._transaction_normalizer.normalize_tool_argument_key(raw_key)
            value = inline_value if inline_value is not None else raw_value
            if key == "transaction":
                value = self._transaction_normalizer.normalize_transaction_instance_names(value)
            normalized[key] = value

        if tool_name == "inspect_graph":
            normalized = self._normalize_inspect_graph_args(normalized)
        if tool_name == "change_graph":
            normalized = copy.deepcopy(normalized)
        return normalized

    def _normalize_inspect_graph_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Fill safe read-only inspect defaults without hiding unsupported syntax."""
        normalized = dict(kwargs)
        view = normalized.get("view")
        normalized.setdefault("targets", [])
        normalized.setdefault("params", [])
        if "view" not in normalized:
            targets = normalized.get("targets")
            normalized["view"] = (
                "details" if isinstance(targets, list) and len(targets) > 0 else "overview"
            )
            view = normalized["view"]
        if isinstance(view, str) and view.strip().lower() == "overview":
            normalized["targets"] = []
        elif isinstance(view, str) and view.strip().lower() == "details":
            normalized = self._normalize_inspect_parameter_targets(normalized)
            params = normalized.get("params")
            if params is None or params == []:
                normalized["params"] = []
        return normalized

    def _normalize_inspect_parameter_targets(
        self,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Split exact `block.parameter` inspect refs into target and param filter."""
        targets = kwargs.get("targets")
        if not isinstance(targets, list):
            return kwargs
        params = kwargs.get("params")
        normalized_params = [str(item) for item in params] if isinstance(params, list) else []
        if any(str(item).strip().lower() == "all" for item in normalized_params):
            return kwargs

        param_keys_by_block = self._inspect_param_keys_by_block()
        if not param_keys_by_block:
            return kwargs

        changed = False
        normalized_targets: list[Any] = []
        seen_targets: set[str] = set()
        seen_params = {item.strip() for item in normalized_params if item.strip()}
        for target in targets:
            if not isinstance(target, str):
                normalized_targets.append(target)
                continue
            split_ref = self._split_inspect_parameter_ref(target, param_keys_by_block)
            if split_ref is None:
                target_key = target.strip()
                if target_key and target_key not in seen_targets:
                    normalized_targets.append(target)
                    seen_targets.add(target_key)
                continue
            block_name, param_key = split_ref
            changed = True
            if block_name not in seen_targets:
                normalized_targets.append(block_name)
                seen_targets.add(block_name)
            if param_key not in seen_params:
                normalized_params.append(param_key)
                seen_params.add(param_key)

        if not changed:
            return kwargs
        normalized = dict(kwargs)
        normalized["targets"] = normalized_targets
        normalized["params"] = normalized_params
        return normalized

    def _inspect_param_keys_by_block(self) -> dict[str, set[str]]:
        from grc_agent.runtime.inspect_graph import _param_keys_by_block

        flowgraph = self.session.flowgraph
        if flowgraph is None:
            return {}
        return {name: set(keys) for name, keys in _param_keys_by_block(flowgraph.blocks).items()}

    @staticmethod
    def _split_inspect_parameter_ref(
        value: str,
        param_keys_by_block: dict[str, set[str]],
    ) -> tuple[str, str] | None:
        text = value.strip()
        if "." not in text:
            return None
        for block_name in sorted(param_keys_by_block, key=len, reverse=True):
            prefix = f"{block_name}."
            if not text.startswith(prefix):
                continue
            param_key = text.removeprefix(prefix)
            if param_key in param_keys_by_block[block_name]:
                return block_name, param_key
        return None

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
        if not isinstance(kwargs, dict):
            kwargs = {}
        validation_kwargs = {k: v for k, v in kwargs.items() if k != "view"}
        validation_error = validate_runtime_tool_call(
            tool_name, validation_kwargs, self._tool_schema_map
        )
        if validation_error is None:
            return None
        return self._tool_result(tool_name=tool_name, ok=False, **validation_error)

    def should_stop_batch_after_result(self, tool_name: str, result: dict[str, Any]) -> bool:
        if not isinstance(result, dict) or result.get("ok") is not False:
            return False
        return tool_name in {
            "new_grc",
            "load_grc",
            "apply_edit",
            "remove_connection",
            "validate_graph",
            "save_graph",
        }

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
        internal_tool_count = len(self._tools)
        model_tool_count = len(surface.model_tool_names)
        agent_core_ready = internal_tool_count > 0 and model_tool_count > 0
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
            "internal_tool_count": internal_tool_count,
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
    # Tool registry builders
    # ------------------------------------------------------------------- #

    def _build_tool_registry(self) -> dict[str, ToolCallable]:
        return {
            "new_grc": self._new_grc,
            "load_grc": self._load_grc,
            "summarize_graph": self._summarize_graph,
            "get_grc_context": self._get_grc_context,
            "describe_block": self._describe_block,
            "suggest_compatible_insertions": self._suggest_compatible_insertions,
            "insert_block_on_connection": self._insert_block_on_connection,
            "auto_insert_block": self._auto_insert_block,
            "remove_connection": self._remove_connection,
            "rewire_connection": self._rewire_connection,
            "apply_edit": self._apply_edit,
            "propose_edit": self._propose_edit,
            "validate_graph": self._validate_graph,
            "save_graph": self._save_graph,
            "search_blocks": self._search_blocks,
            "ask_grc_docs": self._ask_grc_docs,
        }

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
        self._transaction_normalizer = TransactionNormalizer(session=session)
        self._reset_validation_tracking()
        self._record_active_session_history(reason=reason)
        self._history_lineage_key = None
        self._maybe_record_baseline_checkpoint(reason=reason)

    def _checkpoint_before(self, tool_name: str) -> GraphSnapshot | None:
        if tool_name not in _JOURNALED_MUTATION_TOOLS and tool_name != "save_graph":
            return None
        if self.session.flowgraph is None:
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
        if self.session.flowgraph is None:
            return
        if tool_name == "propose_edit":
            return
        if tool_name == "change_graph":
            if result.get("committed") is True:
                self._record_accepted_checkpoint(tool_name, result, before)
            elif not result.get("ok"):
                self._record_failure_journal(tool_name, result, before)
            return
        if tool_name == "save_graph":
            if result.get("ok"):
                self._record_accepted_checkpoint(tool_name, result, before)
            return
        if tool_name not in _JOURNALED_MUTATION_TOOLS:
            return
        if result.get("ok"):
            self._record_accepted_checkpoint(tool_name, result, before)
            return
        self._record_failure_journal(tool_name, result, before)

    def _record_accepted_checkpoint(
        self,
        tool_name: str,
        result: dict[str, Any],
        before: GraphSnapshot | None,
    ) -> None:
        try:
            checkpoint = self._history_journal.record_checkpoint(
                lineage_key=self._ensure_history_lineage_key(),
                session=self.session,
                before=before,
                request_text=self._turn_user_message,
                tool_name=tool_name,
                operation_type=operation_type_from_result(tool_name, result),
                validation_result=(
                    result.get("validation")
                    if isinstance(result.get("validation"), dict)
                    else self.session.validation_state()
                ),
                save_path=(result.get("path") if tool_name == "save_graph" else None),
            )
            checkpoint_id = checkpoint.get("id")
            if isinstance(checkpoint_id, str) and checkpoint_id:
                # Store in telemetry (internal), NOT in the model-visible result.
                telemetry = result.get("dispatch_telemetry")
                if isinstance(telemetry, dict):
                    telemetry["checkpoint_id"] = checkpoint_id
                    telemetry["checkpoint_created"] = True
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
        if tool_name == "change_graph":
            result.setdefault("state_revision", self.session.state_revision)
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
        # Drop active_session for MVP model-facing tools — it is noise
        # the model does not act on. Keep the ``tool`` key for self-describing
        # chat-history records and audit/eval consumers.
        _MVP_TOOLS = {
            "inspect_graph",
            "change_graph",
            "query_knowledge",
            "search_blocks",
            "ask_grc_docs",
            "get_grc_context",
            "describe_block",
        }
        result["tool"] = tool_name
        if default_message is not None and "message" not in result:
            result["message"] = default_message
        if include_active_session is None:
            include_active_session = tool_name not in _MVP_TOOLS
        if tool_name == "change_graph":
            result.setdefault("state_revision", self.session.state_revision)
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
        params: list[str] | None = None,
        debug: bool = False,
    ) -> ToolResult:
        return inspect_graph_wrapper(
            self,
            view=view,
            targets=targets or [],
            params=params or [],
            debug=debug,
        )

    def _search_blocks_version_token(self) -> str:
        catalog_token = _catalog_version_token(self.catalog_root)
        return f"catalog={catalog_token}"

    def _query_knowledge(
        self,
        query: str,
        domain: str,
        debug: bool = False,
    ) -> ToolResult:
        from grc_agent.runtime.inspect_graph import query_knowledge as _qk

        return _qk(self, query=query, domain=domain, debug=debug)

    def _search_blocks(
        self,
        query: str,
        k: int | None = None,
        debug: bool = False,
    ) -> ToolResult:
        return search_blocks_wrapper(
            self,
            query=query,
            k=k,
            debug=debug,
        )

    def _ask_grc_docs(
        self,
        question: str,
        k: int | None = None,
        focus: str | None = None,
        debug: bool = False,
    ) -> ToolResult:
        return ask_grc_docs_wrapper(
            self,
            question=question,
            k=k,
            focus=focus,
            debug=debug,
        )

    def _change_graph(
        self,
        add_blocks: list[Any] | None = None,
        remove_blocks: list[Any] | None = None,
        update_params: list[Any] | None = None,
        update_states: list[Any] | None = None,
        add_connections: list[Any] | None = None,
        remove_connections: list[Any] | None = None,
        force: bool = False,
        debug: bool = False,
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
            debug=debug,
        )

    def _attach_wrapper_dispatch_telemetry(
        self,
        *,
        debug: bool,
        wrapper_name: str,
        wrapper_action: str,
        internal_handlers: list[str],
        started: float,
        before_revision: int,
        before_dirty: bool,
        result: dict[str, Any],
        validation_run: bool,
        output_truncated: bool,
    ) -> dict[str, Any]:
        """Attach structured dispatch telemetry in debug/eval mode only."""
        if not debug:
            return result
        elapsed_ms = int((time.monotonic() - started) * 1000)
        graph_mutated = (
            self.session.state_revision != before_revision or self.session.is_dirty != before_dirty
        )
        clarification_returned = (
            bool(result.get("clarification_options"))
            or bool(result.get("clarification_required"))
            or result.get("error_type")
            in {
                "clarification_required",
                "ambiguous_connection",
                "ambiguous_rewire_old_connection",
                "ambiguous_rewire_new_endpoint",
                "ambiguous_target",
            }
        )
        # Check if checkpoint was created (stored in dispatch_telemetry by the
        # history journal, not in the model-visible result dict).
        dispatch_telem = result.get("dispatch_telemetry")
        if isinstance(dispatch_telem, dict):
            checkpoint_created = bool(dispatch_telem.get("checkpoint_id"))
        else:
            checkpoint_created = False
        telemetry = {
            "wrapper_name": wrapper_name,
            "wrapper_action": wrapper_action,
            "internal_handler_called": list(dict.fromkeys(internal_handlers)) or ["none"],
            "graph_mutated": graph_mutated,
            "validation_run": bool(validation_run),
            "checkpoint_created": checkpoint_created,
            "clarification_returned": clarification_returned,
            "output_truncated": bool(output_truncated),
            "elapsed_ms": elapsed_ms,
        }
        result["dispatch_telemetry"] = telemetry
        logger.info("wrapper_dispatch_telemetry %s", json.dumps(telemetry, sort_keys=True))
        return result

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
        self._replace_session(new_session, reason="new_grc")
        result = self._tool_result(
            "new_grc",
            ok=True,
            message="Empty flowgraph session created.",
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
        is_valid = loaded.validate()
        if not is_valid:
            return self._tool_result(
                "load_grc",
                ok=False,
                message="Refusing to activate loaded graph because validation failed.",
                error_type=ErrorCode.GNU_VALIDATION_FAILED,
                validation=loaded.validation_state(),
            )
        self._replace_session(loaded, reason="load_grc")
        payload = summarize_graph(self.session)
        result = self._payload_result("load_grc", payload, default_message="Graph loaded.")
        result["validation"] = self.session.validation_state()
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

    def _get_grc_context(
        self,
        node_id: str,
        hops: int = 1,
        max_nodes: int | None = None,
    ) -> ToolResult:
        payload = get_grc_context_internal_wrapper(
            node_id,
            hops=hops,
            max_nodes=max_nodes,
            session=self.session,
            catalog_root=self.catalog_root,
            default_max_nodes=self._guardrails_cfg.max_context_nodes,
            symbol_resolver=self._resolve_symbol_like_name,
            context_fn=get_grc_context,
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
            payload["source"] = (
                {
                    "block": result.source.block,
                    "port": result.source.port,
                    "dtype": result.source.dtype,
                    "vlen": result.source.vlen,
                    "domain": result.source.domain,
                }
                if result.source
                else None
            )
            payload["destination"] = (
                {
                    "block": result.destination.block,
                    "port": result.destination.port,
                    "dtype": result.destination.dtype,
                    "vlen": result.destination.vlen,
                    "domain": result.destination.domain,
                }
                if result.destination
                else None
            )
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
        """Bounded agentic insert workflow: search, score, try, commit or clarify."""
        from grc_agent.session import auto_insert_block

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
        if payload.get("clarification_required"):
            # Store for human resolution; no live mutation happened.
            self._store_pending_clarification(payload)
            return self._payload_result("auto_insert_block", payload)
        if payload.get("ok"):
            self._record_successful_validation()
            self._clear_pending_clarification()
        return self._payload_result("auto_insert_block", payload)

    def _remove_connection(
        self,
        connection_id: str | None = None,
        src_block: str | None = None,
        src_port: int | str | None = None,
        dst_block: str | None = None,
        dst_port: int | str | None = None,
    ) -> ToolResult:
        """Resolve connection arguments to one connection_id, then delegate."""
        missing_session = self._missing_session_result("remove_connection")
        if missing_session is not None:
            return missing_session

        resolution = resolve_disconnect_connection_id(
            session=self.session,
            connection_id=connection_id,
            src_block=src_block,
            src_port=src_port,
            dst_block=dst_block,
            dst_port=dst_port,
        )
        if resolution.ambiguous_candidates is not None:
            payload = self._connection_clarification_payload(resolution.ambiguous_candidates)
            self._store_pending_clarification(payload)
            return self._payload_result("remove_connection", payload)
        if not resolution.ok:
            extra: dict[str, Any] = {}
            if resolution.error_type is not None:
                extra["error_type"] = resolution.error_type
            if resolution.state_revision is not None:
                extra["state_revision"] = resolution.state_revision
            if resolution.validation_errors is not None:
                extra["validation_errors"] = resolution.validation_errors
            return self._tool_result(
                tool_name="remove_connection",
                ok=False,
                message=resolution.message or "Could not resolve connection.",
                **extra,
            )
        return self._remove_connection_by_id(resolution.connection_id or "")

    def _remove_connection_by_id(self, connection_id: str) -> ToolResult:
        """Thin wrapper: delegates to apply_edit with op_type=remove_connection."""
        result = self._apply_edit(
            {
                "op_type": "remove_connection",
                "connection_id": connection_id,
            }
        )
        result["tool"] = "remove_connection"
        return result

    def _connection_clarification_payload(
        self,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return connection_clarification_payload_wrapper(self, candidates)

    def _rewire_connection(
        self,
        old_connection_id: str | None = None,
        old_src_block: str | None = None,
        old_src_port: int | str | None = None,
        old_dst_block: str | None = None,
        old_dst_port: int | str | None = None,
        new_src_block: str | None = None,
        new_src_port: int | str | None = None,
        new_dst_block: str | None = None,
        new_dst_port: int | str | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        """Resolve the old edge, then run one atomic remove+add transaction."""
        missing_session = self._missing_session_result("rewire_connection")
        if missing_session is not None:
            return missing_session

        old_resolution = self._resolve_old_rewire_connection_id(
            old_connection_id=old_connection_id,
            old_src_block=old_src_block,
            old_src_port=old_src_port,
            old_dst_block=old_dst_block,
            old_dst_port=old_dst_port,
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        )
        if old_resolution.get("clarification_required"):
            self._store_pending_clarification(old_resolution)
            return self._payload_result("rewire_connection", old_resolution)
        if not old_resolution.get("ok"):
            return self._payload_result("rewire_connection", old_resolution)

        resolved_old_connection_id = old_resolution["old_connection_id"]
        new_resolution = self._resolve_rewire_new_endpoint_args(
            old_connection_id=resolved_old_connection_id,
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        )
        if new_resolution.get("clarification_required"):
            self._store_pending_clarification(new_resolution)
            return self._payload_result("rewire_connection", new_resolution)
        if not new_resolution.get("ok"):
            return self._payload_result("rewire_connection", new_resolution)

        tx_tool = self._propose_edit if dry_run else self._apply_edit
        result = tx_tool(
            [
                {
                    "op_type": "remove_connection",
                    "src_block": old_resolution["src_block"],
                    "src_port": old_resolution["src_port"],
                    "dst_block": old_resolution["dst_block"],
                    "dst_port": old_resolution["dst_port"],
                },
                {
                    "op_type": "add_connection",
                    "src_block": new_resolution["new_src_block"],
                    "src_port": new_resolution["new_src_port"],
                    "dst_block": new_resolution["new_dst_block"],
                    "dst_port": new_resolution["new_dst_port"],
                },
            ]
        )
        result["tool"] = "rewire_connection"
        return result

    @staticmethod
    def _has_endpoint_value(value: Any) -> bool:
        return has_endpoint_value(value)

    def _rewire_new_endpoint_is_exact(
        self,
        *,
        new_src_block: str | None,
        new_src_port: int | str | None,
        new_dst_block: str | None,
        new_dst_port: int | str | None,
    ) -> bool:
        return rewire_new_endpoint_is_exact_wrapper(
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        )

    def _resolve_rewire_new_endpoint_args(
        self,
        *,
        old_connection_id: str,
        new_src_block: str | None,
        new_src_port: int | str | None,
        new_dst_block: str | None,
        new_dst_port: int | str | None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "clarification_required": False,
            "new_src_block": new_src_block or "",
            "new_src_port": str(new_src_port) if new_src_port is not None else "",
            "new_dst_block": new_dst_block or "",
            "new_dst_port": str(new_dst_port) if new_dst_port is not None else "",
        }

    def _rewire_new_endpoint_candidates(
        self,
        *,
        old_connection_id: str,
        new_src_block: str | None,
        new_src_port: int | str | None,
        new_dst_block: str | None,
        new_dst_port: int | str | None,
    ) -> list[dict[str, Any]]:
        return rewire_new_endpoint_candidates_wrapper(
            self,
            old_connection_id=old_connection_id,
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        )

    def _connection_endpoint_candidates(
        self,
        *,
        side: str,
        block: str | None,
        port: int | str | None,
    ) -> list[tuple[str, int | str]]:
        return connection_endpoint_candidates_wrapper(
            self,
            side=side,
            block=block,
            port=port,
        )

    def _loaded_block_by_name(self, instance_name: str) -> Any | None:
        return loaded_block_by_name_wrapper(self, instance_name)

    def _loaded_block_has_port(
        self,
        *,
        block_type: str,
        port: int | str,
        side: str,
    ) -> bool:
        return loaded_block_has_port_wrapper(
            block_type=block_type,
            port=port,
            side=side,
        )

    def _rewire_candidate_passes_preflight(
        self,
        old_connection_id: str,
        candidate: dict[str, Any],
    ) -> bool:
        return rewire_candidate_passes_preflight_wrapper(
            self,
            old_connection_id,
            candidate,
        )

    def _rewire_new_endpoint_clarification_payload(
        self,
        *,
        old_connection_id: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return rewire_new_endpoint_clarification_payload_wrapper(
            self,
            old_connection_id=old_connection_id,
            candidates=candidates,
        )

    def _resolve_old_rewire_connection_id(
        self,
        *,
        old_connection_id: str | None,
        old_src_block: str | None,
        old_src_port: int | str | None,
        old_dst_block: str | None,
        old_dst_port: int | str | None,
        new_src_block: str | None,
        new_src_port: int | str | None,
        new_dst_block: str | None,
        new_dst_port: int | str | None,
    ) -> dict[str, Any]:
        from grc_agent.runtime.connection_ids import parse_connection_id

        if not old_connection_id:
            return {
                "ok": False,
                "error_type": "missing_old_connection_id",
                "message": "old_connection_id is required",
            }
        parsed = parse_connection_id(old_connection_id)
        if parsed is None:
            return {
                "ok": False,
                "error_type": "malformed_old_connection_id",
                "message": f"Cannot parse connection id: {old_connection_id}",
            }
        return {
            "ok": True,
            "clarification_required": False,
            "old_connection_id": old_connection_id,
            **parsed,
        }

    def _rewire_clarification_payload(
        self,
        candidates: list[dict[str, Any]],
        *,
        new_src_block: str,
        new_src_port: int | str | None,
        new_dst_block: str,
        new_dst_port: int | str | None,
    ) -> dict[str, Any]:
        return rewire_clarification_payload_wrapper(
            self,
            candidates,
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        )

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
            result["hint"] = "Preview only — graph was not modified."
        return result

    def _autosave_after_validated_mutation(self, *, allow_invalid: bool = False) -> dict[str, Any]:
        """Persist a successfully validated committed mutation when possible."""
        if self.session.path is None:
            return {
                "ok": False,
                "skipped": True,
                "error_type": "SAVE_PATH_REQUIRED",
                "message": "Autosave skipped because this graph has no file path.",
            }
        if not self.session.is_dirty:
            return {
                "ok": True,
                "skipped": True,
                "path": str(self.session.path),
                "dirty": False,
                "message": "Autosave skipped because graph is unchanged.",
            }
        save_result = self._save_graph(allow_invalid=allow_invalid)
        return {
            "ok": save_result.get("ok") is True,
            "skipped": False,
            "path": save_result.get("path"),
            "dirty": save_result.get("dirty"),
            "error_type": save_result.get("error_type"),
            "message": save_result.get("message", "Autosave failed."),
        }

    def _duplicate_block_clarification_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        return duplicate_block_clarification_payload_wrapper(self, payload)

    def _apply_edit(self, transaction: Any, *, force_validation: bool = False) -> ToolResult:
        missing_session = self._missing_session_result("apply_edit")
        if missing_session is not None:
            return missing_session
        payload = apply_edit(
            self.session,
            self._transaction_normalizer.normalize_transaction_instance_names(transaction),
            self.catalog_root,
            force_validation=force_validation,
        )
        if payload.get("ok") and not payload.get("forced_validation_failure"):
            self._record_successful_validation()
        elif clarification := self._duplicate_block_clarification_payload(payload):
            self._store_pending_clarification(clarification)
            return self._payload_result("apply_edit", clarification)
        result = self._payload_result("apply_edit", payload)
        if result.get("ok"):
            autosave = self._autosave_after_validated_mutation(
                allow_invalid=bool(result.get("forced_validation_failure"))
            )
            result["autosave"] = autosave
            if autosave.get("ok"):
                if autosave.get("skipped") and not result.get("dirty"):
                    msg = "already set to target values; graph unchanged."
                    hint = "The requested changes are already applied. Graph unchanged."
                    if (
                        isinstance(result.get("normalized_operations"), list)
                        and len(result["normalized_operations"]) == 1
                    ):
                        op = result["normalized_operations"][0]
                        if op.get("op_type") == "update_states":
                            state = op.get("state")
                            msg = f"already {state}; graph unchanged."
                            hint = f"Block already {state}. Graph unchanged."
                        elif op.get("op_type") == "update_params":
                            params = op.get("params")
                            if isinstance(params, dict) and len(params) == 1:
                                val = list(params.values())[0]
                                msg = f"already {val}; graph unchanged."
                                hint = f"Parameter already set to {val}. Graph unchanged."
                    result["message"] = msg
                    result["hint"] = hint
                elif result.get("forced_validation_failure"):
                    result["hint"] = "Edit applied and autosaved — validation failed."
                else:
                    result["hint"] = "Edit applied, validated, and autosaved."
            else:
                result["hint"] = (
                    "Edit applied and validated, but autosave did not write the graph: "
                    f"{autosave.get('message', 'autosave failed')}"
                )
        else:
            result["hint"] = None
        return result

    def _validate_graph(self) -> ToolResult:
        missing_session = self._missing_session_result("validate_graph")
        if missing_session is not None:
            return missing_session
        is_valid = self.session.validate()
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
            result["hint"] = "Validation passed."
        return result

    def _save_graph(
        self,
        path: str | None = None,
        overwrite: bool = False,
        *,
        allow_invalid: bool = False,
    ) -> ToolResult:
        missing_session = self._missing_session_result("save_graph")
        if missing_session is not None:
            return missing_session
        if path is None and self.session.path is None:
            return self._tool_result(
                tool_name="save_graph",
                ok=False,
                message="This new graph has no file path yet.",
                error_type="SAVE_PATH_REQUIRED",
            )
        explicit_path = isinstance(path, str) and bool(path.strip())
        target_path = Path(path).expanduser() if explicit_path else self.session.path
        if target_path is not None:
            resolved_target = target_path.resolve(strict=False)
            unsafe_root = self._unsafe_graph_root_for_path(resolved_target)
            if unsafe_root is not None:
                return self._tool_result(
                    tool_name="save_graph",
                    ok=False,
                    message=f"Refusing to write to protected canonical/example graph paths under {unsafe_root}.",
                    error_type=ErrorCode.SAVE_REFUSED,
                )
            current_path = (
                self.session.path.resolve(strict=False) if self.session.path is not None else None
            )
            if (
                explicit_path
                and resolved_target.exists()
                and current_path is not None
                and resolved_target != current_path
                and not overwrite
            ):
                return self._tool_result(
                    tool_name="save_graph",
                    ok=False,
                    message=(
                        "Refusing to overwrite existing destination without explicit "
                        "overwrite permission."
                    ),
                    error_type=ErrorCode.SAVE_REFUSED,
                    path=str(resolved_target),
                )
            if current_path is not None and resolved_target == current_path:
                file_integrity = self.session.file_integrity_state()
                if file_integrity.get("externally_modified"):
                    return self._tool_result(
                        tool_name="save_graph",
                        ok=False,
                        message=(
                            "Refusing to save because the active graph file changed "
                            "on disk after this session loaded or saved it."
                        ),
                        error_type=ErrorCode.STALE_REVISION,
                        path=str(resolved_target),
                        file_integrity=_compact_save_file_integrity(file_integrity),
                    )
        if not allow_invalid and (
            not self._last_validation_ok
            or self._last_validated_state_revision != self.session.state_revision
        ):
            validation = self._validate_graph()
            if validation.get("ok") is not True or not bool(validation.get("valid")):
                return self._tool_result(
                    tool_name="save_graph",
                    ok=False,
                    message="Refusing to save before successful validation.",
                    error_type=ErrorCode.SAVE_REFUSED,
                    requires_validation=True,
                    dirty=self.session.is_dirty,
                    validation=validation,
                )

        try:
            self.session.save(path, validate=not allow_invalid)
        except Exception as exc:
            return self._tool_result(
                tool_name="save_graph",
                ok=False,
                message=f"Failed to save graph: {exc}",
                error_type=ErrorCode.INTERNAL_ERROR,
            )
        self._reset_validation_tracking()
        if allow_invalid:
            self._last_validation_ok = False
            self._last_validated_state_revision = None
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
