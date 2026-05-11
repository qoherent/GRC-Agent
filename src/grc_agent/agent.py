"""Thin runtime wrapper for routed package-level `.grc` tools."""

import copy
from collections import OrderedDict
from functools import lru_cache
import hashlib
import json
import logging
import re
import socket
from pathlib import Path
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from grc_agent.catalog import describe_block
from grc_agent.catalog.loaders import build_catalog_snapshot
from grc_agent._payload import ErrorCode
from grc_agent.config import AgentConfig, default_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.history import (
    GraphHistoryJournal,
    GraphSnapshot,
    lineage_key_for_session,
    operation_type_from_result,
    snapshot_session,
)
from grc_agent.manual import search_manual
from grc_agent.manual.search import DEFAULT_MANUAL_ROOT
from grc_agent.retrieval.search import _search_grc_with_context
from grc_agent.retrieval.vector import semantic_search_grc, vector_index_version_token
from grc_agent.runtime.clarification import ClarificationOption, ClarificationRequest
from grc_agent.runtime.prompt import build_system_prompt
from grc_agent.runtime.docs_answer_advisor import (
    DocsAnswerLlamaClient,
    DocsAnswerSnippet,
    run_docs_answer_advisor,
)
from grc_agent.runtime.path_safety import (
    resolved_path as resolve_runtime_path,
    unsafe_graph_root_for_path,
)
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.tool_surface import (
    MVP_MODEL_TOOL_NAMES,
    MODEL_TOOL_NAMES_ORDERED,
    tool_surface_for_legacy_flag,
)
from grc_agent.runtime.docs_answer import (
    _DOCS_TOPIC_SYNONYMS,
    _DocsComparisonSides,
    _DocsEvidenceCandidate,
    ask_grc_docs as ask_grc_docs_wrapper,
    build_docs_source_quality as build_docs_source_quality_wrapper,
    build_typed_docs_answer as build_typed_docs_answer_wrapper,
    collect_docs_candidates as collect_docs_candidates_wrapper,
    rank_docs_candidates as rank_docs_candidates_wrapper,
)
from grc_agent.runtime.wrappers.inspect_graph import inspect_graph as inspect_graph_wrapper
from grc_agent.runtime.wrappers.lifecycle import (
    load_graph_explicit as load_graph_explicit_wrapper,
    save_graph_explicit as save_graph_explicit_wrapper,
)
from grc_agent.runtime.wrappers.search_blocks import (
    search_blocks as search_blocks_wrapper,
)
from grc_agent.runtime.wrappers.change_graph.dispatcher import dispatch_change_graph
from grc_agent.runtime.wrappers.change_graph_validation import (
    canonicalize_change_graph_target_ref,
    validate_change_graph_operation_args,
)
from grc_agent.runtime.turn_plan import (
    INTENT_ADD_VARIABLE,
    INTENT_PREVIEW,
    INTENT_REWIRE,
    TurnPlan,
    build_turn_plan,
    enrich_turn_plan_with_graph_context,
)
from grc_agent.runtime.transaction_normalization import TransactionNormalizer
from grc_agent.runtime_tool_validation import (
    build_tool_schema_map,
    validate_runtime_tool_call,
)
from grc_agent.session_ops import connection_id as render_connection_id, parse_connection_id
from grc_agent.session import get_grc_context, load_grc, summarize_graph
from grc_agent.session.insertion_suggestions import suggest_insertions
from grc_agent.transaction import apply_edit, propose_edit
from grc_agent.turn_guard import build_continuation_prompt

logger = logging.getLogger(__name__)

ToolResult = dict[str, Any]
ToolCallable = Callable[..., ToolResult]
HistoryEntry = dict[str, Any]

_ADD_VARIABLE_EXACT_PATTERN = re.compile(
    r"\b(?:add|create)\s+(?:a\s+)?variable\s+"
    r"(?:(?:called|named)\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?:(?:set\s+to)|(?:with\s+(?:a\s+)?value\s+of)|(?:with\s+value)|value)\s+"
    r"(?P<value>[^\n]+)",
    re.IGNORECASE,
)
_CONNECTION_ID_TOKEN_PATTERN = re.compile(
    r"[A-Za-z0-9_./-]+:[^\s,;()]+->[A-Za-z0-9_./-]+:[^\s,;()]+"
)
_SAVE_PATH_HINT_PATTERN = re.compile(r"(?P<path>(?:~|/)[^\s'\"`]+\.grc)\b")
_ALIAS_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
_SEARCH_BLOCK_SUMMARY_MAX_CHARS = 120
_INSTALLED_GRAPH_ROOTS = (
    Path("/usr/share/gnuradio/examples"),
    Path("/usr/local/share/gnuradio/examples"),
)
_CANONICAL_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "data"
_DOCS_QUERY_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "across",
        "all",
        "an",
        "and",
        "are",
        "at",
        "be",
        "between",
        "block",
        "blocks",
        "briefly",
        "by",
        "can",
        "checking",
        "context",
        "difference",
        "different",
        "differ",
        "do",
        "does",
        "explain",
        "for",
        "gnu",
        "high",
        "how",
        "in",
        "interact",
        "is",
        "keep",
        "level",
        "meaning",
        "mean",
        "of",
        "or",
        "please",
        "radio",
        "short",
        "the",
        "this",
        "to",
        "used",
        "using",
        "what",
        "with",
    }
)
_DOCS_NAVIGATION_MARKERS = (
    "beginner tutorials",
    "please leave tutorials-related feedback",
    "discussion page of this article",
    "jump to navigation",
    "table of contents",
    "table of content",
)
_DOCS_MENU_TITLE_MARKERS = (
    "tutorials",
    "main page",
    "index",
)
_DOCS_PROCEDURAL_MARKERS = (
    "add the",
    "drag in",
    "connect the",
    "click ",
    "right-click",
    "setting up",
    "set up",
    "we will be using",
    "below to show",
    "workspace",
    "flowgraph below",
)
_DOCS_GENERIC_ANSWER_MARKERS = (
    "gnu radio is a free",
    "gnu radio is a framework",
    "software development toolkit",
    "what is gnu radio",
)
_DOCS_LIST_MARKERS_RE = re.compile(r"(?:^|\s)(?:\d+\.\s+|\*\s+)")
_DOCS_GENERIC_TOPIC_TERMS = frozenset(
    {
        "concept",
        "definition",
        "gnu",
        "radio",
        "signal",
        "system",
        "type",
    }
)
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


def _alias_candidates_for_block(block_id: str, label: str) -> set[str]:
    candidates = {
        block_id.lower().strip(),
        label.lower().strip(),
        _normalize_alias_key(block_id),
        _normalize_alias_key(label),
        f"catalog:block:{block_id}".lower(),
        _normalize_alias_key(f"catalog:block:{block_id}"),
    }
    suffix = block_id
    if "." in suffix:
        suffix = suffix.rsplit(".", 1)[-1]
    if "_" in suffix:
        suffix = suffix.split("_", 1)[-1]
    suffix = suffix.strip().lower()
    if suffix:
        candidates.add(suffix)
        candidates.add(_normalize_alias_key(suffix))
    return {item for item in candidates if item}


def _compact_block_summary(summary: Any) -> str:
    if not isinstance(summary, str):
        return ""
    compact = " ".join(summary.split())
    if len(compact) <= _SEARCH_BLOCK_SUMMARY_MAX_CHARS:
        return compact
    return compact[: _SEARCH_BLOCK_SUMMARY_MAX_CHARS - 1].rstrip() + "…"


@lru_cache(maxsize=4)
def _catalog_alias_to_block_map(catalog_root: str | None) -> dict[str, str]:
    snapshot = build_catalog_snapshot(catalog_root)
    first_seen: dict[str, str] = {}
    ambiguous: set[str] = set()
    for block_id, block in snapshot.blocks.items():
        label = str(block.payload.get("label", "")).strip()
        for alias in _alias_candidates_for_block(block_id, label):
            existing = first_seen.get(alias)
            if existing is None:
                first_seen[alias] = block_id
                continue
            if existing != block_id:
                ambiguous.add(alias)
    for alias in ambiguous:
        first_seen.pop(alias, None)
    return first_seen


@lru_cache(maxsize=4)
def _catalog_version_token(catalog_root: str | None) -> str:
    snapshot = build_catalog_snapshot(catalog_root)
    newest_mtime = 0
    for path in [*snapshot.files.block, *snapshot.files.tree, *snapshot.files.domain]:
        try:
            newest_mtime = max(newest_mtime, path.stat().st_mtime_ns)
        except OSError:
            continue
    return f"{snapshot.root}|blocks={len(snapshot.blocks)}|mtime_ns={newest_mtime}"


