"""Thin runtime wrapper for routed package-level `.grc` tools."""

import copy
import hashlib
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
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

from grc_agent._payload import ErrorCode
from grc_agent.catalog.loaders import build_catalog_snapshot, describe_block
from grc_agent.config import AgentConfig, default_app_config
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
    resolve_old_rewire_connection_id as resolve_old_rewire_connection_id_wrapper,
)
from grc_agent.runtime.change_graph import (
    resolve_rewire_new_endpoint_args as resolve_rewire_new_endpoint_args_wrapper,
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
    DocsAnswerSnippet,
    _DocsComparisonSides,
    _DocsEvidenceCandidate,
    build_catalog_assisted_candidate,
    build_fallback_answer,
    catalog_block_purpose_sentence,
    classify_docs_answer_type,
    clean_catalog_summary_for_answer,
    clean_docs_excerpt,
    clip_docs_snippets_for_helper,
    docs_low_value_reasons,
    docs_primary_terms,
    docs_topic_terms,
    extract_block_definition_subject,
    extract_comparison_sides,
    extract_docs_subject,
    helper_candidates_for_docs_answer,
    helper_eligibility_for_docs_answer,
    is_block_definition_query,
    is_procedural_walkthrough_text,
    is_tutorial_or_howto_query,
    minimum_required_term_hits,
    pick_typed_sentence,
    required_terms_for_answer_type,
    select_docs_candidates_for_answer_type,
    sentence_list,
    should_catalog_assist,
    text_matches_term_or_synonym,
)
from grc_agent.runtime.doc_answer import (
    ask_grc_docs as ask_grc_docs_wrapper,
)
from grc_agent.runtime.doc_answer import (
    build_docs_source_quality as build_docs_source_quality_wrapper,
)
from grc_agent.runtime.doc_answer import (
    build_typed_docs_answer as build_typed_docs_answer_wrapper,
)
from grc_agent.runtime.doc_answer import (
    collect_docs_candidates as collect_docs_candidates_wrapper,
)
from grc_agent.runtime.doc_answer import (
    rank_docs_candidates as rank_docs_candidates_wrapper,
)
from grc_agent.runtime.doc_answer import (
    run_docs_answer_advisor as run_docs_answer_advisor_wrapper,
)
from grc_agent.runtime.inspect_graph import (
    get_grc_context_internal as get_grc_context_internal_wrapper,
)
from grc_agent.runtime.inspect_graph import inspect_graph as inspect_graph_wrapper
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
    is_meaningful,
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


_SAVE_PATH_HINT_PATTERN = re.compile(r"(?P<path>(?:~|/)[^\s'\"`]+\.grc)\b")
_ALIAS_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
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
    normalized = " ".join(value.split()).strip().lower()
    if not normalized:
        return ""
    normalized = _ALIAS_TOKEN_PATTERN.sub(" ", normalized).strip()
    return normalized


def _compact_block_summary(summary: Any) -> str:
    if not isinstance(summary, str):
        return ""
    compact = " ".join(summary.split())
    if len(compact) <= _SEARCH_BLOCK_SUMMARY_MAX_CHARS:
        return compact
    return compact[: _SEARCH_BLOCK_SUMMARY_MAX_CHARS - 1].rstrip() + "…"