def _manual_corpus_version_token(root: Path = DEFAULT_MANUAL_ROOT) -> str:
    try:
        resolved = root.resolve()
    except Exception:
        resolved = root
    if not resolved.is_dir():
        return f"{resolved}|missing"
    newest_mtime = 0
    count = 0
    for path in resolved.glob("*.md"):
        count += 1
        try:
            newest_mtime = max(newest_mtime, path.stat().st_mtime_ns)
        except OSError:
            continue
    return f"{resolved}|files={count}|mtime_ns={newest_mtime}"


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
    _EXPLICIT_SAVE_INTENT_TOKENS: tuple[str, ...] = (
        "save",
        "persist",
        "write out",
        "write it out",
        "write a copy",
        "save a copy",
        "copy to path",
        "save to",
    )
    _EXPLICIT_LOAD_INTENT_TOKENS: tuple[str, ...] = (
        "load",
        "open",
        "switch to",
        "switch over to",
        "reload",
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
        self.history: list[HistoryEntry] = []
        self._last_validated_state_revision: int | None = None
        self._last_validation_ok: bool | None = None
        self._reset_validation_tracking()
        self._tools = self._build_tool_registry()
        self._mvp_tools = self._build_mvp_tool_registry()
        self._active_tool_surface = tool_surface_for_legacy_flag(
            legacy_model_tool_surface=self.config.legacy_model_tool_surface
        )
        self._tool_schemas = build_tool_schemas(self._active_tool_surface.model_tool_names)
        self._all_tool_schemas = build_tool_schemas(MODEL_TOOL_NAMES_ORDERED)
        self._tool_schema_map = build_tool_schema_map(self._all_tool_schemas)
        self._record_active_session_history(reason="initial_session")
        self._turn_required_actions: set[str] = set()
        self._turn_completed_actions: set[str] = set()
        self._turn_any_execution_failed = False
        self._turn_continuation_budget = 0
        self._turn_plan = TurnPlan()
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
        self._ask_grc_docs_cache: OrderedDict[
            tuple[str, int, str, str, str, str], dict[str, Any]
        ] = OrderedDict()
        self._search_blocks_cache: OrderedDict[
            tuple[str, int, str], dict[str, Any]
        ] = OrderedDict()
        self._maybe_record_baseline_checkpoint(reason="initial_session")

    def get_system_prompt(self) -> str:
        return build_system_prompt(
            legacy=self._active_tool_surface.name == "legacy"
        )

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
        """Return schemas narrowed by the active typed turn policy."""
        if allowed_tool_names is None:
            allowed_order = tuple(self._turn_plan.allowed_tools)
        elif isinstance(allowed_tool_names, set):
            allowed_order = tuple(
                name for name in MODEL_TOOL_NAMES_ORDERED if name in allowed_tool_names
            )
        else:
            allowed_order = tuple(allowed_tool_names)
        schemas_by_name = {schema["function"]["name"]: schema for schema in self._tool_schemas}
        return [
            self._schema_narrowed_for_turn(schemas_by_name[name])
            for name in allowed_order
            if name in schemas_by_name
        ]

    def _surface_tool_gate_result(
        self,
        *,
        tool_name: str,
        model_tool_call: bool,
    ) -> ToolResult | None:
        """Reject disallowed model-driven tools for the active surface profile."""
        if not model_tool_call:
            return None
        if self._active_tool_surface.name != "mvp":
            return None
        if tool_name in MVP_MODEL_TOOL_NAMES:
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
        kwargs = self.normalize_tool_call_arguments(tool_name, kwargs)
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
        if tool_name in {"save_graph", "save_graph_explicit"}:
            normalized = self._normalize_save_graph_path(normalized)
        return normalized

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
        for turn in reversed(self.history):
            if turn.get("role") != "user":
                continue
            content = turn.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content)
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

    def _current_user_text(self) -> str:
        if isinstance(self._turn_user_message, str) and self._turn_user_message.strip():
            return self._turn_user_message.strip()
        for turn in reversed(self.history):
            if turn.get("role") != "user":
                continue
            content = turn.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
        return ""

    def _has_explicit_save_intent(self) -> bool:
        text = self._current_user_text().lower()
        return bool(text) and any(
            token in text for token in self._EXPLICIT_SAVE_INTENT_TOKENS
        )

    def _has_explicit_load_intent(self) -> bool:
        text = self._current_user_text().lower()
        return bool(text) and any(
            token in text for token in self._EXPLICIT_LOAD_INTENT_TOKENS
        )

    @staticmethod
    def _resolved_path(path_value: str | Path) -> Path:
        return resolve_runtime_path(path_value)

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
        """Validate one runtime tool call against the declared public schema."""
        surface_gate = self._surface_tool_gate_result(
            tool_name=tool_name,
            model_tool_call=model_tool_call,
        )
        if surface_gate is not None:
            return surface_gate
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
        return tool_name in {
            "new_grc",
            "load_grc",
            "load_graph_explicit",
            "apply_edit",
            "remove_connection",
            "validate_graph",
            "save_graph",
            "save_graph_explicit",
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

        # Expire if session revision changed since clarification was created
        if (
            self._pending_clarification_revision is not None
            and self.session.state_revision != self._pending_clarification_revision
        ):
            self._clear_pending_clarification()
            return {
                "mode": "expired",
                "text": "The pending question is no longer valid because the graph has changed.",
            }

        raw = user_message.strip()
        if not raw:
            return {"mode": "reminder", "text": self._pending_clarification_reminder()}

        req = ClarificationRequest.from_dict(self._pending_clarification)
        selected_label = self._parse_clarification_option_label(
            raw,
            labels={opt.label for opt in req.options},
        )

        if selected_label is not None:
            for opt in req.options:
                if opt.label.upper() == selected_label:
                    tool_call_id = self._record_clarification_option_call(raw, opt)
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

        label_reply = self._parse_clarification_option_label(
            raw,
            labels={"A", "B", "C"},
        )
        if label_reply is not None:
            # Label in message but not matching any stored option
            return {
                "mode": "reminder",
                "text": (
                    f"'{label_reply}' is not a valid option. "
                    f"Choose one of: {', '.join(o.label for o in req.options)}. "
                    f"Or use D / free text to describe what you want instead."
                ),
            }

        # D or custom / free text
        custom_label = req.custom_option.label.upper()
        custom_selected = self._parse_clarification_option_label(
            raw,
            labels={custom_label},
        )
        if custom_selected == custom_label or len(raw) > 1:
            self._clear_pending_clarification()
            return {
                "mode": "custom",
                "text": "Continuing with custom request.",
                "custom_hint": raw,
            }

        return {"mode": "reminder", "text": self._pending_clarification_reminder()}

    @staticmethod
    def _parse_clarification_option_label(
        raw: str,
        *,
        labels: set[str],
    ) -> str | None:
        token = raw.strip().upper()
        if not token:
            return None
        if len(token) == 2 and token[1] in ").":
            token = token[0]
        if len(token) == 1 and token in {label.upper() for label in labels}:
            return token
        return None

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
        self.history.append({"role": "user", "content": raw_reply})
        self.history.append(
            {
                "role": "assistant",
                "content": "",
                "clarification_selection": {
                    "label": option.label,
                    "clarification_id": clarification_id,
                },
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": option.tool_name,
                            "arguments": json.dumps(option.tool_args, sort_keys=True),
                        },
                    }
                ],
            }
        )
        return tool_call_id

    def _record_clarification_option_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> None:
        self.history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": result,
            }
        )

    def _pending_clarification_reminder(self) -> str:
        if self._pending_clarification is None:
            return ""
        opts = self._pending_clarification.get("options", [])
        lines = ["A pending choice requires your response:"]
        for o in opts:
            lines.append(f"  {o['label']}) {o['title']}: {o['description']}")
        lines.append("  D) Other / custom (free text)")
        return "\n".join(lines)

    def _store_pending_clarification(self, payload: dict[str, Any]) -> None:
        """Store a clarification produced by a tool for user resolution."""
        stored = copy.deepcopy(payload)
        revision = stored.get("state_revision")
        if not isinstance(revision, int):
            revision = self.session.state_revision
            stored["state_revision"] = revision
        for option in stored.get("options", []):
            if not isinstance(option, dict):
                continue
            metadata = option.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                option["metadata"] = metadata
            metadata.setdefault("state_revision", revision)
        self._pending_clarification = stored
        self._pending_clarification_revision = revision

    def _clear_pending_clarification(self) -> None:
        self._pending_clarification = None
        self._pending_clarification_revision = None

    def check_unsupported_request(self, user_message: str) -> dict[str, Any] | None:
        """Return a refusal response for unsupported runtime actions."""
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
        unsupported_operations: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("undo", ("undo",)),
            ("redo", ("redo",)),
            ("Python export", ("export", "python")),
            ("standalone Python export", ("standalone", "python")),
            ("code generation", ("generate", "code")),
        )
        for label, keywords in unsupported_operations:
            if all(kw in lowered for kw in keywords):
                return {
                    "ok": True,
                    "model": "guard",
                    "steps": 0,
                    "tool_rounds_used": 0,
                    "tool_calls_executed": 0,
                    "assistant_text": (
                        f"{label} is unsupported. I cannot perform that action "
                        "through the current verified GRC tool contract."
                    ),
                }
        return None

    def check_ambiguous_connection_edit(self, user_message: str) -> dict[str, Any] | None:
        """Ask for exact endpoints before vague connection mutations reach the model."""
        lowered = user_message.lower()
        connection_edit = any(
            phrase in lowered
            for phrase in (
                "disconnect",
                "unwire",
                "remove connection",
                "delete connection",
                "remove the wire",
                "delete the wire",
            )
        )
        if not connection_edit:
            return None

        has_exact_endpoint_language = (
            "->" in user_message
            or (
                " output " in f" {lowered} "
                and " input " in f" {lowered} "
                and any(char.isdigit() for char in user_message)
            )
            or ("src_block" in lowered and "dst_block" in lowered)
        )
        if has_exact_endpoint_language:
            return None

        return {
            "ok": True,
            "model": "guard",
            "steps": 0,
            "tool_rounds_used": 0,
            "tool_calls_executed": 0,
            "assistant_text": (
                "I need exact connection endpoints before changing wires. "
                "Provide source block, source port, destination block, and destination port, "
                "or ask me to inspect the graph first."
            ),
        }

    def init_turn_requirements(self, user_message: str) -> TurnPlan:
        """Parse user message and initialise typed turn-completion tracking."""
        self._turn_user_message = user_message
        plan = build_turn_plan(user_message)
        if self.session.flowgraph is not None:
            plan = enrich_turn_plan_with_graph_context(
                plan,
                user_message,
                self.session.flowgraph.blocks,
            )
        self._turn_plan = plan
        self._turn_required_actions = set(self._turn_plan.required_actions)
        self._turn_completed_actions = set()
        self._turn_any_execution_failed = False
        self._turn_continuation_budget = 1
        return self._turn_plan

    def active_turn_plan(self) -> TurnPlan:
        """Return the current typed turn plan."""
        return self._turn_plan

    def validate_turn_route(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        *,
        allowed_tool_names: set[str] | tuple[str, ...] | None = None,
    ) -> ToolResult | None:
        """Reject model tool calls that contradict the active turn policy."""
        plan = self._turn_plan
        effective_allowed = (
            set(plan.allowed_tools)
            if allowed_tool_names is None
            else set(allowed_tool_names)
        )
        if tool_name not in effective_allowed:
            return self._route_mismatch_result(
                tool_name,
                f"Tool `{tool_name}` is not allowed for intent `{plan.intent}`.",
            )
        if not plan.expected_op_types or tool_name not in {"apply_edit", "propose_edit"}:
            return None

        op_types = self._transaction_op_types(kwargs.get("transaction"))
        if not op_types:
            return None
        unexpected = sorted(set(op_types) - set(plan.expected_op_types))
        if not unexpected:
            return None
        return self._route_mismatch_result(
            tool_name,
            (
                f"Intent `{plan.intent}` only allows operation type(s) "
                f"{', '.join(plan.expected_op_types)}, but the model attempted "
                f"{', '.join(unexpected)}."
            ),
        )

    def record_tool_completion(self, tool_name: str, ok: bool) -> None:
        """Record a tool execution result for turn-completion tracking."""
        if ok and tool_name in self._turn_required_actions:
            self._turn_completed_actions.add(tool_name)
        if not ok:
            self._turn_any_execution_failed = True

    def mark_turn_recovery_success(self) -> None:
        """Allow normal turn nudges after a bounded correction succeeds."""
        self._turn_any_execution_failed = False

    def check_turn_continuation(self) -> tuple[bool, str]:
        """Return (should_nudge, nudge_text) if remaining actions need a nudge."""
        remaining = self._turn_required_actions - self._turn_completed_actions
        if not remaining or self._turn_continuation_budget <= 0 or self._turn_any_execution_failed:
            return False, ""
        self._turn_continuation_budget -= 1
        return True, build_continuation_prompt(remaining)

    def deterministic_turn_tool_call(
        self,
        user_message: str,
    ) -> dict[str, Any] | None:
        """Return an exact verified-tool call for simple typed intents.

        This keeps deterministic routing policy behind GrcAgent while the
        llama.cpp adapter remains a transport/bounded-loop layer. Callers must
        still run route validation, schema validation, and normal tool
        execution before any graph mutation.
        """
        if self._turn_plan.intent == INTENT_REWIRE:
            return self._deterministic_exact_rewire_tool_call(user_message)
        if self._turn_plan.intent == INTENT_ADD_VARIABLE:
            return self._deterministic_exact_add_variable_tool_call(user_message)
        return None

    def _deterministic_exact_add_variable_tool_call(
        self,
        user_message: str,
    ) -> dict[str, Any] | None:
        match = _ADD_VARIABLE_EXACT_PATTERN.search(user_message)
        if match is None:
            return None
        value = re.split(
            r"(?:\s+(?:and|then)\s+)|[,;]",
            match.group("value").strip(),
            maxsplit=1,
        )[0]
        value = value.strip().strip("\"'").rstrip(".")
        if not value:
            return None
        tool_name = (
            "propose_edit"
            if INTENT_PREVIEW in {self._turn_plan.intent}
            or (
                "propose_edit" in self._turn_plan.required_actions
                and "apply_edit" not in self._turn_plan.required_actions
            )
            else "apply_edit"
        )
        if tool_name not in self._turn_plan.allowed_tools:
            return None
        return {
            "id": "deterministic_add_variable",
            "name": tool_name,
            "arguments": {
                "transaction": {
                    "op_type": "add_block",
                    "block_type": "variable",
                    "instance_name": match.group("name"),
                    "parameters": {"value": value},
                }
            },
        }

    def _deterministic_exact_rewire_tool_call(
        self,
        user_message: str,
    ) -> dict[str, Any] | None:
        if "rewire_connection" not in self._turn_plan.allowed_tools:
            return None

        parsed_connection_ids: list[tuple[str, int | str, str, int | str]] = []
        for match in _CONNECTION_ID_TOKEN_PATTERN.finditer(user_message):
            parsed = parse_connection_id(match.group(0).rstrip(".,;"))
            if parsed is None:
                continue
            parsed_connection_ids.append(parsed)
            if len(parsed_connection_ids) == 2:
                break

        if len(parsed_connection_ids) != 2:
            return None

        old_src_block, old_src_port, old_dst_block, old_dst_port = parsed_connection_ids[0]
        new_src_block, new_src_port, new_dst_block, new_dst_port = parsed_connection_ids[1]
        return {
            "id": "deterministic_rewire_connection",
            "name": "rewire_connection",
            "arguments": {
                "old_connection_id": render_connection_id(
                    old_src_block,
                    old_src_port,
                    old_dst_block,
                    old_dst_port,
                ),
                "new_src_block": new_src_block,
                "new_src_port": new_src_port,
                "new_dst_block": new_dst_block,
                "new_dst_port": new_dst_port,
            },
        }

    def _route_mismatch_result(self, tool_name: str, message: str) -> ToolResult:
        return self._tool_result(
            tool_name=tool_name,
            ok=False,
            message=(
                f"{message} No graph change was made because the requested route "
                "did not match the typed turn policy."
            ),
            error_type="route_mismatch",
            turn_plan=self._turn_plan.as_dict(),
            allowed_tools=list(self._turn_plan.allowed_tools),
            expected_op_types=list(self._turn_plan.expected_op_types),
        )

    @staticmethod
    def _transaction_op_types(transaction: Any) -> tuple[str, ...]:
        operations = transaction if isinstance(transaction, list) else [transaction]
        op_types: list[str] = []
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            op_type = operation.get("op_type")
            if isinstance(op_type, str) and op_type:
                op_types.append(op_type)
        return tuple(op_types)

    def _turn_exact_new_rewire_endpoint(
        self,
    ) -> tuple[str, int | str, str, int | str] | None:
        """Return exact `to src:port->dst:port` endpoint for schema narrowing."""
        for match in re.finditer(r"\bto\s+([^\s,;]+->[^\s,;]+)", self._turn_user_message):
            parsed = parse_connection_id(match.group(1).rstrip(".:"))
            if parsed is not None:
                return parsed
        return None

    def _schema_narrowed_for_turn(self, schema: dict[str, Any]) -> dict[str, Any]:
        name = schema["function"]["name"]
        if name == "get_grc_context" and self._turn_plan.target_ref:
            narrowed = copy.deepcopy(schema)
            parameters = narrowed["function"]["parameters"]
            node_id = parameters["properties"]["node_id"]
            node_id["enum"] = [self._turn_plan.target_ref]
            node_id["description"] = (
                "Exact loaded session node selected by the typed turn policy."
            )
            return narrowed
        if (
            name == "rewire_connection"
            and self._turn_plan.intent == "rewire"
            and (exact_new_endpoint := self._turn_exact_new_rewire_endpoint()) is not None
        ):
            narrowed = copy.deepcopy(schema)
            parameters = narrowed["function"]["parameters"]
            parameters["required"] = [
                "new_src_block",
                "new_src_port",
                "new_dst_block",
                "new_dst_port",
            ]
            src_block, src_port, dst_block, dst_port = exact_new_endpoint
            properties = parameters["properties"]
            properties["new_src_block"]["enum"] = [src_block]
            properties["new_src_port"]["enum"] = [src_port]
            properties["new_dst_block"]["enum"] = [dst_block]
            properties["new_dst_port"]["enum"] = [dst_port]
            return narrowed
        if (
            name not in {"apply_edit", "propose_edit"}
            or self._turn_plan.expected_op_types
            not in {("update_states",), ("update_params",)}
        ):
            return schema

        narrowed = copy.deepcopy(schema)
        parameters = narrowed["function"]["parameters"]
        transaction = parameters["properties"]["transaction"]
        if self._turn_plan.expected_op_types == ("update_params",):
            if not self._turn_plan.target_ref or not self._turn_plan.parameter_name:
                return schema
            transaction.clear()
            transaction.update(
                {
                    "type": "object",
                    "description": "One update_params operation for an exact loaded block parameter.",
                    "properties": {
                        "op_type": {
                            "type": "string",
                            "enum": ["update_params"],
                        },
                        "instance_name": {
                            "type": "string",
                            "enum": [self._turn_plan.target_ref],
                        },
                        "params": {
                            "type": "object",
                            "properties": {
                                self._turn_plan.parameter_name: {
                                    "type": ["string", "number", "integer", "boolean"],
                                }
                            },
                            "required": [self._turn_plan.parameter_name],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["op_type", "instance_name", "params"],
                    "additionalProperties": False,
                }
            )
            return narrowed

        transaction.clear()
        transaction.update(
            {
                "type": "object",
                "description": (
                    "One update_states operation. Use this only to enable or disable "
                    "one loaded block instance."
                ),
                "properties": {
                    "op_type": {
                        "type": "string",
                        "enum": ["update_states"],
                    },
                    "instance_name": {
                        "type": "string",
                        "description": "Loaded block instance name.",
                    },
                    "state": {
                        "type": "string",
                        "enum": ["enabled", "disabled"],
                    },
                },
                "required": ["op_type", "instance_name", "state"],
                "additionalProperties": False,
            }
        )
        return narrowed

    @staticmethod
    def looks_like_transaction_payload(payload: Any) -> bool:
        return TransactionNormalizer.looks_like_transaction_payload(payload)

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
            "history_length": len(self.history),
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

    def run_step_fake(
        self, user_msg: str, fake_assistant_actions: list[HistoryEntry]
    ) -> None:
        """
        A fake loop step to test the plumbing.
        fake_assistant_actions is a list of dicts.
        If it has 'tool', it's a tool call. If it has 'text', it's a message.
        """
        self.history.append({"role": "user", "content": user_msg})
        self._turn_user_message = user_msg

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

                result = self.execute_tool(tool_name, kwargs, model_tool_call=True)

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
        if tool_name == "semantic_search_grc":
            for key in ("query", "scope"):
                value = content.get(key)
                if isinstance(value, str) and value:
                    compact[key] = value
            results_preview = GrcAgent._semantic_search_result_preview(content.get("results"))
            if results_preview:
                compact["results_preview"] = results_preview
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
                "block_count": active_session.get("block_count"),
                "connection_count": active_session.get("connection_count"),
                "variable_count": active_session.get("variable_count"),
                "variable_preview": active_session.get("variable_preview"),
                "block_preview": active_session.get("block_preview"),
                "connection_preview": active_session.get("connection_preview"),
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
        if tool_name == "semantic_search_grc":
            compact.pop("results", None)
            history_preview = self._semantic_search_result_preview(content.get("results"))
            if history_preview:
                compact["results_preview"] = history_preview

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
        if tool_name == "semantic_search_grc":
            lines.append(
                "next_step_note: semantic search is read-only candidate discovery; it cannot authorize apply_edit, save_graph, insertions, removals, or repairs."
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
        connections_hint = ""
        count_parts = []
        if isinstance(content.get("block_count"), int):
            count_parts.append(f"blocks={content.get('block_count')}")
        if isinstance(content.get("connection_count"), int):
            count_parts.append(f"connections={content.get('connection_count')}")
        if isinstance(content.get("variable_count"), int):
            count_parts.append(f"variables={content.get('variable_count')}")
        counts_hint = f" {', '.join(count_parts)};" if count_parts else ""
        if reason != "turn_refresh":
            variable_preview = content.get("variable_preview")
            if isinstance(variable_preview, list) and variable_preview:
                variables_hint = f" variables=[{', '.join(str(item) for item in variable_preview)}];"
            block_preview = content.get("block_preview")
            if isinstance(block_preview, list) and block_preview:
                blocks_hint = f" blocks=[{', '.join(str(item) for item in block_preview[:6])}];"
            connection_preview = content.get("connection_preview")
            if isinstance(connection_preview, list) and connection_preview:
                connections_hint = (
                    " connections_preview=["
                    f"{', '.join(str(item) for item in connection_preview[:8])}];"
                )
        return (
            f"{action}: path={content.get('path')}, "
            f"graph_id={content.get('graph_id')}, "
            f"state_revision={content.get('state_revision')}, "
            f"dirty={content.get('dirty')}, "
            f"validation={validation_status};"
            f"{counts_hint}{variables_hint}{blocks_hint}{connections_hint}"
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

    @staticmethod
    def _semantic_search_result_preview(
        results: Any,
        *,
        max_items: int = 3,
    ) -> list[dict[str, str]]:
        if not isinstance(results, list):
            return []
        preview: list[dict[str, str]] = []
        for item in results[:max_items]:
            if not isinstance(item, dict):
                continue
            compact: dict[str, str] = {}
            for key in ("canonical_block_id", "record_id", "source_type", "title"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    compact[key] = value
            score = item.get("vector_score_raw")
            if isinstance(score, int | float):
                compact["vector_score_raw"] = str(score)
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
            "search_manual": self._search_manual,
            "semantic_search_grc": self._semantic_search_grc,
            "suggest_compatible_insertions": self._suggest_compatible_insertions,
            "insert_block_on_connection": self._insert_block_on_connection,
            "auto_insert_block": self._auto_insert_block,
            "remove_connection": self._remove_connection,
            "rewire_connection": self._rewire_connection,
            "apply_edit": self._apply_edit,
            "propose_edit": self._propose_edit,
            "validate_graph": self._validate_graph,
            "save_graph": self._save_graph,
        }

    def _build_mvp_tool_registry(self) -> dict[str, ToolCallable]:
        """Return the simplified model-facing MVP tool surface."""
        return {
            "inspect_graph": self._inspect_graph,
            "search_blocks": self._search_blocks,
            "ask_grc_docs": self._ask_grc_docs,
            "change_graph": self._change_graph,
            "save_graph_explicit": self._save_graph_explicit,
            "load_graph_explicit": self._load_graph_explicit,
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
            and tool_name not in {"save_graph", "save_graph_explicit"}
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
            if result.get("ok") and not bool(result.get("dry_run")):
                self._record_accepted_checkpoint(tool_name, result, before)
            elif not result.get("ok"):
                self._record_failure_journal(tool_name, result, before)
            return
        if tool_name in {"save_graph", "save_graph_explicit"}:
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
                    if tool_name in {"save_graph", "save_graph_explicit"}
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

    def _inspect_graph(
        self,
        operation: str,
        target: str | None = None,
        max_items: int | None = None,
        debug: bool = False,
    ) -> ToolResult:
        return inspect_graph_wrapper(
            self,
            operation=operation,
            target=target,
            max_items=max_items,
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
        vector_token = vector_index_version_token()
        return f"catalog={catalog_token}|vector={vector_token}"

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
        while len(self._search_blocks_cache) > self._retrieval_cfg.conceptual_cache_size:
            self._search_blocks_cache.popitem(last=False)

    def _ask_grc_docs_cache_key(
        self,
        *,
        question: str,
        k: int,
        retrieval_mode: str,
        sources: list[dict[str, str]],
    ) -> tuple[str, int, str, str, str, str, str]:
        source_digest = hashlib.sha1()
        for row in sources:
            title = str(row.get("title", "")).strip()
            source = str(row.get("source", "")).strip()
            excerpt = str(row.get("excerpt", "")).strip()
            source_digest.update(f"{title}|{source}|{excerpt}".encode("utf-8"))
        return (
            question,
            k,
            retrieval_mode,
            source_digest.hexdigest(),
            _manual_corpus_version_token(),
            self._docs_answer_cfg.helper_prompt_version,
            self._docs_answer_cfg.helper_mode,
        )

    def _ask_grc_docs_cache_get(
        self,
        key: tuple[str, int, str, str, str, str, str],
    ) -> dict[str, Any] | None:
        hit = self._ask_grc_docs_cache.get(key)
        if hit is None:
            return None
        self._ask_grc_docs_cache.move_to_end(key)
        return copy.deepcopy(hit)

    def _ask_grc_docs_cache_put(
        self,
        key: tuple[str, int, str, str, str, str, str],
        payload: dict[str, Any],
    ) -> None:
        self._ask_grc_docs_cache[key] = copy.deepcopy(payload)
        self._ask_grc_docs_cache.move_to_end(key)
        while len(self._ask_grc_docs_cache) > self._docs_answer_cfg.answer_cache_size:
            self._ask_grc_docs_cache.popitem(last=False)

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

    def _collect_docs_candidates(
        self,
        *,
        lexical_payload: dict[str, Any],
        semantic_manual: dict[str, Any],
        semantic_tutorial: dict[str, Any],
    ) -> list[_DocsEvidenceCandidate]:
        return collect_docs_candidates_wrapper(
            self,
            lexical_payload=lexical_payload,
            semantic_manual=semantic_manual,
            semantic_tutorial=semantic_tutorial,
        )

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
        clipped: list[DocsAnswerSnippet] = []
        total_chars = 0
        for snippet in snippets:
            excerpt_text = snippet.excerpt
            if len(excerpt_text) > self._docs_answer_cfg.helper_max_snippet_chars:
                excerpt_text = (
                    excerpt_text[
                        : self._docs_answer_cfg.helper_max_snippet_chars - 1
                    ].rstrip()
                    + "…"
                )
            candidate = DocsAnswerSnippet(
                title=snippet.title,
                source=snippet.source,
                excerpt=excerpt_text,
            )
            chunk_chars = len(candidate.title) + len(candidate.source) + len(candidate.excerpt)
            if (
                clipped
                and total_chars + chunk_chars
                > self._docs_answer_cfg.helper_max_total_context_chars
            ):
                break
            clipped.append(candidate)
            total_chars += chunk_chars
        return clipped or snippets[:1]

    @staticmethod
    def _is_tutorial_or_howto_query(query: str) -> bool:
        lower = query.lower()
        markers = (
            "how to",
            "tutorial",
            "walkthrough",
            "step by step",
            "example",
            "guide",
        )
        if any(marker in lower for marker in markers):
            return True
        return bool(
            re.search(r"(?i)^\s*how\s+do\s+.+\s+work\??\s*$", query)
            or re.search(r"(?i)^\s*how\s+does\s+.+\s+work\??\s*$", query)
        )

    @staticmethod
    def _docs_topic_terms(query: str) -> list[str]:
        return [
            token
            for token in re.findall(r"[a-z0-9]+", query.lower())
            if len(token) > 2 and token not in _DOCS_QUERY_STOP_WORDS
        ]

    @staticmethod
    def _docs_primary_terms(query: str) -> list[str]:
        return [
            token
            for token in GrcAgent._docs_topic_terms(query)
            if token not in _DOCS_GENERIC_TOPIC_TERMS
        ]

    @staticmethod
    def _normalize_docs_source_key(source: str) -> str:
        text = " ".join(str(source or "").split()).strip().lower()
        if not text:
            return ""
        if "](" in text:
            text = text.split("](", 1)[0]
        if text.startswith("http"):
            text = text.split("#", 1)[0]
            text = text.split("&oldid=", 1)[0]
            text = text.rstrip("/&?")
        return text

    @staticmethod
    def _clean_docs_excerpt(excerpt: str) -> str:
        text = " ".join(str(excerpt or "").split()).strip()
        if not text:
            return ""
        segments = re.split(r"(?<=[.!?])\s+", text)
        kept: list[str] = []
        for segment in segments:
            lower = segment.lower()
            if any(marker in lower for marker in _DOCS_NAVIGATION_MARKERS):
                continue
            if _DOCS_LIST_MARKERS_RE.search(segment) and len(segment) < 70:
                continue
            kept.append(segment)
        cleaned = " ".join(kept).strip()
        return cleaned if cleaned else text

    @staticmethod
    def _docs_title_aliases(title: str) -> list[str]:
        compact = " ".join(str(title or "").split()).strip().lower()
        if not compact:
            return []
        aliases: set[str] = set()
        tokens = re.findall(r"[a-z0-9]+", compact)
        if tokens:
            aliases.add("_".join(tokens))
        match = re.match(r"^(?P<prefix>[a-z0-9]+)\s+(?P<lhs>.+?)\s+and\s+(?P<rhs>.+)$", compact)
        if match:
            prefix = match.group("prefix")
            lhs = "_".join(re.findall(r"[a-z0-9]+", match.group("lhs")))
            rhs = "_".join(re.findall(r"[a-z0-9]+", match.group("rhs")))
            if prefix and lhs and rhs:
                aliases.add(f"{prefix}_{lhs}_and_{rhs}")
                aliases.add(f"{prefix}_{rhs}_and_{lhs}")
        return sorted(alias for alias in aliases if alias)

    def _infer_docs_source_type(
        self,
        *,
        source: str,
        title: str,
        source_type_hint: str | None = None,
    ) -> str:
        hint = (source_type_hint or "").strip().lower()
        if hint == "tutorial_chunk":
            return "tutorial"
        if hint == "manual_chunk":
            return "manual"
        source_l = source.lower()
        title_l = title.lower()
        if "catalog:" in source_l:
            return "catalog"
        if "tutorial" in source_l or "guided_tutorial" in source_l:
            return "tutorial"
        if any(marker in title_l for marker in _DOCS_MENU_TITLE_MARKERS):
            return "tutorial"
        return "manual"

    def _docs_low_value_reasons(self, *, candidate: _DocsEvidenceCandidate) -> list[str]:
        reasons: list[str] = []
        title_l = candidate.snippet.title.lower()
        excerpt_l = candidate.snippet.excerpt.lower()
        section_l = candidate.section.lower()
        if title_l.strip() == "what is gnu radio":
            reasons.append("generic_gnuradio_page")
        if any(marker in title_l for marker in _DOCS_MENU_TITLE_MARKERS):
            reasons.append("menu_index_page")
        if "porting guide" in title_l:
            reasons.append("porting_guide_fragment")
        if title_l.startswith("simulation example"):
            reasons.append("simulation_walkthrough")
        if any(marker in excerpt_l for marker in _DOCS_NAVIGATION_MARKERS):
            reasons.append("navigation_boilerplate")
        if excerpt_l.count(" 1. ") + excerpt_l.count(" 2. ") + excerpt_l.count(" 3. ") >= 3:
            reasons.append("toc_dominated")
        numbered_links = len(_DOCS_LIST_MARKERS_RE.findall(excerpt_l))
        prose_chars = len(re.findall(r"[a-z]", excerpt_l))
        if numbered_links >= 6 and prose_chars < 260:
            reasons.append("link_list_fragment")
        if len(candidate.snippet.excerpt.strip()) < 70:
            reasons.append("very_short_fragment")
        if candidate.snippet.excerpt.rstrip().endswith("…") and len(candidate.snippet.excerpt) < 220:
            reasons.append("snippet_fragment")
        if section_l in {"", candidate.snippet.title.lower()} and "tutorials" in excerpt_l[:180]:
            reasons.append("generic_context_only")
        return reasons

    @staticmethod
    def _is_procedural_walkthrough_text(text: str) -> bool:
        lower = text.lower()
        return any(marker in lower for marker in _DOCS_PROCEDURAL_MARKERS)

    @staticmethod
    def _is_block_definition_query(question: str) -> bool:
        q = question.lower().strip()
        if not (
            re.search(r"\bwhat does .+ do\??$", q)
            or re.search(r"\bwhat is (?:an? |the )?.+ block used for(?: in .+)?\??$", q)
            or re.search(r"\bwhat does .+ block do\??$", q)
            or re.search(r"\bhow do .+ blocks? (?:relate to|interact with) .+\??$", q)
            or re.search(r"\bhow does .+ block (?:relate to|interact with) .+\??$", q)
        ):
            return False
        if "interact with" in q:
            return False
        if "relate to" in q and "pmt" not in q:
            return False
        subject = GrcAgent._extract_block_definition_subject(question) or ""
        subject_l = subject.lower()
        if subject_l in {"grcc", "gnu radio"}:
            return False
        if "grcc" in subject_l:
            return False
        markers = (
            "block",
            "sink",
            "source",
            "strobe",
            "throttle",
            "head",
            "debug",
            "tagged stream",
            "null sink",
            "qt gui",
            "embedded python",
        )
        return any(marker in subject_l for marker in markers) or bool(
            re.fullmatch(r"[a-z0-9_]+", subject_l)
        )

    @staticmethod
    def _extract_block_definition_subject(question: str) -> str | None:
        q = " ".join(question.split()).strip()
        patterns = (
            r"(?i)\bwhat does (?:the )?(?P<subject>.+?) block do\??$",
            r"(?i)\bwhat is (?:an? |the )?(?P<subject>.+?) block(?: used for)?(?: in .+)?\??$",
            r"(?i)\bwhat does (?:the )?(?P<subject>.+?) do\??$",
            r"(?i)\bhow do (?:the )?(?P<subject>.+?) blocks? (?:relate to|interact with) .+\??$",
            r"(?i)\bhow does (?:the )?(?P<subject>.+?) block (?:relate to|interact with) .+\??$",
        )
        for pattern in patterns:
            match = re.search(pattern, q)
            if not match:
                continue
            subject = " ".join(str(match.group("subject") or "").split()).strip(" ?.,")
            if subject:
                return subject
        return None

    @staticmethod
    def _extract_docs_subject(question: str) -> str | None:
        q = " ".join(question.split()).strip()
        patterns = (
            r"(?i)\bwhat is (?:an? |the )?(?P<subject>.+?)\??$",
            r"(?i)\bwhat does (?:the )?(?P<subject>.+?) do\??$",
            r"(?i)\bhow do (?P<subject>.+?) work\??$",
            r"(?i)\bhow does (?P<subject>.+?) work\??$",
            r"(?i)\bhow do (?P<subject>.+?) relate to .+?\??$",
            r"(?i)\bhow does (?P<subject>.+?) relate to .+?\??$",
            r"(?i)\bhow do (?P<subject>.+?) interact\??$",
            r"(?i)\bhow do (?P<subject>.+?) affect .+?\??$",
            r"(?i)\bhow does (?P<subject>.+?) affect .+?\??$",
        )
        for pattern in patterns:
            match = re.search(pattern, q)
            if not match:
                continue
            subject = " ".join(str(match.group("subject") or "").split()).strip(" ?.,")
            if subject:
                return subject
        return None

    def _build_catalog_assisted_candidate(
        self,
        *,
        question: str,
    ) -> _DocsEvidenceCandidate | None:
        try:
            result = self._search_blocks(question, k=3, debug=False, enrich=True)
        except Exception:
            return None
        if result.get("ok") is not True:
            return None
        rows = result.get("results")
        if not isinstance(rows, list) or not rows:
            return None
        subject = self._extract_block_definition_subject(question) or question
        subject_terms = self._docs_primary_terms(subject) or self._docs_topic_terms(subject)
        scored_rows: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = " ".join(
                [
                    str(row.get("name") or ""),
                    str(row.get("block_id") or ""),
                    str(row.get("summary") or ""),
                ]
            ).lower()
            score = sum(1 for term in subject_terms if term and term in text)
            scored_rows.append((score, row))
        if not scored_rows:
            return None
        scored_rows.sort(key=lambda item: item[0], reverse=True)
        if scored_rows[0][0] <= 0:
            return None
        top = scored_rows[0][1]
        if not isinstance(top, dict):
            return None
        block_id = str(top.get("block_id") or "").strip()
        name = str(top.get("name") or block_id).strip()
        summary = str(top.get("summary") or "").strip()
        if not name or not summary:
            return None
        alias = "_".join(re.findall(r"[a-z0-9]+", name.lower()))
        source_ref = "catalog"
        if block_id and alias:
            source_ref = f"catalog:{alias}:{alias}_blocks:{block_id}"
        elif block_id:
            source_ref = f"catalog:{block_id}"
        elif alias:
            source_ref = f"catalog:{alias}"
        return _DocsEvidenceCandidate(
            snippet=DocsAnswerSnippet(
                title=name,
                source=source_ref,
                excerpt=summary,
            ),
            source_channel="catalog_assist",
            source_type="catalog",
            section=name,
            lexical_score=18.0,
            semantic_score=None,
            topic_score=0.0,
            quality_score=0.0,
            low_value_reasons=(),
            procedural=False,
        )

    def _should_catalog_assist(
        self,
        question: str,
        ranked_candidates: list[_DocsEvidenceCandidate],
    ) -> bool:
        lower = question.lower()
        if not any(
            marker in lower
            for marker in (
                "block",
                "sink",
                "source",
                "strobe",
                "throttle",
                "tagged stream",
                "qt gui",
                "message debug",
                "null sink",
            )
        ):
            return False
        if not ranked_candidates:
            return True
        subject = self._extract_docs_subject(question) or question
        subject_terms = self._docs_primary_terms(subject)
        if not subject_terms:
            return False
        top = ranked_candidates[0]
        top_title_source = " ".join([top.snippet.title, top.snippet.source]).lower()
        subject_hits = sum(1 for term in subject_terms if term in top_title_source)
        required_hits = 1 if len(subject_terms) <= 1 else 2
        return subject_hits < required_hits

    @staticmethod
    def _is_docs_evidence_strong(
        ranked_candidates: list[_DocsEvidenceCandidate],
        *,
        question: str,
    ) -> bool:
        if not ranked_candidates:
            return False
        top = ranked_candidates[0]
        severe = {
            "generic_gnuradio_page",
            "menu_index_page",
            "navigation_boilerplate",
            "toc_dominated",
        }
        if any(reason in severe for reason in top.low_value_reasons):
            return False
        primary_terms = GrcAgent._docs_primary_terms(question)
        top_text = " ".join(
            [top.snippet.title, top.section, top.snippet.excerpt]
        ).lower()
        primary_hits = sum(1 for term in primary_terms if term in top_text)
        if primary_terms and primary_hits == 0:
            return False
        return top.quality_score >= 4.5 and top.topic_score >= 2.0

    @staticmethod
    def _classify_docs_answer_type(question: str) -> str:
        lower = question.lower()
        if any(
            marker in lower
            for marker in (
                "abi guarantee",
                "abi guarantees",
                "zero-copy",
                "lock-free",
                "fpga bitstream",
                "auto-repair",
                "deterministically",
            )
        ):
            return "insufficient"
        if "hierarchical block" in lower:
            return "definition"
        if GrcAgent._is_block_definition_query(question):
            return "block_definition"
        if (
            "block" in lower
            and "relate to" in lower
            and "pmt" in lower
            and ("difference between" not in lower and "differ" not in lower and " versus " not in lower)
        ):
            return "block_definition"
        if any(marker in lower for marker in ("difference between", "differ", "vs", "versus")):
            return "comparison"
        if "interact with" in lower or "relate to" in lower:
            return "comparison"
        if "grcc" in lower:
            return "tool_command_concept"
        if GrcAgent._is_tutorial_or_howto_query(question):
            return "procedural_how_to"
        if lower.startswith(("what is ", "what are ", "explain ")):
            return "definition"
        return "definition"

    @staticmethod
    def _normalized_docs_retrieval_query(*, question: str, answer_type: str) -> str:
        query = " ".join(question.split()).strip()
        if answer_type == "tool_command_concept" and "grcc" in query.lower():
            return "grcc compile validation flowgraph"
        return query

    @staticmethod
    def _text_matches_term_or_synonym(text: str, term: str) -> bool:
        if not term:
            return False
        if term in text:
            return True
        for synonym in _DOCS_TOPIC_SYNONYMS.get(term, ()):
            if synonym in text:
                return True
        return False

    def _select_docs_candidates_for_answer_type(
        self,
        *,
        question: str,
        answer_type: str,
        ranked_candidates: list[_DocsEvidenceCandidate],
        limit: int,
    ) -> list[_DocsEvidenceCandidate]:
        if not ranked_candidates:
            return []
        if answer_type != "comparison":
            return ranked_candidates[:limit]
        sides = self._extract_comparison_sides(question)
        if sides is None:
            return ranked_candidates[:limit]
        left_candidate: _DocsEvidenceCandidate | None = None
        right_candidate: _DocsEvidenceCandidate | None = None
        for candidate in ranked_candidates:
            text = " ".join(
                [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
            ).lower()
            if left_candidate is None and any(
                self._text_matches_term_or_synonym(text, term) for term in sides.left_terms
            ):
                left_candidate = candidate
            if right_candidate is None and any(
                self._text_matches_term_or_synonym(text, term) for term in sides.right_terms
            ):
                right_candidate = candidate
            if left_candidate is not None and right_candidate is not None:
                break
        selected: list[_DocsEvidenceCandidate] = []
        if left_candidate is not None:
            selected.append(left_candidate)
        if right_candidate is not None and right_candidate is not left_candidate:
            selected.append(right_candidate)
        for candidate in ranked_candidates:
            if len(selected) >= limit:
                break
            if candidate in selected:
                continue
            selected.append(candidate)
        return selected[:limit]

    @staticmethod
    def _extract_comparison_sides(question: str) -> _DocsComparisonSides | None:
        q = " ".join(question.split()).strip()
        patterns = (
            r"(?i)\bdifference between (?P<left>.+?) and (?P<right>.+?)\??$",
            r"(?i)\bhow does (?P<left>.+?) differ from (?P<right>.+?)\??$",
            r"(?i)\bhow do (?P<left>.+?) differ from (?P<right>.+?)\??$",
            r"(?i)\bhow are (?P<left>.+?) different from (?P<right>.+?)\??$",
            r"(?i)\bhow is (?P<left>.+?) different from (?P<right>.+?)\??$",
            r"(?i)\b(?P<left>.+?) vs\.? (?P<right>.+?)\??$",
            r"(?i)\b(?P<left>.+?) versus (?P<right>.+?)\??$",
            r"(?i)\bhow do (?P<left>.+?) interact with (?P<right>.+?)\??$",
            r"(?i)\bhow does (?P<left>.+?) interact with (?P<right>.+?)\??$",
            r"(?i)\bhow do (?P<left>.+?) relate to (?P<right>.+?)\??$",
            r"(?i)\bhow does (?P<left>.+?) relate to (?P<right>.+?)\??$",
        )
        for pattern in patterns:
            match = re.search(pattern, q)
            if not match:
                continue
            left = " ".join(str(match.group("left") or "").split()).strip(" ?.,")
            right = " ".join(str(match.group("right") or "").split()).strip(" ?.,")
            left = re.sub(r"(?i)\b(keep it short|briefly|please cite source)\b.*$", "", left).strip(" ?.,")
            right = re.sub(r"(?i)\b(keep it short|briefly|please cite source)\b.*$", "", right).strip(" ?.,")
            if not left or not right:
                continue
            left_terms = tuple(GrcAgent._docs_topic_terms(left))
            right_terms = tuple(GrcAgent._docs_topic_terms(right))
            if not left_terms or not right_terms:
                continue
            return _DocsComparisonSides(
                left_label=left,
                right_label=right,
                left_terms=left_terms,
                right_terms=right_terms,
            )
        return None

    @staticmethod
    def _sentence_list(text: str) -> list[str]:
        return [
            " ".join(sentence.split()).strip()
            for sentence in re.split(r"(?<=[.!?])\s+", text)
            if " ".join(sentence.split()).strip()
        ]

    def _pick_typed_sentence(
        self,
        *,
        candidate: _DocsEvidenceCandidate,
        required_terms: tuple[str, ...],
        allow_procedural: bool,
        min_term_hits: int = 1,
    ) -> str:
        best = ""
        best_score = -999.0
        for sentence in self._sentence_list(candidate.snippet.excerpt):
            lower = sentence.lower()
            if len(sentence) < 24 or len(sentence) > 220:
                continue
            if sentence.count("#") >= 1 or "```" in sentence:
                continue
            if sentence.count("`") >= 2 or "self.connect(" in lower:
                continue
            if "::" in sentence:
                continue
            if ".py" in lower or "shown here" in lower:
                continue
            if "following functions" in lower:
                continue
            if "project" in lower and "can be found in" in lower:
                continue
            if " * " in sentence:
                continue
            if "function for this purpose" in lower and "connect" in lower:
                continue
            if any(marker in lower for marker in _DOCS_NAVIGATION_MARKERS):
                continue
            if re.search(r"\b\d+\.\s+\w+", sentence) and sentence.count(".") >= 3:
                continue
            if "for the purposes of this tutorial" in lower:
                continue
            if re.search(r"\b\d+\.$", sentence):
                continue
            if any(
                marker in lower
                for marker in (
                    "creating and modifying python blocks",
                    "flowgraph fundamentals",
                    "beginner tutorials",
                )
            ):
                continue
            if sentence.endswith("…") and len(sentence) < 120:
                continue
            if any(marker in lower for marker in ("input port(s)", "output port(s)", "parameter(s)")):
                continue
            if not allow_procedural and self._is_procedural_walkthrough_text(lower):
                continue
            term_hits = sum(1 for term in required_terms if term in lower)
            if required_terms and term_hits < max(1, min_term_hits):
                continue
            synonym_hits = 0
            for term in required_terms:
                for synonym in _DOCS_TOPIC_SYNONYMS.get(term, ()):
                    if synonym in lower:
                        synonym_hits += 1
            score = float(term_hits) + min(2.0, float(synonym_hits) * 0.5)
            if re.search(r"\b(is|are|means|refers to|used for|allows|converts|provides|carries)\b", lower):
                score += 1.5
            if any(marker in lower for marker in ("asynchronous", "between blocks", "control data", "time domain", "frequency domain")):
                score += 1.6
            if "pmt symbol" in lower and "asynchronous" not in lower:
                score -= 0.8
            if "the lower part is a modified version" in lower:
                score -= 2.0
            if score > best_score:
                best_score = score
                best = sentence
        return best

    @staticmethod
    def _minimum_required_term_hits(required_terms: tuple[str, ...]) -> int:
        if len(required_terms) >= 4:
            return 2
        if len(required_terms) >= 2:
            return 1
        return 1

    def _required_terms_for_answer_type(
        self,
        *,
        question: str,
        answer_type: str,
    ) -> tuple[str, ...]:
        if answer_type == "comparison":
            sides = self._extract_comparison_sides(question)
            if sides is None:
                return tuple(self._docs_primary_terms(question) or self._docs_topic_terms(question))
            terms = {
                *sides.left_terms,
                *sides.right_terms,
            }
            return tuple(sorted(term for term in terms if term))
        if answer_type == "tool_command_concept":
            return ("grcc", "validation", "compile")
        subject = self._extract_docs_subject(question) or question
        return tuple(self._docs_primary_terms(subject) or self._docs_topic_terms(subject))

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
        quality = str(source_quality.get("quality") or "weak")
        question_lower = question.lower()
        if "hierarchical block" in question_lower:
            return (False, "special_case_hier_block")
        if "embedded python block" in question_lower:
            return (False, "special_case_embedded_python")
        if answer_type == "insufficient":
            return (False, "unsupported_question")
        if quality == "weak":
            return (False, "weak_evidence")
        if not bool(source_quality.get("supports_answer_type")):
            return (False, "answer_type_not_supported")
        if answer_type == "comparison":
            if quality != "strong":
                return (False, "comparison_requires_strong_evidence")
            if typed_insufficient:
                return (False, "comparison_deterministic_insufficient")
            if len(selected_candidates) < 2:
                return (False, "comparison_missing_side_evidence")
            if "difference:" in typed_answer.lower() and typed_answer.count(":") >= 3:
                return (False, "high_confidence_simple_comparison")
            return (True, "eligible_comparison_synthesis")
        if answer_type == "definition":
            if typed_insufficient and len(selected_candidates) >= 2:
                return (True, "eligible_definition_recovery")
            if len(selected_candidates) < 2:
                return (False, "single_source_definition")
            return (False, "high_confidence_simple_definition")
        if answer_type == "procedural_how_to":
            has_tutorial = any(
                candidate.source_type == "tutorial" for candidate in selected_candidates
            )
            if quality == "strong" and has_tutorial and typed_insufficient:
                return (True, "eligible_procedural_recovery")
            if quality == "strong" and has_tutorial and len(typed_answer) > 180:
                return (True, "eligible_procedural_synthesis")
            return (False, "procedural_deterministic_sufficient")
        if answer_type == "tool_command_concept":
            if typed_insufficient:
                return (False, "tool_command_missing_evidence")
            return (False, "tool_command_deterministic_sufficient")
        if answer_type == "block_definition":
            lower = typed_answer.lower()
            if lower.startswith("according to the local block catalog"):
                return (False, "concise_catalog_answer")
            return (False, "block_definition_deterministic_only")
        return (False, "unsupported_answer_type")

    def _helper_candidates_for_docs_answer(
        self,
        *,
        question: str,
        answer_type: str,
        ranked_candidates: list[_DocsEvidenceCandidate],
    ) -> list[_DocsEvidenceCandidate]:
        severe = {
            "generic_gnuradio_page",
            "menu_index_page",
            "navigation_boilerplate",
            "toc_dominated",
        }
        base_candidates = [
            candidate
            for candidate in ranked_candidates
            if not any(reason in severe for reason in candidate.low_value_reasons)
        ]
        if answer_type == "comparison":
            selected = self._select_docs_candidates_for_answer_type(
                question=question,
                answer_type=answer_type,
                ranked_candidates=(base_candidates or ranked_candidates),
                limit=3,
            )
            return selected[:3]
        if answer_type == "procedural_how_to":
            helper_candidates = [candidate for candidate in base_candidates if candidate.source_type == "tutorial"]
            return helper_candidates[:3] or (base_candidates[:3] or ranked_candidates[:2])
        helper_candidates = base_candidates[:3]
        return helper_candidates or ranked_candidates[:2]

    @staticmethod
    def _clean_catalog_summary_for_answer(name: str, summary: str) -> str:
        text = " ".join(summary.split()).strip()
        if not text:
            return ""
        text = text.replace("…", "")
        text = re.sub(rf"(?i)^{re.escape(name)}\s+", "", text).strip()
        text = re.sub(r"\([a-z0-9_]+\)", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\b[a-z]+_[a-z0-9_]+\b", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(
            r"\bwith\s+\d+\s+input\s+por.*$",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip(" .,:;")
        text = re.sub(r"\([a-z0-9_.-]*$", "", text, flags=re.IGNORECASE).strip(" .,:;")
        text = re.sub(r"\bblocks?\b\.?$", "", text, flags=re.IGNORECASE).strip(" .,:;")
        text = re.sub(r"\bparameters:\s*.+$", "", text, flags=re.IGNORECASE).strip(" .,:;")
        text = re.sub(r"\binputs?:\s*.+$", "", text, flags=re.IGNORECASE).strip(" .,:;")
        text = re.sub(r"\boutputs?:\s*.+$", "", text, flags=re.IGNORECASE).strip(" .,:;")
        text = re.sub(r"\bwith\s+\d+\.?$", "", text, flags=re.IGNORECASE).strip(" .,:;")
        text = re.sub(r"\bsink to nowhere\b", "discards input samples", text, flags=re.IGNORECASE)
        text = re.sub(r"\bdrop output samples\b", "discard input samples", text, flags=re.IGNORECASE)
        text = re.sub(r"\bdiscard stream data\b", "discard input samples", text, flags=re.IGNORECASE)
        text = re.sub(r"\s{2,}", " ", text).strip(" .,:;")
        text = re.sub(r"\s+", " ", text).strip(" .,:;")
        name_tokens = re.findall(r"[a-z0-9]+", name.lower())
        lowered = text.lower()
        for token in name_tokens:
            lowered = re.sub(rf"\b{re.escape(token)}\b", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip(" .,:;")
        informative_tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", lowered)
            if len(token) > 2 and token not in {"block", "blocks", "stream", "data", "samples"}
        ]
        if len(informative_tokens) < 2:
            return ""
        if not re.search(
            r"\b(limit|throttle|pass|discard|consume|drop|debug|convert|send|receive|display|show|rate|message|sample)\b",
            lowered,
        ):
            return ""
        if len(text.split()) > 24:
            text = " ".join(text.split()[:24]).strip(" .,:;")
        return text

    @staticmethod
    def _catalog_block_purpose_sentence(name: str, summary: str) -> str:
        name_l = name.lower()
        summary_l = summary.lower()
        if not summary:
            if "stream to tagged stream" in name_l:
                return "converts a stream to a tagged stream by attaching length tags"
            if "message strobe" in name_l:
                return "emits PMT messages periodically"
            if "message debug" in name_l:
                return "is used to inspect and print message traffic for debugging"
            return ""
        if "throttle" in name_l and any(
            marker in summary_l for marker in ("rate", "sample", "limit", "throttle", "pace")
        ):
            if "hardware" in summary_l or "simulation" in summary_l:
                return (
                    "limits the rate of samples in a flowgraph, typically to prevent "
                    "simulations from running as fast as the CPU allows"
                )
            return "limits the rate of samples in a flowgraph"
        if "head" in name_l and any(
            marker in summary_l for marker in ("fixed", "number of samples", "limit", "stop")
        ):
            return "passes only a fixed number of samples, then stops forwarding data"
        if ("null" in name_l and "sink" in name_l) and any(
            marker in summary_l for marker in ("discard", "drop", "consume", "nowhere")
        ):
            return "consumes and discards input samples"
        return summary

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
        del evidence_strong
        answer_type = self._classify_docs_answer_type(question)
        return self._build_typed_docs_answer(
            question=question,
            ranked_candidates=ranked_candidates,
            answer_type=answer_type,
        )

    @staticmethod
    def _is_lexical_docs_evidence_strong(
        *,
        query: str,
        question: str,
        answer_type: str,
        lexical_payload: dict[str, Any],
        limit: int,
    ) -> bool:
        if lexical_payload.get("ok") is not True:
            return False
        results = lexical_payload.get("results")
        if not isinstance(results, list) or not results:
            return False
        top = results[0] if isinstance(results[0], dict) else {}
        top_title = str(top.get("title") or "").lower()
        top_excerpt = str(top.get("excerpt") or "").lower()
        top_source = ""
        citation = top.get("citation")
        if isinstance(citation, dict):
            top_source = str(citation.get("url") or citation.get("path") or "").lower()
        if any(marker in top_title for marker in _DOCS_MENU_TITLE_MARKERS):
            return False
        if top_title.strip() == "what is gnu radio":
            return False
        if any(marker in top_excerpt for marker in _DOCS_NAVIGATION_MARKERS):
            return False
        if answer_type == "tool_command_concept":
            top_text = " ".join([top_title, top_excerpt, top_source]).lower()
            has_grcc = "grcc" in top_text
            has_tool_context = ("compile" in top_text) or ("validation" in top_text)
            return has_grcc and has_tool_context
        if answer_type == "comparison":
            sides = GrcAgent._extract_comparison_sides(question)
            if sides is None:
                return False
            rows = [row for row in results if isinstance(row, dict)][: max(3, min(6, limit + 2))]
            left_match = False
            right_match = False
            for row in rows:
                citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
                row_source = str(citation.get("url") or citation.get("path") or "").lower()
                row_text = " ".join(
                    [
                        str(row.get("title") or "").lower(),
                        str(row.get("excerpt") or "").lower(),
                        row_source,
                    ]
                )
                if not left_match and any(
                    GrcAgent._text_matches_term_or_synonym(row_text, term) for term in sides.left_terms
                ):
                    left_match = True
                if not right_match and any(
                    GrcAgent._text_matches_term_or_synonym(row_text, term) for term in sides.right_terms
                ):
                    right_match = True
                if left_match and right_match:
                    break
            if not (left_match and right_match):
                return False
        if GrcAgent._is_procedural_walkthrough_text(top_excerpt) and not GrcAgent._is_tutorial_or_howto_query(query):
            query_tokens = GrcAgent._docs_primary_terms(query)
            title_hits = sum(1 for token in query_tokens if token and token in top_title)
            if title_hits == 0:
                return False
        top_score = top.get("score")
        score_value = float(top_score) if isinstance(top_score, int | float) else 0.0
        result_count = len([row for row in results if isinstance(row, dict)])
        if score_value >= 28.0:
            query_tokens = GrcAgent._docs_primary_terms(query)
            if query_tokens and not any(
                token in top_title or token in top_excerpt or token in top_source
                for token in query_tokens
            ):
                return False
            return True
        if score_value >= 20.0 and result_count >= min(2, max(1, limit)):
            query_tokens = GrcAgent._docs_primary_terms(query)
            if query_tokens and not any(
                token in top_title or token in top_excerpt or token in top_source
                for token in query_tokens
            ):
                return False
            return True
        query_tokens = GrcAgent._docs_primary_terms(query) or GrcAgent._docs_topic_terms(query)
        token_hits = sum(1 for token in query_tokens if token and token in top_excerpt)
        title_hits = sum(1 for token in query_tokens if token and token in top_title)
        if query_tokens and (token_hits + title_hits) == 0:
            return False
        if token_hits < max(1, min(3, len(query_tokens))):
            return False
        if score_value < 12.0 and token_hits < 2:
            return False
        return True

    @staticmethod
    def _classify_docs_advisor_error(message: str) -> str:
        lower = message.lower()
        if "timed out" in lower or "timeout" in lower:
            return "timeout"
        if "unsupported keys" in lower or "missing keys" in lower or "must be" in lower:
            return "schema_parse_failure"
        if "malformed json" in lower or "must be object" in lower:
            return "malformed_helper_output"
        if "context" in lower and "length" in lower:
            return "prompt_too_large"
        if "http 400" in lower or "http 404" in lower:
            if "model" in lower:
                return "config_issue"
            return "implementation_bug"
        if "transport failure" in lower:
            return "llama_server_unavailable"
        return "implementation_bug"

    def _run_docs_answer_advisor(
        self,
        *,
        question: str,
        answer_type: str,
        snippets: list[DocsAnswerSnippet],
        focus: str | None,
    ) -> dict[str, Any] | None:
        estimated_prompt_chars = (
            len(question)
            + (len(focus) if isinstance(focus, str) else 0)
            + sum(
                len(snippet.title) + len(snippet.source) + len(snippet.excerpt)
                for snippet in snippets
            )
        )
        self._last_docs_advisor_meta = {
            "advisor_attempted": False,
            "advisor_success": False,
            "fallback_reason": "not_attempted",
            "helper_latency_ms": None,
            "prompt_chars": 0,
            "snippet_count": len(snippets),
            "schema_valid": False,
            "timeout_ms": int(self._docs_answer_cfg.helper_timeout_seconds * 1000),
            "cache_hit": False,
            "helper_finish_reason": None,
        }
        if not self._llama_server_url.strip() or not self._llama_model.strip():
            self._last_docs_advisor_meta["fallback_reason"] = "helper_disabled"
            self._last_docs_advisor_meta["prompt_chars"] = estimated_prompt_chars
            return None
        if not snippets:
            self._last_docs_advisor_meta["fallback_reason"] = "retrieval_empty"
            self._last_docs_advisor_meta["prompt_chars"] = estimated_prompt_chars
            return None
        now = time.monotonic()
        if now >= self._docs_advisor_probe_at:
            self._docs_advisor_reachable = self._probe_docs_advisor_server()
            self._docs_advisor_probe_at = now + (
                self._docs_answer_cfg.retry_interval_on_success_seconds
                if self._docs_advisor_reachable
                else self._docs_answer_cfg.retry_interval_on_failure_seconds
            )
        if not self._docs_advisor_reachable:
            self._last_docs_advisor_meta["fallback_reason"] = "llama_server_unavailable"
            self._last_docs_advisor_meta["prompt_chars"] = estimated_prompt_chars
            return None
        self._last_docs_advisor_meta["advisor_attempted"] = True
        started = time.perf_counter()
        try:
            client = DocsAnswerLlamaClient(
                base_url=self._llama_server_url,
                timeout_seconds=min(
                    self._llama_request_timeout_seconds,
                    self._docs_answer_cfg.helper_timeout_seconds,
                ),
                max_tokens=self._docs_answer_cfg.helper_max_output_tokens,
                temperature=0.0,
            )
            result = run_docs_answer_advisor(
                client=client,
                model=self._llama_model,
                question=question,
                answer_type=answer_type,
                snippets=snippets,
                focus=focus,
                max_answer_chars=self._docs_answer_cfg.answer_target_chars,
                max_excerpt_chars=self._docs_answer_cfg.excerpt_target_chars,
                max_sources=self._docs_answer_cfg.max_sources,
            )
            self._last_docs_advisor_meta.update(
                {
                    "advisor_success": True,
                    "fallback_reason": "none",
                    "helper_latency_ms": int(result.get("advisor_latency_ms") or 0),
                    "prompt_chars": int(result.get("prompt_chars") or 0),
                    "snippet_count": int(result.get("snippet_count") or len(snippets)),
                    "schema_valid": bool(result.get("schema_valid")),
                    "timeout_ms": int(
                        result.get("timeout_ms")
                        or int(self._docs_answer_cfg.helper_timeout_seconds * 1000)
                    ),
                    "cache_hit": False,
                    "helper_finish_reason": result.get("helper_finish_reason"),
                }
            )
            return result
        except Exception as exc:
            logger.info("docs_answer_advisor_failed error=%s", exc)
            self._last_docs_advisor_meta.update(
                {
                    "advisor_success": False,
                    "fallback_reason": self._classify_docs_advisor_error(str(exc)),
                    "helper_latency_ms": int((time.perf_counter() - started) * 1000),
                    "prompt_chars": estimated_prompt_chars,
                    "helper_finish_reason": "error",
                }
            )
            return None

    def _probe_docs_advisor_server(self) -> bool:
        """Cheap connectivity probe to avoid repeated long helper timeouts."""
        try:
            parsed = urlsplit(self._llama_server_url)
            host = parsed.hostname
            port = parsed.port
            if not host or not port:
                return False
            with socket.create_connection(
                (host, int(port)),
                timeout=self._docs_answer_cfg.probe_timeout_seconds,
            ):
                return True
        except Exception:
            return False

    def _validate_change_graph_operation_args(
        self,
        *,
        dry_run: bool,
        operation_kind: str | None,
        target_ref: dict[str, Any] | None,
        block_id: str | None,
        candidate_id: str | None,
        instance_name: str | None,
        connection_id: str | None,
        src_block: str | None,
        src_port: int | str | None,
        dst_block: str | None,
        dst_port: int | str | None,
        new_src_block: str | None,
        new_src_port: int | str | None,
        new_dst_block: str | None,
        new_dst_port: int | str | None,
        insert_params: dict[str, Any] | None,
        detach_connections: bool | None,
        detach_connection_ids: list[str] | None,
        param_key: str | None,
        param_value: Any,
        state: str | None,
        variable_name: str | None,
        variable_value: Any,
    ) -> ToolResult | None:
        return validate_change_graph_operation_args(
            self,
            dry_run=dry_run,
            operation_kind=operation_kind,
            target_ref=target_ref,
            block_id=block_id,
            candidate_id=candidate_id,
            instance_name=instance_name,
            connection_id=connection_id,
            src_block=src_block,
            src_port=src_port,
            dst_block=dst_block,
            dst_port=dst_port,
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
            insert_params=insert_params,
            detach_connections=detach_connections,
            detach_connection_ids=detach_connection_ids,
            param_key=param_key,
            param_value=param_value,
            state=state,
            variable_name=variable_name,
            variable_value=variable_value,
        )

    def _canonicalize_change_graph_target_ref(
        self,
        *,
        dry_run: bool,
        operation_kind: str | None,
        target_ref: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, ToolResult | None]:
        return canonicalize_change_graph_target_ref(
            self,
            dry_run=dry_run,
            operation_kind=operation_kind,
            target_ref=target_ref,
        )

    def _change_graph(
        self,
        dry_run: bool,
        user_goal: str,
        operation_kind: str | None = None,
        target_ref: dict[str, Any] | None = None,
        block_id: str | None = None,
        candidate_id: str | None = None,
        insert_block: str | None = None,
        instance_name: str | None = None,
        connection_id: str | None = None,
        src_block: str | None = None,
        src_port: int | str | None = None,
        dst_block: str | None = None,
        dst_port: int | str | None = None,
        state_revision: int | None = None,
        new_src_block: str | None = None,
        new_src_port: int | str | None = None,
        new_dst_block: str | None = None,
        new_dst_port: int | str | None = None,
        insert_params: dict[str, Any] | None = None,
        detach_connections: bool | None = None,
        detach_connection_ids: list[str] | None = None,
        param_key: str | None = None,
        param_value: Any = None,
        state: str | None = None,
        variable_name: str | None = None,
        variable_value: Any = None,
        debug: bool = False,
    ) -> ToolResult:
        return dispatch_change_graph(
            self,
            dry_run=dry_run,
            user_goal=user_goal,
            operation_kind=operation_kind,
            target_ref=target_ref,
            block_id=block_id,
            candidate_id=candidate_id,
            insert_block=insert_block,
            instance_name=instance_name,
            connection_id=connection_id,
            src_block=src_block,
            src_port=src_port,
            dst_block=dst_block,
            dst_port=dst_port,
            state_revision=state_revision,
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
            insert_params=insert_params,
            detach_connections=detach_connections,
            detach_connection_ids=detach_connection_ids,
            param_key=param_key,
            param_value=param_value,
            state=state,
            variable_name=variable_name,
            variable_value=variable_value,
            debug=debug,
        )

    @staticmethod
    def _compact_inspect_payload(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": bool(payload.get("ok")),
            "operation": operation,
            "message": payload.get("message"),
        }
        for key in (
            "summary",
            "valid",
            "errors",
            "target",
            "nodes",
            "warnings",
            "block_count",
            "connection_count",
            "variable_count",
            "dirty",
        ):
            if key in payload:
                out[key] = payload.get(key)
        return out

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
            "dry_run": bool(result.get("dry_run")),
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
        self._replace_session(loaded, reason="load_grc")
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

    def _get_grc_context(
        self,
        node_id: str,
        hops: int = 1,
        max_nodes: int | None = None,
    ) -> ToolResult:
        resolved_node_id = self._resolve_symbol_like_name(node_id) or node_id
        resolved_max_nodes = (
            self._guardrails_cfg.max_context_nodes
            if max_nodes is None
            else max_nodes
        )
        payload = get_grc_context(
            self.session,
            resolved_node_id,
            hops=hops,
            max_nodes=resolved_max_nodes,
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

    def _search_manual(self, query: str, k: int | None = None) -> ToolResult:
        if k is None:
            payload = search_manual(query)
        else:
            payload = search_manual(query, k=k)
        if payload.get("ok"):
            payload["hint"] = (
                "Manual results are explanation-only. Do not use them as transaction "
                "recipes; use catalog/session tools plus grcc validation for graph changes."
            )
        return self._payload_result("search_manual", payload)

    def _semantic_search_grc(
        self,
        query: str,
        scope: str = "all",
        k: int | None = None,
    ) -> ToolResult:
        if k is None:
            payload = semantic_search_grc(query, scope=scope)
        else:
            payload = semantic_search_grc(query, scope=scope, k=k)
        if payload.get("ok"):
            payload["hint"] = (
                "Semantic search results are read-only candidates. They cannot authorize graph edits, "
                "saves, insertions, removals, repairs, params payloads, or transaction payloads."
            )
        return self._payload_result("semantic_search_grc", payload)

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

        endpoint_args = {
            "src_block": src_block,
            "src_port": src_port,
            "dst_block": dst_block,
            "dst_port": dst_port,
        }
        has_endpoint_hint = any(value is not None for value in endpoint_args.values())
        if has_endpoint_hint:
            resolved = self.session.find_connection_candidates(
                src_block=src_block,
                src_port=src_port,
                dst_block=dst_block,
                dst_port=dst_port,
            )
            candidates = resolved["candidates"]
            if not candidates:
                return self._tool_result(
                    tool_name="remove_connection",
                    ok=False,
                    message="No existing connection matches the provided endpoint fields.",
                    error_type="connection_not_found",
                    state_revision=self.session.state_revision,
                )
            if len(candidates) > 1:
                payload = self._connection_clarification_payload(candidates)
                self._store_pending_clarification(payload)
                return self._payload_result("remove_connection", payload)

            resolved_connection_id = candidates[0]["connection_id"]
            if connection_id is not None and connection_id != resolved_connection_id:
                return self._tool_result(
                    tool_name="remove_connection",
                    ok=False,
                    message=(
                        "connection_id does not match the provided endpoint fields: "
                        f"{connection_id}"
                    ),
                    error_type="connection_endpoint_mismatch",
                    state_revision=self.session.state_revision,
                )
            connection_id = resolved_connection_id

        if not isinstance(connection_id, str) or not connection_id.strip():
            return self._tool_result(
                tool_name="remove_connection",
                ok=False,
                message=(
                    "remove_connection requires either connection_id or enough "
                    "endpoint fields to resolve one existing connection."
                ),
                error_type=ErrorCode.TOOL_CALL_INVALID,
                validation_errors=[
                    {
                        "code": "missing_required",
                        "field": "connection_id",
                        "message": "Provide connection_id or endpoint fields.",
                    }
                ],
            )
        return self._remove_connection_by_id(connection_id.strip())

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
        labels = ("A", "B", "C")
        options: list[ClarificationOption] = []
        revision = self.session.state_revision
        for label, candidate in zip(labels, candidates, strict=False):
            connection_id = candidate["connection_id"]
            options.append(
                ClarificationOption(
                    label=label,
                    title=connection_id,
                    description=(
                        f"{candidate['src_block']}:{candidate['src_port']} -> "
                        f"{candidate['dst_block']}:{candidate['dst_port']}"
                    ),
                    tool_name="remove_connection",
                    tool_args={"connection_id": connection_id},
                    metadata={
                        "state_revision": revision,
                        "connection_id": connection_id,
                    },
                )
            )
        request = ClarificationRequest(
            kind="connection_disambiguation",
            question="Multiple existing connections match. Choose the one to remove.",
            options=options,
            state_revision=revision,
        )
        payload = request.to_dict()
        payload.update(
            {
                "ok": False,
                "message": "Multiple existing connections match the provided endpoints.",
                "error_type": "ambiguous_connection",
            }
        )
        return payload

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
        return value is not None and not (isinstance(value, str) and not value.strip())

    def _rewire_new_endpoint_is_exact(
        self,
        *,
        new_src_block: str | None,
        new_src_port: int | str | None,
        new_dst_block: str | None,
        new_dst_port: int | str | None,
    ) -> bool:
        return all(
            self._has_endpoint_value(value)
            for value in (new_src_block, new_src_port, new_dst_block, new_dst_port)
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
        if self._rewire_new_endpoint_is_exact(
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        ):
            return {
                "ok": True,
                "new_src_block": str(new_src_block),
                "new_src_port": new_src_port,
                "new_dst_block": str(new_dst_block),
                "new_dst_port": new_dst_port,
            }

        missing_fields = [
            field
            for field, value in (
                ("new_src_block", new_src_block),
                ("new_src_port", new_src_port),
                ("new_dst_block", new_dst_block),
                ("new_dst_port", new_dst_port),
            )
            if not self._has_endpoint_value(value)
        ]
        has_source_hint = self._has_endpoint_value(new_src_block) or self._has_endpoint_value(new_src_port)
        has_destination_hint = self._has_endpoint_value(new_dst_block) or self._has_endpoint_value(new_dst_port)
        if not has_source_hint or not has_destination_hint:
            missing_side = "new_source" if not has_source_hint else "new_destination"
            return {
                "ok": False,
                "message": (
                    "rewire_connection requires at least one hint for both the "
                    "new source and new destination; it will not infer an entire endpoint side."
                ),
                "error_type": ErrorCode.TOOL_CALL_INVALID,
                "state_revision": self.session.state_revision,
                "validation_errors": [
                    {
                        "code": "missing_required",
                        "field": missing_side,
                        "message": (
                            "Provide exact fields or at least one bounded hint for "
                            "this new endpoint side."
                        ),
                    }
                ],
            }
        candidates = self._rewire_new_endpoint_candidates(
            old_connection_id=old_connection_id,
            new_src_block=new_src_block,
            new_src_port=new_src_port,
            new_dst_block=new_dst_block,
            new_dst_port=new_dst_port,
        )
        if not candidates:
            return {
                "ok": False,
                "message": (
                    "rewire_connection requires exact new endpoints or endpoint hints "
                    "that resolve to existing executable candidates."
                ),
                "error_type": ErrorCode.TOOL_CALL_INVALID,
                "state_revision": self.session.state_revision,
                "validation_errors": [
                    {
                        "code": "missing_required",
                        "field": field,
                        "message": (
                            "Provide an exact new endpoint field or enough endpoint "
                            "hints to resolve executable candidates."
                        ),
                    }
                    for field in missing_fields
                ],
            }
        if len(candidates) == 1:
            candidate = candidates[0]
            return {"ok": True, **candidate}
        if len(candidates) > 3:
            return {
                "ok": False,
                "message": (
                    "Too many executable new endpoint candidates match. "
                    "Provide exact new source and destination endpoints."
                ),
                "error_type": "ambiguous_rewire_endpoint",
                "state_revision": self.session.state_revision,
                "candidate_count": len(candidates),
            }
        return self._rewire_new_endpoint_clarification_payload(
            old_connection_id=old_connection_id,
            candidates=candidates,
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
        parsed_old = parse_connection_id(old_connection_id)
        if parsed_old is None:
            return []
        source_candidates = self._connection_endpoint_candidates(
            side="source",
            block=new_src_block,
            port=new_src_port,
        )
        destination_candidates = self._connection_endpoint_candidates(
            side="destination",
            block=new_dst_block,
            port=new_dst_port,
        )
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, int | str, str, int | str]] = set()
        for source_block, source_port in source_candidates:
            for destination_block, destination_port in destination_candidates:
                connection = (source_block, source_port, destination_block, destination_port)
                if connection == parsed_old or connection in seen:
                    continue
                seen.add(connection)
                candidate = {
                    "new_src_block": source_block,
                    "new_src_port": source_port,
                    "new_dst_block": destination_block,
                    "new_dst_port": destination_port,
                }
                if self._rewire_candidate_passes_preflight(old_connection_id, candidate):
                    candidates.append(candidate)
        return candidates

    def _connection_endpoint_candidates(
        self,
        *,
        side: str,
        block: str | None,
        port: int | str | None,
    ) -> list[tuple[str, int | str]]:
        if self._has_endpoint_value(block) and self._has_endpoint_value(port):
            loaded_block = self._loaded_block_by_name(str(block))
            if loaded_block is None:
                return []
            if not self._loaded_block_has_port(
                block_type=loaded_block.block_type,
                port=port,
                side=side,
            ):
                return []
            return [(str(block), port)]
        flowgraph = self.session.flowgraph
        if flowgraph is None:
            return []
        candidates: set[tuple[str, int | str]] = set()
        if self._has_endpoint_value(port):
            if self._has_endpoint_value(block):
                candidates.add((str(block), port))
            else:
                for loaded_block in flowgraph.blocks:
                    if self._loaded_block_has_port(
                        block_type=loaded_block.block_type,
                        port=port,
                        side=side,
                    ):
                        candidates.add((loaded_block.instance_name, port))
        for connection in flowgraph.connections:
            if side == "source":
                endpoint_block = connection.src_block
                endpoint_port = connection.src_port
            else:
                endpoint_block = connection.dst_block
                endpoint_port = connection.dst_port
            if self._has_endpoint_value(block) and endpoint_block != block:
                continue
            if self._has_endpoint_value(port) and not FlowgraphSession._port_matches(endpoint_port, port):
                continue
            candidates.add((endpoint_block, endpoint_port))
        return sorted(candidates, key=lambda item: (item[0], str(item[1])))

    def _loaded_block_by_name(self, instance_name: str) -> Any | None:
        flowgraph = self.session.flowgraph
        if flowgraph is None:
            return None
        return next(
            (
                loaded_block
                for loaded_block in flowgraph.blocks
                if loaded_block.instance_name == instance_name
            ),
            None,
        )

    def _loaded_block_has_port(
        self,
        *,
        block_type: str,
        port: int | str,
        side: str,
    ) -> bool:
        description = describe_block(block_type)
        if not description.get("ok"):
            return False
        field_name = "outputs" if side == "source" else "inputs"
        ports = description.get(field_name)
        if not isinstance(ports, list):
            return False
        if not isinstance(port, str):
            return any(
                isinstance(candidate, dict)
                and candidate.get("domain") != "message"
                and not candidate.get("id")
                for candidate in ports
            )
        return any(
            isinstance(candidate, dict) and candidate.get("id") == port
            for candidate in ports
        )

    def _rewire_candidate_passes_preflight(
        self,
        old_connection_id: str,
        candidate: dict[str, Any],
    ) -> bool:
        proposal = propose_edit(
            self.session,
            [
                {
                    "op_type": "remove_connection",
                    "connection_id": old_connection_id,
                },
                {
                    "op_type": "add_connection",
                    "src_block": candidate["new_src_block"],
                    "src_port": candidate["new_src_port"],
                    "dst_block": candidate["new_dst_block"],
                    "dst_port": candidate["new_dst_port"],
                },
            ],
            self.catalog_root,
        )
        return bool(proposal.get("ok"))

    def _rewire_new_endpoint_clarification_payload(
        self,
        *,
        old_connection_id: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        revision = self.session.state_revision
        options: list[ClarificationOption] = []
        for label, candidate in zip(("A", "B", "C"), candidates, strict=False):
            new_connection_id = render_connection_id(
                candidate["new_src_block"],
                candidate["new_src_port"],
                candidate["new_dst_block"],
                candidate["new_dst_port"],
            )
            options.append(
                ClarificationOption(
                    label=label,
                    title=new_connection_id,
                    description=f"replace {old_connection_id} with {new_connection_id}",
                    tool_name="rewire_connection",
                    tool_args={
                        "old_connection_id": old_connection_id,
                        "new_src_block": candidate["new_src_block"],
                        "new_src_port": candidate["new_src_port"],
                        "new_dst_block": candidate["new_dst_block"],
                        "new_dst_port": candidate["new_dst_port"],
                    },
                    metadata={
                        "state_revision": revision,
                        "old_connection_id": old_connection_id,
                        "new_connection_id": new_connection_id,
                    },
                )
            )
        request = ClarificationRequest(
            kind="rewire_new_endpoint_disambiguation",
            question="Multiple executable new endpoints match. Choose the exact rewire target.",
            options=options,
            state_revision=revision,
        )
        payload = request.to_dict()
        payload.update(
            {
                "ok": False,
                "message": "Multiple executable new endpoints match the provided hints.",
                "error_type": "ambiguous_rewire_endpoint",
            }
        )
        return payload

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
        old_endpoint_args = {
            "src_block": old_src_block,
            "src_port": old_src_port,
            "dst_block": old_dst_block,
            "dst_port": old_dst_port,
        }
        has_old_hint = any(value is not None for value in old_endpoint_args.values())

        if has_old_hint:
            resolved = self.session.find_connection_candidates(**old_endpoint_args)
            candidates = resolved["candidates"]
            if not candidates:
                return {
                    "ok": False,
                    "message": "No existing old connection matches the provided endpoint fields.",
                    "error_type": "connection_not_found",
                    "state_revision": self.session.state_revision,
                }
            if len(candidates) > 1:
                if not self._rewire_new_endpoint_is_exact(
                    new_src_block=new_src_block,
                    new_src_port=new_src_port,
                    new_dst_block=new_dst_block,
                    new_dst_port=new_dst_port,
                ):
                    return {
                        "ok": False,
                        "message": (
                            "Multiple old connections match. Provide an exact old "
                            "connection before resolving partial new endpoint hints."
                        ),
                        "error_type": "ambiguous_connection",
                        "state_revision": self.session.state_revision,
                    }
                return self._rewire_clarification_payload(
                    candidates,
                    new_src_block=str(new_src_block),
                    new_src_port=new_src_port,
                    new_dst_block=str(new_dst_block),
                    new_dst_port=new_dst_port,
                )
            resolved_connection_id = candidates[0]["connection_id"]
            if old_connection_id is not None and old_connection_id != resolved_connection_id:
                return {
                    "ok": False,
                    "message": (
                        "old_connection_id does not match the provided old endpoint fields: "
                        f"{old_connection_id}"
                    ),
                    "error_type": "connection_endpoint_mismatch",
                    "state_revision": self.session.state_revision,
                }
            return {"ok": True, "old_connection_id": resolved_connection_id}

        if not isinstance(old_connection_id, str) or not old_connection_id.strip():
            return {
                "ok": False,
                "message": (
                    "rewire_connection requires old_connection_id or enough old "
                    "endpoint fields to resolve one existing connection."
                ),
                "error_type": ErrorCode.TOOL_CALL_INVALID,
                "state_revision": self.session.state_revision,
                "validation_errors": [
                    {
                        "code": "missing_required",
                        "field": "old_connection_id",
                        "message": "Provide old_connection_id or old endpoint fields.",
                    }
                ],
            }

        parsed = parse_connection_id(old_connection_id.strip())
        if parsed is None:
            return {
                "ok": False,
                "message": "old_connection_id must be in form src_block:src_port->dst_block:dst_port.",
                "error_type": ErrorCode.TOOL_CALL_INVALID,
                "state_revision": self.session.state_revision,
            }
        src_block, src_port, dst_block, dst_port = parsed
        resolved = self.session.find_connection_candidates(
            src_block=src_block,
            src_port=src_port,
            dst_block=dst_block,
            dst_port=dst_port,
        )
        candidates = resolved["candidates"]
        if not candidates:
            return {
                "ok": False,
                "message": f"Old connection not found: {old_connection_id.strip()}",
                "error_type": "connection_not_found",
                "state_revision": self.session.state_revision,
            }
        if len(candidates) > 1:
            if not self._rewire_new_endpoint_is_exact(
                new_src_block=new_src_block,
                new_src_port=new_src_port,
                new_dst_block=new_dst_block,
                new_dst_port=new_dst_port,
            ):
                return {
                    "ok": False,
                    "message": (
                        "Multiple old connections match. Provide an exact old "
                        "connection before resolving partial new endpoint hints."
                    ),
                    "error_type": "ambiguous_connection",
                    "state_revision": self.session.state_revision,
                }
            return self._rewire_clarification_payload(
                candidates,
                new_src_block=str(new_src_block),
                new_src_port=new_src_port,
                new_dst_block=str(new_dst_block),
                new_dst_port=new_dst_port,
            )
        return {"ok": True, "old_connection_id": candidates[0]["connection_id"]}

    def _rewire_clarification_payload(
        self,
        candidates: list[dict[str, Any]],
        *,
        new_src_block: str,
        new_src_port: int | str | None,
        new_dst_block: str,
        new_dst_port: int | str | None,
    ) -> dict[str, Any]:
        revision = self.session.state_revision
        options: list[ClarificationOption] = []
        for label, candidate in zip(("A", "B", "C"), candidates, strict=False):
            old_connection_id = candidate["connection_id"]
            options.append(
                ClarificationOption(
                    label=label,
                    title=old_connection_id,
                    description=(
                        f"replace {candidate['src_block']}:{candidate['src_port']} -> "
                        f"{candidate['dst_block']}:{candidate['dst_port']} with "
                        f"{new_src_block}:{new_src_port} -> {new_dst_block}:{new_dst_port}"
                    ),
                    tool_name="rewire_connection",
                    tool_args={
                        "old_connection_id": old_connection_id,
                        "new_src_block": new_src_block,
                        "new_src_port": new_src_port,
                        "new_dst_block": new_dst_block,
                        "new_dst_port": new_dst_port,
                    },
                    metadata={
                        "state_revision": revision,
                        "old_connection_id": old_connection_id,
                    },
                )
            )
        request = ClarificationRequest(
            kind="rewire_connection_disambiguation",
            question="Multiple old connections match. Choose the exact edge to rewire.",
            options=options,
            state_revision=revision,
        )
        payload = request.to_dict()
        payload.update(
            {
                "ok": False,
                "message": "Multiple old connections match the provided endpoint hints.",
                "error_type": "ambiguous_connection",
            }
        )
        return payload

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

        return suggestions

    def _duplicate_block_clarification_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Build executable clarification for duplicate names only when safe."""
        errors = payload.get("errors")
        operations = payload.get("normalized_operations")
        if not isinstance(errors, list) or not isinstance(operations, list):
            return None

        duplicate_errors = [
            error
            for error in errors
            if isinstance(error, dict)
            and error.get("code") == "block_name_not_unique"
            and isinstance(error.get("op_index"), int)
        ]
        if len(duplicate_errors) != 1 or len(operations) != 1:
            return None

        op_index = duplicate_errors[0]["op_index"]
        if op_index < 0 or op_index >= len(operations):
            return None
        operation = operations[op_index]
        if not isinstance(operation, dict):
            return None
        if operation.get("op_type") not in {"update_params", "update_states", "remove_block"}:
            return None
        if "block_type" in operation:
            # Same-name same-type duplicates are not executable without a UID-based schema.
            return None
        instance_name = operation.get("instance_name")
        if not isinstance(instance_name, str) or not instance_name:
            return None

        resolved = self.session.resolve_block_reference(instance_name)
        candidates = resolved.get("candidates", [])
        if not isinstance(candidates, list) or len(candidates) < 2 or len(candidates) > 3:
            return None

        block_types = [
            candidate.get("block_type")
            for candidate in candidates
            if isinstance(candidate, dict) and isinstance(candidate.get("block_type"), str)
        ]
        if len(block_types) != len(candidates):
            return None
        block_types_are_unique = len(set(block_types)) == len(block_types)

        revision = self.session.state_revision
        options: list[ClarificationOption] = []
        for label, candidate in zip(("A", "B", "C"), candidates, strict=False):
            block_type = candidate["block_type"]
            transaction = copy.deepcopy(operation)
            if block_types_are_unique:
                transaction["block_type"] = block_type
            else:
                block_uid = candidate.get("block_uid")
                if not isinstance(block_uid, str) or not block_uid:
                    return None
                transaction.pop("instance_name", None)
                transaction.pop("block_type", None)
                transaction["target_ref"] = {
                    "block_uid": block_uid,
                    "expected_instance_name": instance_name,
                    "expected_block_type": block_type,
                    "base_state_revision": revision,
                }
            options.append(
                ClarificationOption(
                    label=label,
                    title=f"{instance_name} ({block_type})",
                    description=(
                        f"state={candidate.get('state')}; "
                        f"coordinate={candidate.get('coordinate')}"
                    ),
                    tool_name="apply_edit",
                    tool_args={"transaction": transaction},
                    metadata={
                        "state_revision": revision,
                        "block_uid": candidate.get("block_uid"),
                        "block_type": block_type,
                    },
                )
            )

        request = ClarificationRequest(
            kind="block_disambiguation",
            question=f"Multiple blocks are named `{instance_name}`. Choose the exact target.",
            options=options,
            state_revision=revision,
        )
        clarification = request.to_dict()
        clarification.update(
            {
                "ok": False,
                "message": (
                    "Multiple blocks match the requested instance_name. "
                    "Choose one candidate before mutating."
                ),
                "error_type": "ambiguous_block",
                "errors": copy.deepcopy(errors),
                "normalized_operations": copy.deepcopy(operations),
            }
        )
        return clarification

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
        elif clarification := self._duplicate_block_clarification_payload(payload):
            self._store_pending_clarification(clarification)
            return self._payload_result("apply_edit", clarification)
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

    def _save_graph_explicit(
        self,
        path: str | None = None,
        overwrite: bool = False,
        debug: bool = False,
    ) -> ToolResult:
        return save_graph_explicit_wrapper(
            self,
            path=path,
            overwrite=overwrite,
            debug=debug,
        )

    def _load_graph_explicit(
        self,
        path: str,
        debug: bool = False,
    ) -> ToolResult:
        return load_graph_explicit_wrapper(
            self,
            path=path,
            debug=debug,
        )

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