def _compact_save_file_integrity(file_integrity: dict[str, Any]) -> dict[str, Any]:
    def _short_hash(value: Any) -> str | None:
        return value[:12] if isinstance(value, str) and value else None

    compact: dict[str, Any] = {
        "status": file_integrity.get("status"),
        "path": file_integrity.get("path"),
        "persisted_sha256": _short_hash(file_integrity.get("persisted_sha256")),
        "current_sha256": _short_hash(file_integrity.get("current_sha256")),
    }
    error = file_integrity.get("error")
    if isinstance(error, str) and error:
        compact["error"] = error
    return {key: value for key, value in compact.items() if is_meaningful(value)}





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
    _INTERNAL_TOOL_NAME_REQUEST_VERBS: tuple[str, ...] = (
        "call",
        "use",
        "invoke",
        "run",
        "execute",
    )
    _INTERNAL_TOOL_NAMES_BLOCKED_IN_MVP: tuple[str, ...] = (
        "apply_edit",
        "remove_connection",
        "rewire_connection",
        "save_graph",
        "validate_graph",
        "propose_edit",
        "insert_block_on_connection",
        "auto_insert_block",
    )
    _UNSUPPORTED_OPERATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("undo", ("undo",)),
        ("redo", ("redo",)),
        ("Python export", ("export", "python")),
        ("standalone Python export", ("standalone", "python")),
        ("code generation", ("generate", "code")),
    )

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
        self._docs_advisor_probe_at: float = 0.0
        self._docs_advisor_reachable: bool = True
        self._last_docs_advisor_meta: dict[str, Any] = {
            "advisor_attempted": False,
            "advisor_success": False,
            "fallback_reason": "not_attempted",
            "helper_latency_ms": None,
            "prompt_chars": 0,
            "snippet_count": 0,
            "schema_valid": False,
            "timeout_ms": int(self._docs_answer_cfg.helper_timeout_seconds * 1000),
            "cache_hit": False,
            "helper_finish_reason": None,
        }
        self._ask_grc_docs_cache: OrderedDict[tuple[str, ...], dict[str, Any]] = (
            OrderedDict()
        )
        self._search_blocks_cache: OrderedDict[
            tuple[str, int, str], dict[str, Any]
        ] = OrderedDict()
        self._maybe_record_baseline_checkpoint(reason="initial_session")

    def get_system_prompt(self) -> str:
        return build_system_prompt(self.chat_session_id)

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
        if tool_name in MVP_MODEL_TOOL_NAMES:
            return None
        if tool_name in self._tools:
            return None
        return self._tool_result(
            tool_name=tool_name,
            ok=False,
            message=(
                "[TOOL_NOT_ALLOWED_FOR_SURFACE] "
                f"Tool '{tool_name}' is not allowed for MVP model-facing execution."
            ),
            error_type=ErrorCode.TOOL_NOT_ALLOWED_FOR_SURFACE,
            active_tool_surface=self._active_tool_surface.name,
            allowed_model_tools=list(MVP_MODEL_TOOL_NAMES),
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
            logger.info("tool_call_rejected tool=%s error_type=%s", tool_name, validation_result.get("error_type"))
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
        if tool_name == "save_graph":
            normalized = self._normalize_save_graph_path(normalized)
        if tool_name == "inspect_graph":
            normalized = self._normalize_inspect_graph_args(normalized)
        if tool_name == "change_graph":
            normalized = self._normalize_change_graph_args(
                normalized,
                model_tool_call=model_tool_call,
            )
        return normalized

    def _normalize_change_graph_args(
        self,
        kwargs: dict[str, Any],
        *,
        model_tool_call: bool,
    ) -> dict[str, Any]:
        """Normalize common exact-identifier slips before schema validation."""
        normalized = copy.deepcopy(kwargs)
        for field_name in ("update_params", "update_states"):
            rows = normalized.get(field_name)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                block_id = row.get("block_id")
                if isinstance(block_id, str) and block_id.strip().startswith("block:"):
                    row.pop("block_id", None)
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
                "details"
                if isinstance(targets, list) and len(targets) > 0
                else "overview"
            )
            view = normalized["view"]
        if isinstance(view, str) and view.strip().lower() == "overview":
            normalized["targets"] = []
            normalized["params"] = []
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
        flowgraph = self.session.flowgraph
        if flowgraph is None:
            return {}
        result: dict[str, set[str]] = {}
        for block in flowgraph.blocks:
            params = block.params.get("parameters") if isinstance(block.params, dict) else None
            if not isinstance(params, dict):
                continue
            result[block.instance_name] = {str(key) for key in params}
        return result

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

    def _normalize_save_graph_path(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        raw_path = kwargs.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return kwargs
        requested = Path(raw_path.strip()).expanduser()
        requested_dir = requested.parent
        if requested_dir.exists():
            return kwargs

        candidates = self._save_path_candidates_from_user_text()
        if not candidates:
            return kwargs
        compatible = [
            candidate
            for candidate in candidates
            if candidate.name == requested.name and candidate.parent.exists()
        ]
        if len(compatible) != 1:
            return kwargs

        recovered = compatible[0]
        if recovered == requested:
            return kwargs
        kwargs = dict(kwargs)
        kwargs["path"] = str(recovered)
        logger.info(
            "save_graph_path_recovered requested=%s recovered=%s",
            str(requested),
            str(recovered),
        )
        return kwargs

    def _save_path_candidates_from_user_text(self) -> list[Path]:
        texts: list[str] = []
        if isinstance(self._turn_user_message, str) and self._turn_user_message.strip():
            texts.append(self._turn_user_message)
        for message in reversed(self.chat_history.get_messages()):
            if message.role != ChatMessageRole.User:
                continue
            text = _user_text_of(message)
            if text:
                texts.append(text)
                break
        if not texts:
            return []

        unique: dict[str, Path] = {}
        for text in texts:
            for match in _SAVE_PATH_HINT_PATTERN.finditer(text):
                candidate = match.group("path").strip()
                if not candidate:
                    continue
                expanded = str(Path(candidate).expanduser())
                unique.setdefault(expanded, Path(expanded))
        return list(unique.values())



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
        validation_kwargs = {
            k: v for k, v in kwargs.items()
            if k != "view"
        }
        validation_error = validate_runtime_tool_call(
            tool_name, validation_kwargs, self._tool_schema_map
        )
        if validation_error is None:
            return None
        return self._tool_result(tool_name=tool_name, ok=False, **validation_error)

    def should_stop_batch_after_result(
        self, tool_name: str, result: dict[str, Any]
    ) -> bool:
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
            tool_call_id = self._record_clarification_option_call(
                resolution["raw_reply"], opt
            )
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
            clarification_id = str(
                self._pending_clarification.get("clarification_id") or "pending"
            )
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

    @staticmethod
    def _guard_result(assistant_text: str) -> dict[str, Any]:
        """Structured result for a guard-level refusal (no tool execution)."""
        return {
            "ok": True,
            "model": "guard",
            "steps": 0,
            "tool_rounds_used": 0,
            "tool_calls_executed": 0,
            "assistant_text": assistant_text,
        }

    def check_unsupported_request(self, user_message: str) -> dict[str, Any] | None:
        """Return a factual refusal for unsupported runtime actions.

        Messages state the fact of what is unsupported (AGENTS.md: error
        strings return facts, never what to do about it). The available
        tool surface — declared in the system prompt — is the sole guide
        for what to call instead.
        """
        lowered = user_message.lower()
        if self._active_tool_surface.name == "mvp":
            for tool_name in self._INTERNAL_TOOL_NAMES_BLOCKED_IN_MVP:
                if not re.search(rf"\b{re.escape(tool_name)}\b", lowered):
                    continue
                for verb in self._INTERNAL_TOOL_NAME_REQUEST_VERBS:
                    if re.search(
                        rf"\b{re.escape(verb)}\b(?:\W+\w+){{0,4}}\W+{re.escape(tool_name)}\b",
                        lowered,
                    ):
                        return self._guard_result(
                            f"{tool_name} is not part of the model-facing tool surface."
                        )
        for keywords in self._RAW_YAML_EDIT_PATTERNS:
            if all(kw in lowered for kw in keywords):
                return self._guard_result(
                    "Raw .grc YAML editing is not supported through this surface."
                )
        for label, keywords in self._UNSUPPORTED_OPERATIONS:
            if all(kw in lowered for kw in keywords):
                return self._guard_result(f"{label} is not supported.")
        return None

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
            "search_blocks": self._search_blocks,
            "ask_grc_docs": self._ask_grc_docs,
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
        if (
            tool_name not in _JOURNALED_MUTATION_TOOLS
            and tool_name != "save_graph"
        ):
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
                save_path=(
                    result.get("path")
                    if tool_name == "save_graph"
                    else None
                ),
            )
            checkpoint_id = checkpoint.get("id")
            if (
                isinstance(checkpoint_id, str)
                and checkpoint_id
                and not result.get("checkpoint_id")
            ):
                result["checkpoint_id"] = checkpoint_id
            telemetry = result.get("dispatch_telemetry")
            if isinstance(telemetry, dict):
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
        result["tool"] = tool_name
        if default_message is not None and "message" not in result:
            result["message"] = default_message
        if include_active_session is None:
            include_active_session = tool_name != "change_graph"
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
                compact[key] = value[:max_list_items]
                compact["output_truncated"] = True
        validation_errors = compact.get("validation_errors")
        if (
            isinstance(validation_errors, list)
            and len(validation_errors) > max_validation_errors
        ):
            compact["validation_errors"] = validation_errors[:max_validation_errors]
            compact["output_truncated"] = True
        validation = compact.get("validation_result")
        if isinstance(validation, dict):
            stderr = validation.get("stderr")
            if isinstance(stderr, str) and len(stderr) > max_stderr_chars:
                validation = dict(validation)
                validation["stderr"] = stderr[: max_stderr_chars - 1].rstrip() + "…"
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

    def _search_blocks_cache_key(self, *, query: str, k: int) -> tuple[str, int, str]:
        return (
            query,
            k,
            self._search_blocks_version_token(),
        )

    def _search_blocks_version_token(self) -> str:
        catalog_token = _catalog_version_token(self.catalog_root)
        return f"catalog={catalog_token}"

    def _search_blocks_cache_get(
        self, key: tuple[str, int, str]
    ) -> dict[str, Any] | None:
        hit = self._search_blocks_cache.get(key)
        if hit is None:
            return None
        self._search_blocks_cache.move_to_end(key)
        return copy.deepcopy(hit)

    def _search_blocks_cache_put(
        self, key: tuple[str, int, str], payload: dict[str, Any]
    ) -> None:
        self._search_blocks_cache[key] = copy.deepcopy(payload)
        self._search_blocks_cache.move_to_end(key)
        while len(self._search_blocks_cache) > self._retrieval_cfg.lexical_cache_size:
            self._search_blocks_cache.popitem(last=False)

    def _ask_grc_docs_cache_key(
        self,
        *,
        question: str,
        k: int,
        retrieval_mode: str,
        sources: list[dict[str, str]],
        focus: str | None = None,
        cache_scope: str = "sources",
    ) -> tuple[str, ...]:
        source_digest = hashlib.sha1()
        for row in sources:
            title = str(row.get("title", "")).strip()
            source = str(row.get("source", "")).strip()
            excerpt = str(row.get("excerpt", "")).strip()
            source_digest.update(f"{title}|{source}|{excerpt}".encode())
        return (
            cache_scope,
            question,
            str(k),
            str(focus or ""),
            retrieval_mode,
            source_digest.hexdigest(),
            self._docs_answer_cfg.helper_prompt_version,
            self._docs_answer_cfg.helper_mode,
        )

    def _ask_grc_docs_cache_get(
        self,
        key: tuple[str, ...],
    ) -> dict[str, Any] | None:
        hit = self._ask_grc_docs_cache.get(key)
        if hit is None:
            return None
        self._ask_grc_docs_cache.move_to_end(key)
        return copy.deepcopy(hit)

    def _ask_grc_docs_cache_put(
        self,
        key: tuple[str, ...],
        payload: dict[str, Any],
    ) -> None:
        self._ask_grc_docs_cache[key] = copy.deepcopy(payload)
        self._ask_grc_docs_cache.move_to_end(key)
        while len(self._ask_grc_docs_cache) > self._docs_answer_cfg.answer_cache_size:
            self._ask_grc_docs_cache.popitem(last=False)

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
        enrich: bool = False,
    ) -> ToolResult:
        return search_blocks_wrapper(
            self,
            query=query,
            k=k,
            debug=debug,
            enrich=enrich,
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

    def _collect_docs_candidates(self) -> list[_DocsEvidenceCandidate]:
        return collect_docs_candidates_wrapper(self)

    def _rank_docs_candidates(
        self,
        *,
        question: str,
        candidates: list[_DocsEvidenceCandidate],
    ) -> list[_DocsEvidenceCandidate]:
        return rank_docs_candidates_wrapper(
            self,
            question=question,
            candidates=candidates,
        )

    def _clip_docs_snippets_for_helper(
        self,
        snippets: list[DocsAnswerSnippet],
    ) -> list[DocsAnswerSnippet]:
        return clip_docs_snippets_for_helper(self, snippets)

    @staticmethod
    def _is_tutorial_or_howto_query(query: str) -> bool:
        return is_tutorial_or_howto_query(query)

    @staticmethod
    def _docs_topic_terms(query: str) -> list[str]:
        return docs_topic_terms(query)

    @staticmethod
    def _docs_primary_terms(query: str) -> list[str]:
        return docs_primary_terms(query)

    @staticmethod
    def _clean_docs_excerpt(excerpt: str) -> str:
        return clean_docs_excerpt(excerpt)

    def _docs_low_value_reasons(self, *, candidate: _DocsEvidenceCandidate) -> list[str]:
        return docs_low_value_reasons(candidate=candidate)

    @staticmethod
    def _is_procedural_walkthrough_text(text: str) -> bool:
        return is_procedural_walkthrough_text(text)

    @staticmethod
    def _is_block_definition_query(question: str) -> bool:
        return is_block_definition_query(question)

    @staticmethod
    def _extract_block_definition_subject(question: str) -> str | None:
        return extract_block_definition_subject(question)

    @staticmethod
    def _extract_docs_subject(question: str) -> str | None:
        return extract_docs_subject(question)

    def _build_catalog_assisted_candidate(
        self,
        *,
        question: str,
    ) -> _DocsEvidenceCandidate | None:
        return build_catalog_assisted_candidate(self, question=question)

    def _should_catalog_assist(
        self,
        question: str,
        ranked_candidates: list[_DocsEvidenceCandidate],
    ) -> bool:
        return should_catalog_assist(question, ranked_candidates)

    @staticmethod
    def _classify_docs_answer_type(question: str) -> str:
        return classify_docs_answer_type(question)

    @staticmethod
    def _text_matches_term_or_synonym(text: str, term: str) -> bool:
        return text_matches_term_or_synonym(text, term)

    def _select_docs_candidates_for_answer_type(
        self,
        *,
        question: str,
        answer_type: str,
        ranked_candidates: list[_DocsEvidenceCandidate],
        limit: int,
    ) -> list[_DocsEvidenceCandidate]:
        return select_docs_candidates_for_answer_type(
            question=question,
            answer_type=answer_type,
            ranked_candidates=ranked_candidates,
            limit=limit,
        )

    @staticmethod
    def _extract_comparison_sides(question: str) -> _DocsComparisonSides | None:
        return extract_comparison_sides(question)

    @staticmethod
    def _sentence_list(text: str) -> list[str]:
        return sentence_list(text)

    def _pick_typed_sentence(
        self,
        *,
        candidate: _DocsEvidenceCandidate,
        required_terms: tuple[str, ...],
        allow_procedural: bool,
        min_term_hits: int = 1,
    ) -> str:
        return pick_typed_sentence(
            candidate=candidate,
            required_terms=required_terms,
            allow_procedural=allow_procedural,
            min_term_hits=min_term_hits,
        )

    @staticmethod
    def _minimum_required_term_hits(required_terms: tuple[str, ...]) -> int:
        return minimum_required_term_hits(required_terms)

    def _required_terms_for_answer_type(
        self,
        *,
        question: str,
        answer_type: str,
    ) -> tuple[str, ...]:
        return required_terms_for_answer_type(question=question, answer_type=answer_type)

    def _build_docs_source_quality(
        self,
        *,
        question: str,
        answer_type: str,
        selected_candidates: list[_DocsEvidenceCandidate],
    ) -> dict[str, Any]:
        return build_docs_source_quality_wrapper(
            self,
            question=question,
            answer_type=answer_type,
            selected_candidates=selected_candidates,
        )

    def _helper_eligibility_for_docs_answer(
        self,
        *,
        question: str,
        answer_type: str,
        source_quality: dict[str, Any],
        selected_candidates: list[_DocsEvidenceCandidate],
        typed_answer: str,
        typed_insufficient: bool,
    ) -> tuple[bool, str]:
        return helper_eligibility_for_docs_answer(
            question=question,
            answer_type=answer_type,
            source_quality=source_quality,
            selected_candidates=selected_candidates,
            typed_answer=typed_answer,
            typed_insufficient=typed_insufficient,
        )

    def _helper_candidates_for_docs_answer(
        self,
        *,
        question: str,
        answer_type: str,
        ranked_candidates: list[_DocsEvidenceCandidate],
    ) -> list[_DocsEvidenceCandidate]:
        return helper_candidates_for_docs_answer(
            question=question,
            answer_type=answer_type,
            ranked_candidates=ranked_candidates,
        )

    @staticmethod
    def _clean_catalog_summary_for_answer(name: str, summary: str) -> str:
        return clean_catalog_summary_for_answer(name, summary)

    @staticmethod
    def _catalog_block_purpose_sentence(name: str, summary: str) -> str:
        return catalog_block_purpose_sentence(name, summary)

    def _build_typed_docs_answer(
        self,
        *,
        question: str,
        ranked_candidates: list[_DocsEvidenceCandidate],
        answer_type: str,
    ) -> tuple[str, bool]:
        return build_typed_docs_answer_wrapper(
            self,
            question=question,
            ranked_candidates=ranked_candidates,
            answer_type=answer_type,
        )

    def _build_fallback_answer(
        self,
        *,
        question: str,
        ranked_candidates: list[_DocsEvidenceCandidate],
        evidence_strong: bool,
    ) -> tuple[str, bool]:
        return build_fallback_answer(
            self,
            question=question,
            ranked_candidates=ranked_candidates,
            evidence_strong=evidence_strong,
        )

    def _run_docs_answer_advisor(
        self,
        *,
        question: str,
        answer_type: str,
        snippets: list[DocsAnswerSnippet],
        focus: str | None,
    ) -> dict[str, Any] | None:
        return run_docs_answer_advisor_wrapper(
            self,
            question=question,
            answer_type=answer_type,
            snippets=snippets,
            focus=focus,
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
            self.session.state_revision != before_revision
            or self.session.is_dirty != before_dirty
        )
        clarification_returned = bool(result.get("clarification_options")) or bool(
            result.get("clarification_required")
        ) or result.get("error_type") in {
            "clarification_required",
            "ambiguous_connection",
            "ambiguous_rewire_old_connection",
            "ambiguous_rewire_new_endpoint",
            "ambiguous_target",
        }
        checkpoint_created = isinstance(result.get("checkpoint_id"), str) and bool(
            result.get("checkpoint_id")
        )
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
        is_valid = loaded.validate()
        if not is_valid:
            return self._tool_result(
                "load_grc",
                ok=False,
                message="Refusing to activate loaded graph because validation failed.",
                error_type=(
                    ErrorCode.VALIDATION_TIMEOUT
                    if loaded.last_validation_returncode == -2
                    else ErrorCode.GNU_VALIDATION_FAILED
                ),
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
            payload = self._connection_clarification_payload(
                resolution.ambiguous_candidates
            )
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
                    "connection_id": resolved_old_connection_id,
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
        return resolve_rewire_new_endpoint_args_wrapper(
            self,
            old_connection_id=old_connection_id,
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        )

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
        return resolve_old_rewire_connection_id_wrapper(
            self,
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
                    if isinstance(result.get("normalized_operations"), list) and len(result["normalized_operations"]) == 1:
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
                    result["hint"] = (
                        "Edit applied and autosaved — validation failed."
                    )
                else:
                    result["hint"] = (
                        "Edit applied, validated, and autosaved."
                    )
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
                message="This new graph has no file path yet. Call save_graph(path=\"...\").",
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
                    message=(
                        "Refusing to write to protected canonical/example graph paths. "
                        f"Choose a copied working path outside {unsafe_root}."
                    ),
                    error_type=ErrorCode.SAVE_REFUSED,
                )
            current_path = (
                self.session.path.resolve(strict=False)
                if self.session.path is not None
                else None
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
                            "on disk after this session loaded or saved it. Reload "
                            "the graph before saving."
                        ),
                        error_type=ErrorCode.STALE_REVISION,
                        path=str(resolved_target),
                        file_integrity=_compact_save_file_integrity(file_integrity),
                    )
        if (
            not allow_invalid
            and (
            not self._last_validation_ok
            or self._last_validated_state_revision != self.session.state_revision
            )
        ):
            validation = self._validate_graph()
            if validation.get("ok") is not True or not bool(validation.get("valid")):
                return self._tool_result(
                    tool_name="save_graph",
                    ok=False,
                    message=(
                        "Refusing to save before successful validation. "
                        "Fix the graph and validate again before saving."
                    ),
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
