"""Thin runtime wrapper for routed package-level `.grc` tools."""

import copy
from collections import OrderedDict
from dataclasses import dataclass
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
from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.tool_surface import (
    MVP_MODEL_TOOL_NAMES,
    MODEL_TOOL_NAMES_ORDERED,
    tool_surface_for_legacy_flag,
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
_DOCS_TOPIC_SYNONYMS: dict[str, tuple[str, ...]] = {
    "pmt": ("polymorphic", "types", "message"),
    "pmts": ("polymorphic", "types", "message"),
    "stream": ("sample", "samples", "data"),
    "tags": ("metadata", "length", "tag"),
    "message": ("port", "ports", "pdu", "queue"),
    "flowgraph": ("top block", "graph", "blocks"),
    "decimation": ("sample rate", "downsample", "rate change", "sample_rate_change"),
    "interpolation": ("sample rate", "upsample", "rate change", "sample_rate_change"),
    "sample": ("rate", "sps", "decimation", "interpolation"),
    "ports": ("message", "stream", "queue"),
    "throttle": ("rate", "sample", "limit", "pace"),
    "grcc": ("compiler", "compile", "validation", "validate"),
    "hierarchical": ("hier", "wrapper", "block"),
    "tagged": ("length", "packet", "pdu"),
}
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


@dataclass(frozen=True)
class _DocsEvidenceCandidate:
    snippet: DocsAnswerSnippet
    source_channel: str
    source_type: str
    section: str
    lexical_score: float
    semantic_score: float | None
    topic_score: float
    quality_score: float
    low_value_reasons: tuple[str, ...]
    procedural: bool


@dataclass(frozen=True)
class _DocsComparisonSides:
    left_label: str
    right_label: str
    left_terms: tuple[str, ...]
    right_terms: tuple[str, ...]


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
        return Path(path_value).expanduser().resolve(strict=False)

    def _unsafe_graph_root_for_path(self, path_value: str | Path) -> str | None:
        candidate = self._resolved_path(path_value)
        roots = (
            *(_INSTALLED_GRAPH_ROOTS),
            _CANONICAL_FIXTURE_ROOT,
        )
        for root in roots:
            resolved_root = root.expanduser().resolve(strict=False)
            try:
                candidate.relative_to(resolved_root)
            except ValueError:
                continue
            return str(resolved_root)
        return None

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
        started = time.monotonic()
        before_revision = self.session.state_revision
        before_dirty = self.session.is_dirty
        op = str(operation).strip().lower()
        handlers: list[str] = []
        output_truncated = False
        if op == "summarize":
            handlers.append("summarize_graph")
            summary_limit = (
                max_items
                if isinstance(max_items, int) and max_items > 0
                else self._guardrails_cfg.max_graph_summary_blocks
            )
            payload = summarize_graph(self.session, max_blocks=summary_limit)
            output_truncated = bool(payload.get("blocks_truncated", 0))
            result = self._payload_result("inspect_graph", self._compact_inspect_payload(op, payload))
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="inspect_graph",
                wrapper_action=op,
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=output_truncated,
            )
        if op == "validate":
            handlers.append("validate_graph")
            result = self._validate_graph()
            wrapper_result = self._payload_result(
                "inspect_graph",
                {
                    "ok": bool(result.get("ok")),
                    "operation": op,
                    "valid": bool(result.get("valid")),
                    "message": result.get("message"),
                    "error_type": result.get("error_type"),
                    "validation_result": {
                        "valid": bool(result.get("valid")),
                        "returncode": result.get("returncode"),
                        "stderr": result.get("stderr"),
                    },
                },
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="inspect_graph",
                wrapper_action=op,
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=wrapper_result,
                validation_run=True,
                output_truncated=False,
            )
        if op == "context":
            if not isinstance(target, str) or not target.strip():
                result = self._tool_result(
                    "inspect_graph",
                    ok=False,
                    message="context requires target.",
                    error_type=ErrorCode.INVALID_REQUEST,
                )
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="inspect_graph",
                    wrapper_action=op,
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=result,
                    validation_run=False,
                    output_truncated=False,
                )
            handlers.append("get_grc_context")
            payload = self._get_grc_context(
                target.strip(),
                max_nodes=max_items or self._guardrails_cfg.max_context_nodes,
            )
            output_truncated = bool(payload.get("truncated"))
            result = self._payload_result("inspect_graph", self._compact_inspect_payload(op, payload))
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="inspect_graph",
                wrapper_action=op,
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=output_truncated,
            )

        missing_session = self._missing_session_result("inspect_graph")
        if missing_session is not None:
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="inspect_graph",
                wrapper_action=op,
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=missing_session,
                validation_run=False,
                output_truncated=False,
            )

        snapshot = self.active_session_snapshot() or {}
        if op == "list_blocks":
            handlers.append("session_snapshot.list_blocks")
            items = list((snapshot.get("block_preview") or []))
            total_items = len(items)
            if isinstance(max_items, int) and max_items > 0:
                items = items[:max_items]
            output_truncated = len(items) < total_items
            result = self._payload_result(
                "inspect_graph",
                {"ok": True, "operation": op, "items": items},
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="inspect_graph",
                wrapper_action=op,
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=output_truncated,
            )
        if op == "list_connections":
            handlers.append("session_snapshot.list_connections")
            items = list((snapshot.get("connection_preview") or []))
            total_items = len(items)
            if isinstance(max_items, int) and max_items > 0:
                items = items[:max_items]
            output_truncated = len(items) < total_items
            result = self._payload_result(
                "inspect_graph",
                {"ok": True, "operation": op, "items": items},
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="inspect_graph",
                wrapper_action=op,
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=output_truncated,
            )
        if op == "list_variables":
            handlers.append("session_snapshot.list_variables")
            items = list((snapshot.get("variable_preview") or []))
            total_items = len(items)
            if isinstance(max_items, int) and max_items > 0:
                items = items[:max_items]
            output_truncated = len(items) < total_items
            result = self._payload_result(
                "inspect_graph",
                {"ok": True, "operation": op, "items": items},
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="inspect_graph",
                wrapper_action=op,
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=output_truncated,
            )
        if op == "history_summary":
            handlers.append("history_journal.list_records")
            records = self._history_journal.list_records()
            if isinstance(self._history_lineage_key, str):
                records = [
                    record
                    for record in records
                    if record.get("lineage_key") == self._history_lineage_key
                ]
            if isinstance(max_items, int) and max_items > 0:
                records = records[-max_items:]
            else:
                records = records[-10:]
            compact = [
                {
                    "id": record.get("id"),
                    "kind": record.get("record_type"),
                    "tool_name": record.get("tool_name"),
                    "operation_type": record.get("operation_type"),
                    "state_revision": record.get("state_revision"),
                    "timestamp": record.get("timestamp"),
                }
                for record in records
            ]
            result = self._payload_result(
                "inspect_graph",
                {"ok": True, "operation": op, "items": compact},
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="inspect_graph",
                wrapper_action=op,
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
        result = self._tool_result(
            "inspect_graph",
            ok=False,
            message=f"Unsupported inspect_graph operation: {operation!r}",
            error_type=ErrorCode.INVALID_REQUEST,
        )
        return self._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="inspect_graph",
            wrapper_action=op,
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
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
        started = time.monotonic()
        before_revision = self.session.state_revision
        before_dirty = self.session.is_dirty
        handlers: list[str] = []
        q = " ".join(str(query).split()) if isinstance(query, str) else ""
        if not q:
            result = self._tool_result(
                "search_blocks",
                ok=False,
                message="query must be non-empty.",
                error_type=ErrorCode.INVALID_REQUEST,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="search_blocks",
                wrapper_action="query",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
        session_ctx = self.session if self.session.flowgraph is not None else None
        limit_value = (
            self._retrieval_cfg.search_blocks_default_k
            if k is None
            else int(k)
        )
        limit = max(1, min(limit_value, self._retrieval_cfg.search_blocks_max_k))
        cacheable = not debug and not enrich
        lexical: dict[str, Any] = {"ok": True, "results": []}
        retrieval_mode = "hybrid"
        semantic: dict[str, Any] = {"ok": True, "results": []}

        query_raw = " ".join(q.split()).strip().lower()
        query_alias = _normalize_alias_key(q)
        exact_block_id: str | None = None
        exact_alias_hit = False
        if self._retrieval_cfg.exact_match_fast_path:
            try:
                alias_map = _catalog_alias_to_block_map(self.catalog_root)
                if query_raw and query_raw in alias_map:
                    exact_block_id = alias_map[query_raw]
                    exact_alias_hit = True
                elif query_alias and query_alias in alias_map:
                    exact_block_id = alias_map[query_alias]
                    exact_alias_hit = True
            except Exception:
                logger.exception("search_blocks_alias_map_failed")

        cache_key: tuple[str, int, str] | None = None
        if exact_block_id is None and cacheable:
            cache_key = self._search_blocks_cache_key(query=q, k=limit)
            cached_payload = self._search_blocks_cache_get(cache_key)
            if cached_payload is not None:
                handlers.append("search_blocks_cache(hit)")
                result = self._payload_result(
                    "search_blocks",
                    {
                        "ok": True,
                        "query": q,
                        "results": cached_payload["results"],
                        "degraded_retrieval": bool(cached_payload["degraded_retrieval"]),
                        "retrieval_mode": str(cached_payload["retrieval_mode"]),
                        "message": "Block candidates returned.",
                    },
                )
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="search_blocks",
                    wrapper_action="query",
                    internal_handlers=handlers,
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=result,
                    validation_run=False,
                    output_truncated=bool(cached_payload.get("output_truncated", False)),
                )

        handlers.append("search_grc(lexical,catalog)")
        lexical = _search_grc_with_context(
            q,
            scope="catalog",
            k=limit,
            session=session_ctx,
            catalog_root=self.catalog_root,
        )
        lexical_rows = lexical.get("results", []) if lexical.get("ok") else []

        if exact_block_id is None:
            handlers.append("search_blocks_cache(miss)")
            handlers.append("semantic_search_grc(catalog)")
            semantic = semantic_search_grc(q, scope="catalog", k=limit)
        else:
            retrieval_mode = "exact"

        merged: dict[str, dict[str, Any]] = {}
        degraded_retrieval = False
        if semantic.get("ok"):
            for row in semantic.get("results", []):
                if not isinstance(row, dict):
                    continue
                block_id = row.get("canonical_block_id")
                if not isinstance(block_id, str) or not block_id:
                    continue
                name = row.get("title")
                summary = row.get("excerpt")
                merged[block_id] = {
                    "block_id": block_id,
                    "name": name if isinstance(name, str) and name else block_id,
                    "summary": _compact_block_summary(summary),
                }
                if debug:
                    merged[block_id]["debug"] = {
                        "source": "semantic",
                        "record_id": row.get("record_id"),
                        "score": row.get("vector_score_raw"),
                    }
        else:
            if semantic:
                degraded_retrieval = semantic.get("error_type") in {
                    "missing_index",
                    ErrorCode.RETRIEVAL_NOT_READY,
                }
                if degraded_retrieval:
                    retrieval_mode = "lexical_fallback_missing_vector"

        if lexical.get("ok"):
            for row in lexical_rows:
                if not isinstance(row, dict):
                    continue
                block_id = row.get("block_id")
                if not isinstance(block_id, str) or not block_id:
                    continue
                current = merged.get(block_id)
                summary = row.get("summary")
                label = row.get("label")
                if current is None:
                    merged[block_id] = {
                        "block_id": block_id,
                        "name": label if isinstance(label, str) and label else block_id,
                        "summary": _compact_block_summary(summary),
                    }
                    if debug:
                        merged[block_id]["debug"] = {
                            "source": "lexical",
                            "record_id": row.get("node_id"),
                        }
                else:
                    if not current.get("summary") and isinstance(summary, str):
                        current["summary"] = _compact_block_summary(summary)
                    if current.get("name") == block_id and isinstance(label, str):
                        current["name"] = label
                    if debug and "debug" not in current:
                        current["debug"] = {
                            "source": "semantic+lexical",
                            "record_id": row.get("node_id"),
                        }

        if enrich:
            handlers.append("describe_block(enrichment)")
            for item in merged.values():
                if item.get("summary"):
                    continue
                details = describe_block(str(item.get("block_id", "")))
                if details.get("ok"):
                    summary = details.get("summary")
                    if isinstance(summary, str) and summary:
                        item["summary"] = _compact_block_summary(summary)

        ordered = list(merged.values())
        query_l = q.lower()
        ordered.sort(
            key=lambda item: (
                0
                if query_l in {item["block_id"].lower(), item["name"].lower()}
                else 1,
                item["block_id"],
            )
        )
        if retrieval_mode == "exact" and exact_block_id:
            limited = [item for item in ordered if item.get("block_id") == exact_block_id][:1]
            if not limited and lexical_rows:
                for row in lexical_rows:
                    if row.get("block_id") == exact_block_id:
                        label = row.get("label")
                        summary = row.get("summary")
                        fallback_row: dict[str, Any] = {
                            "block_id": exact_block_id,
                            "name": label if isinstance(label, str) and label else exact_block_id,
                            "summary": _compact_block_summary(summary),
                        }
                        if debug:
                            fallback_row["debug"] = {
                                "source": "lexical_exact_fallback",
                                "record_id": row.get("node_id"),
                                "exact_alias": exact_alias_hit,
                            }
                        limited = [fallback_row]
                        break
        else:
            limited = ordered[:limit]

        output_truncated = len(ordered) > len(limited)
        if not debug:
            limited = [
                {
                    "block_id": str(item.get("block_id", "")),
                    "name": str(item.get("name", "")),
                    "summary": str(item.get("summary", "")),
                }
                for item in limited
            ]
        if cache_key is not None and cacheable:
            self._search_blocks_cache_put(
                cache_key,
                {
                    "results": limited,
                    "degraded_retrieval": degraded_retrieval,
                    "retrieval_mode": retrieval_mode,
                    "output_truncated": output_truncated,
                },
            )
        result = self._payload_result(
            "search_blocks",
            {
                "ok": True,
                "query": q,
                "results": limited,
                "degraded_retrieval": degraded_retrieval,
                "retrieval_mode": retrieval_mode,
                "message": "Block candidates returned.",
            },
        )
        return self._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="search_blocks",
            wrapper_action="query",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=output_truncated,
        )

    def _ask_grc_docs(
        self,
        question: str,
        k: int | None = None,
        focus: str | None = None,
        debug: bool = False,
    ) -> ToolResult:
        started = time.monotonic()
        before_revision = self.session.state_revision
        before_dirty = self.session.is_dirty
        handlers: list[str] = []
        if not isinstance(question, str) or not question.strip():
            result = self._tool_result(
                "ask_grc_docs",
                ok=False,
                message="question must be non-empty.",
                error_type=ErrorCode.INVALID_REQUEST,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="ask_grc_docs",
                wrapper_action="query",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )

        limit = self._retrieval_cfg.ask_grc_docs_default_k
        if isinstance(k, int) and not isinstance(k, bool):
            limit = max(1, min(k, self._retrieval_cfg.ask_grc_docs_max_k))
        question_text = " ".join(question.split())
        focus_text = (
            " ".join(focus.split())
            if isinstance(focus, str) and focus.strip()
            else None
        )
        answer_type = self._classify_docs_answer_type(question_text)

        retrieval_k = max(
            limit,
            min(self._retrieval_cfg.ask_grc_docs_max_k, max(limit + 2, 5)),
        )
        retrieval_query = self._normalized_docs_retrieval_query(
            question=question_text,
            answer_type=answer_type,
        )
        handlers.append("search_manual")
        lexical_payload = search_manual(retrieval_query, k=retrieval_k)
        semantic_manual: dict[str, Any] = {"ok": False, "results": []}
        semantic_tutorial: dict[str, Any] = {"ok": False, "results": []}
        degraded_retrieval = False
        fallback_used = False
        fallback_reason = "not_attempted"
        warnings: list[str] = []
        retrieval_mode = "lexical_only"

        lexical_strong = self._is_lexical_docs_evidence_strong(
            query=retrieval_query,
            question=question_text,
            answer_type=answer_type,
            lexical_payload=lexical_payload,
            limit=limit,
        )
        lexical_weak = not lexical_strong
        tutorial_or_howto_query = self._is_tutorial_or_howto_query(question_text)
        run_manual_semantic = (
            self._docs_answer_cfg.semantic_manual_enabled
            and (not self._docs_answer_cfg.lexical_first or lexical_weak)
        )
        run_tutorial_semantic = (
            self._docs_answer_cfg.semantic_tutorial_enabled
            and (tutorial_or_howto_query or lexical_weak)
            and (not self._docs_answer_cfg.lexical_first or lexical_weak)
        )

        if run_manual_semantic:
            handlers.append("semantic_search_grc(manual)")
            semantic_manual = semantic_search_grc(
                retrieval_query,
                scope="manual",
                k=retrieval_k,
            )
            if semantic_manual.get("ok") is not True and semantic_manual.get(
                "error_type"
            ) in {
                "missing_index",
                ErrorCode.RETRIEVAL_NOT_READY,
            }:
                degraded_retrieval = True

        if run_tutorial_semantic:
            handlers.append("semantic_search_grc(tutorial)")
            semantic_tutorial = semantic_search_grc(
                retrieval_query,
                scope="tutorial",
                k=retrieval_k,
            )
            if semantic_tutorial.get("ok") is not True and semantic_tutorial.get(
                "error_type"
            ) in {
                "missing_index",
                ErrorCode.RETRIEVAL_NOT_READY,
            }:
                degraded_retrieval = True

        if run_manual_semantic and run_tutorial_semantic:
            retrieval_mode = "lexical_plus_manual_and_tutorial_semantic"
        elif run_manual_semantic:
            retrieval_mode = "lexical_plus_manual_semantic"
        elif run_tutorial_semantic:
            retrieval_mode = "lexical_plus_tutorial_semantic"

        if degraded_retrieval:
            retrieval_mode = "lexical_fallback_missing_vector"

        candidates = self._collect_docs_candidates(
            lexical_payload=lexical_payload,
            semantic_manual=semantic_manual,
            semantic_tutorial=semantic_tutorial,
        )
        ranked_candidates = self._rank_docs_candidates(
            question=question_text,
            candidates=candidates,
        )
        if self._is_block_definition_query(question_text):
            handlers.append("search_blocks(catalog_assisted_docs)")
            assisted = self._build_catalog_assisted_candidate(
                question=question_text
            )
            if assisted is not None:
                ranked_candidates = self._rank_docs_candidates(
                    question=question_text,
                    candidates=[*candidates, assisted],
                )
        elif self._should_catalog_assist(question_text, ranked_candidates):
            handlers.append("search_blocks(catalog_assisted_docs)")
            assisted = self._build_catalog_assisted_candidate(question=question_text)
            if assisted is not None:
                ranked_candidates = self._rank_docs_candidates(
                    question=question_text,
                    candidates=[*candidates, assisted],
                )

        severe_reasons = {
            "generic_gnuradio_page",
            "menu_index_page",
            "navigation_boilerplate",
            "toc_dominated",
        }
        filtered_candidates = [
            candidate
            for candidate in ranked_candidates
            if not any(reason in severe_reasons for reason in candidate.low_value_reasons)
        ]
        selected_pool = filtered_candidates or ranked_candidates
        selected_candidates = self._select_docs_candidates_for_answer_type(
            question=question_text,
            answer_type=answer_type,
            ranked_candidates=selected_pool,
            limit=max(1, min(limit, self._retrieval_cfg.ask_grc_docs_max_k)),
        )
        snippets = [candidate.snippet for candidate in selected_candidates]
        source_quality = self._build_docs_source_quality(
            question=question_text,
            answer_type=answer_type,
            selected_candidates=selected_candidates,
        )
        if degraded_retrieval:
            warnings.append("vector_index_missing_or_not_ready")

        insufficient_evidence = len(snippets) == 0 or str(source_quality.get("quality")) == "weak"
        answer = ""
        source_limit = min(limit, self._docs_answer_cfg.max_sources)
        sources = [
            {
                "title": snippet.title,
                "source": snippet.source,
                "excerpt": snippet.excerpt[: self._docs_answer_cfg.excerpt_target_chars],
            }
            for snippet in snippets
        ]
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
            "source_quality": dict(source_quality),
            "helper_eligible": False,
            "helper_skipped_reason": "not_evaluated",
        }
        evidence_strong = str(source_quality.get("quality")) == "strong"
        cache_key = self._ask_grc_docs_cache_key(
            question=question_text,
            k=source_limit,
            retrieval_mode=retrieval_mode,
            sources=sources[:source_limit],
        )
        cached_docs_answer = self._ask_grc_docs_cache_get(cache_key)
        if cached_docs_answer is not None:
            answer = str(cached_docs_answer.get("answer") or "")
            sources = list(cached_docs_answer.get("sources") or [])
            insufficient_evidence = bool(cached_docs_answer.get("insufficient_evidence"))
            fallback_used = bool(cached_docs_answer.get("fallback_used"))
            fallback_reason = str(cached_docs_answer.get("fallback_reason") or "cache_hit")
            helper_eligible = bool(cached_docs_answer.get("helper_eligible", False))
            helper_skipped_reason = str(
                cached_docs_answer.get("helper_skipped_reason") or "cache_hit"
            )
            cached_quality = cached_docs_answer.get("source_quality")
            if isinstance(cached_quality, dict):
                source_quality = dict(cached_quality)
            self._last_docs_advisor_meta.update(
                {
                    "advisor_attempted": False,
                    "advisor_success": True,
                    "fallback_reason": "none",
                    "helper_latency_ms": 0,
                    "prompt_chars": 0,
                    "snippet_count": len(snippets),
                    "schema_valid": True,
                    "cache_hit": True,
                    "helper_finish_reason": "cache_hit",
                    "source_quality": dict(source_quality),
                    "helper_eligible": bool(helper_eligible),
                    "helper_skipped_reason": helper_skipped_reason,
                }
            )
        if snippets and cached_docs_answer is None:
            helper_eligible = False
            helper_skipped_reason = "not_evaluated"
            typed_answer = "Local docs did not contain enough direct evidence for this question."
            typed_insufficient = True
            if str(source_quality.get("quality")) != "weak":
                typed_answer, typed_insufficient = self._build_typed_docs_answer(
                    question=question_text,
                    ranked_candidates=ranked_candidates,
                    answer_type=answer_type,
                )
                helper_eligible, helper_skipped_reason = self._helper_eligibility_for_docs_answer(
                    question=question_text,
                    answer_type=answer_type,
                    source_quality=source_quality,
                    selected_candidates=selected_candidates,
                    typed_answer=typed_answer,
                    typed_insufficient=typed_insufficient,
                )
            else:
                helper_skipped_reason = "weak_evidence"
            answer = typed_answer
            insufficient_evidence = bool(typed_insufficient)
            fallback_used = True
            fallback_reason = "typed_fallback"
            helper_input_candidates = self._helper_candidates_for_docs_answer(
                question=question_text,
                answer_type=answer_type,
                ranked_candidates=selected_pool,
            )
            helper_input = self._clip_docs_snippets_for_helper(
                [candidate.snippet for candidate in helper_input_candidates]
            )
            helper_result = None
            self._last_docs_advisor_meta.update(
                {
                    "source_quality": dict(source_quality),
                    "helper_eligible": bool(helper_eligible),
                    "helper_skipped_reason": helper_skipped_reason,
                }
            )
            if self._docs_answer_cfg.enabled and helper_eligible:
                helper_mode = self._docs_answer_cfg.helper_mode
                run_helper = False
                if helper_mode in {"always", "auto"}:
                    run_helper = True
                elif helper_mode == "never":
                    helper_skipped_reason = "helper_mode_never"
                if run_helper:
                    helper_result = self._run_docs_answer_advisor(
                        question=question_text,
                        answer_type=answer_type,
                        snippets=helper_input,
                        focus=focus_text,
                    )
                elif (
                    self._last_docs_advisor_meta.get("fallback_reason", "not_attempted")
                    == "not_attempted"
                ):
                    self._last_docs_advisor_meta["fallback_reason"] = helper_skipped_reason
            elif not self._docs_answer_cfg.enabled:
                helper_skipped_reason = "helper_disabled"
            else:
                self._last_docs_advisor_meta["fallback_reason"] = helper_skipped_reason

            advisor_meta = dict(self._last_docs_advisor_meta)
            if helper_result is not None:
                helper_answer = str(helper_result.get("answer") or "").strip()
                helper_answer_l = helper_answer.lower()
                helper_invalid = (
                    answer_type == "block_definition"
                    and any(
                        marker in helper_answer_l
                        for marker in ("input port(s)", "output port(s)", "parameter(s)")
                    )
                )
                if helper_invalid:
                    fallback_used = True
                    fallback_reason = "helper_answer_low_value"
                    self._last_docs_advisor_meta["fallback_reason"] = "helper_answer_low_value"
                    self._last_docs_advisor_meta["helper_finish_reason"] = "low_value"
                else:
                    answer = helper_answer
                    selected_sources: list[dict[str, str]] = []
                    source_indexes = helper_result.get("source_indexes")
                    if isinstance(source_indexes, list):
                        for index in source_indexes:
                            if not isinstance(index, int):
                                continue
                            if index < 0 or index >= len(helper_input):
                                continue
                            snippet = helper_input[index]
                            selected_sources.append(
                                {
                                    "title": snippet.title,
                                    "source": snippet.source,
                                    "excerpt": snippet.excerpt[
                                        : self._docs_answer_cfg.excerpt_target_chars
                                    ],
                                }
                            )
                    if selected_sources:
                        sources = selected_sources[:source_limit]
                    insufficient_evidence = bool(helper_result.get("insufficient_evidence"))
                    fallback_used = False
                    fallback_reason = "none"
                    self._last_docs_advisor_meta["helper_finish_reason"] = str(
                        helper_result.get("helper_finish_reason") or "stop"
                    )
            else:
                fallback_used = True
                fallback_reason = str(advisor_meta.get("fallback_reason") or "advisor_failed")
                if not self._last_docs_advisor_meta.get("helper_finish_reason"):
                    self._last_docs_advisor_meta["helper_finish_reason"] = fallback_reason
                if helper_eligible:
                    warnings.append("docs_answer_advisor_fallback")
            self._last_docs_advisor_meta["helper_eligible"] = bool(helper_eligible)
            self._last_docs_advisor_meta["helper_skipped_reason"] = helper_skipped_reason
            self._last_docs_advisor_meta["source_quality"] = dict(source_quality)
        elif not snippets:
            fallback_used = True
            fallback_reason = "retrieval_empty"
            self._last_docs_advisor_meta["helper_finish_reason"] = "retrieval_empty"
            self._last_docs_advisor_meta["helper_skipped_reason"] = "retrieval_empty"

        if not answer:
            answer, insufficient_evidence = self._build_fallback_answer(
                question=question_text,
                ranked_candidates=ranked_candidates,
                evidence_strong=evidence_strong,
            )
        answer = " ".join(answer.split())
        if len(answer) > self._docs_answer_cfg.answer_target_chars:
            answer = answer[: self._docs_answer_cfg.answer_target_chars - 1].rstrip() + "…"
        if cached_docs_answer is None:
            self._ask_grc_docs_cache_put(
                cache_key,
                {
                    "answer": answer,
                    "sources": sources[:source_limit],
                    "insufficient_evidence": bool(insufficient_evidence),
                    "fallback_used": bool(fallback_used or degraded_retrieval),
                    "fallback_reason": fallback_reason,
                    "source_quality": dict(source_quality),
                    "helper_eligible": bool(
                        self._last_docs_advisor_meta.get("helper_eligible", False)
                    ),
                    "helper_skipped_reason": str(
                        self._last_docs_advisor_meta.get("helper_skipped_reason") or ""
                    ),
                },
            )

        result = self._payload_result(
            "ask_grc_docs",
            {
                "ok": True,
                "question": question_text,
                "focus": focus_text,
                "answer": answer,
                "sources": sources[:source_limit],
                "insufficient_evidence": bool(insufficient_evidence),
                "fallback_used": bool(fallback_used or degraded_retrieval),
                "degraded_retrieval": bool(degraded_retrieval),
                "retrieval_mode": retrieval_mode,
                "warnings": warnings,
                "message": "Grounded docs answer returned.",
            },
        )
        if debug:
            meta = dict(self._last_docs_advisor_meta)
            meta["fallback_reason"] = fallback_reason
            result["docs_answer_telemetry"] = meta
        output_truncated = len(sources) >= limit
        return self._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="ask_grc_docs",
            wrapper_action="query",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=output_truncated,
        )

    def _collect_docs_candidates(
        self,
        *,
        lexical_payload: dict[str, Any],
        semantic_manual: dict[str, Any],
        semantic_tutorial: dict[str, Any],
    ) -> list[_DocsEvidenceCandidate]:
        candidates: list[_DocsEvidenceCandidate] = []
        seen_sources: set[str] = set()

        def _append(
            *,
            title: Any,
            source: Any,
            excerpt: Any,
            section: Any,
            channel: str,
            lexical_score: float = 0.0,
            semantic_score: float | None = None,
            source_type_hint: str | None = None,
        ) -> None:
            title_text = " ".join(str(title or "").split()).strip()
            source_text = " ".join(str(source or "").split()).strip()
            excerpt_text = self._clean_docs_excerpt(str(excerpt or ""))
            if not title_text or not source_text or not excerpt_text:
                return
            aliases = self._docs_title_aliases(title_text)
            if aliases:
                source_text = f"{source_text} | title_aliases:{','.join(aliases)}"

            source_key = self._normalize_docs_source_key(source_text)
            if source_key in seen_sources:
                return
            seen_sources.add(source_key)

            lower_excerpt = excerpt_text.lower()
            if title_text and lower_excerpt.startswith(title_text.lower()):
                excerpt_text = excerpt_text[len(title_text):].lstrip(" -:.\n")
            elif source_text and lower_excerpt.startswith(source_text.lower()):
                excerpt_text = excerpt_text[len(source_text):].lstrip(" -:.\n")

            max_collected_excerpt_chars = max(
                self._docs_answer_cfg.helper_max_total_context_chars,
                self._docs_answer_cfg.helper_max_snippet_chars,
                self._docs_answer_cfg.excerpt_target_chars * 2,
            )
            if len(excerpt_text) > max_collected_excerpt_chars:
                excerpt_text = excerpt_text[:max_collected_excerpt_chars].rstrip()
            candidates.append(
                _DocsEvidenceCandidate(
                    snippet=DocsAnswerSnippet(
                        title=title_text,
                        source=source_text,
                        excerpt=excerpt_text,
                    ),
                    source_channel=channel,
                    source_type=self._infer_docs_source_type(
                        source=source_text,
                        title=title_text,
                        source_type_hint=source_type_hint,
                    ),
                    section=" ".join(str(section or "").split()).strip(),
                    lexical_score=float(lexical_score),
                    semantic_score=semantic_score,
                    topic_score=0.0,
                    quality_score=0.0,
                    low_value_reasons=(),
                    procedural=False,
                )
            )

        if lexical_payload.get("ok") is True:
            for row in lexical_payload.get("results", []):
                if not isinstance(row, dict):
                    continue
                citation = row.get("citation")
                source = None
                if isinstance(citation, dict):
                    source = citation.get("url") or citation.get("path")
                score_raw = row.get("score")
                lexical_score = (
                    float(score_raw) if isinstance(score_raw, int | float) else 0.0
                )
                _append(
                    title=row.get("title"),
                    source=source,
                    excerpt=row.get("excerpt"),
                    section=row.get("section"),
                    channel="lexical",
                    lexical_score=lexical_score,
                )

        for payload, channel in (
            (semantic_manual, "semantic_manual"),
            (semantic_tutorial, "semantic_tutorial"),
        ):
            if payload.get("ok") is not True:
                continue
            for row in payload.get("results", []):
                if not isinstance(row, dict):
                    continue
                provenance = row.get("provenance")
                source = None
                if isinstance(provenance, dict):
                    source = provenance.get("url") or provenance.get("path")
                semantic_raw = row.get("vector_score_raw")
                semantic_score = (
                    float(semantic_raw)
                    if isinstance(semantic_raw, int | float)
                    else None
                )
                _append(
                    title=row.get("title"),
                    source=source,
                    excerpt=row.get("excerpt"),
                    section=row.get("section"),
                    channel=channel,
                    semantic_score=semantic_score,
                    source_type_hint=str(row.get("source_type") or ""),
                )
        return candidates

    def _rank_docs_candidates(
        self,
        *,
        question: str,
        candidates: list[_DocsEvidenceCandidate],
    ) -> list[_DocsEvidenceCandidate]:
        if not candidates:
            return []
        keywords = self._docs_topic_terms(question)
        primary_terms = self._docs_primary_terms(question)
        query_l = question.lower()
        howto = self._is_tutorial_or_howto_query(question)
        block_definition_query = self._is_block_definition_query(question)
        ranked: list[_DocsEvidenceCandidate] = []
        for candidate in candidates:
            title_l = candidate.snippet.title.lower()
            section_l = candidate.section.lower()
            excerpt_l = candidate.snippet.excerpt.lower()
            text = " ".join([title_l, section_l, excerpt_l])
            term_hits = sum(1 for term in keywords if term in text)
            title_hits = sum(1 for term in keywords if term in title_l)
            heading_hits = sum(1 for term in keywords if term in section_l)
            phrase_bonus = 2.0 if query_l in text and len(query_l) > 8 else 0.0
            synonym_hits = 0
            for term in keywords:
                for synonym in _DOCS_TOPIC_SYNONYMS.get(term, ()):
                    if synonym in text:
                        synonym_hits += 1
            topic_score = (
                float(term_hits)
                + (2.0 * float(title_hits))
                + (1.5 * float(heading_hits))
                + min(2.0, float(synonym_hits) * 0.5)
                + phrase_bonus
            )
            source_pref = 0.0
            if howto:
                source_pref = 1.5 if candidate.source_type == "tutorial" else -0.3
            elif block_definition_query and candidate.source_type == "catalog":
                source_pref = 2.4
            elif block_definition_query and candidate.source_type == "manual":
                source_pref = 0.6
            elif block_definition_query and candidate.source_type == "tutorial":
                source_pref = -1.4
            elif candidate.source_type == "manual":
                source_pref = 1.5
            elif candidate.source_type == "tutorial":
                source_pref = -1.2
            lexical_component = min(4.0, candidate.lexical_score / 25.0)
            semantic_component = 0.0
            if isinstance(candidate.semantic_score, float):
                semantic_component = (candidate.semantic_score - 0.62) * 7.0
            low_value_reasons = self._docs_low_value_reasons(candidate=candidate)
            low_value_penalty = float(len(low_value_reasons)) * 1.6
            procedural = self._is_procedural_walkthrough_text(
                candidate.snippet.excerpt
            )
            procedural_penalty = 2.5 if procedural and not howto else 0.0
            primary_hits = sum(1 for term in primary_terms if term in text)
            if primary_terms and primary_hits == 0:
                topic_score -= 1.5
            if (
                primary_terms
                and not any(term in title_l for term in primary_terms)
                and "catalog:" not in candidate.snippet.source.lower()
            ):
                topic_score -= 0.8
            weak_absence_penalty = 0.0
            if topic_score <= 0.0 and (candidate.semantic_score or 0.0) < 0.74:
                weak_absence_penalty = 2.0
            quality_score = (
                topic_score
                + source_pref
                + lexical_component
                + semantic_component
                - low_value_penalty
                - procedural_penalty
                - weak_absence_penalty
            )
            ranked.append(
                _DocsEvidenceCandidate(
                    snippet=candidate.snippet,
                    source_channel=candidate.source_channel,
                    source_type=candidate.source_type,
                    section=candidate.section,
                    lexical_score=candidate.lexical_score,
                    semantic_score=candidate.semantic_score,
                    topic_score=topic_score,
                    quality_score=quality_score,
                    low_value_reasons=tuple(low_value_reasons),
                    procedural=procedural,
                )
            )
        ranked.sort(
            key=lambda item: (
                -item.quality_score,
                -item.topic_score,
                -(item.semantic_score or 0.0),
                -item.lexical_score,
                item.snippet.title.lower(),
            )
        )
        return ranked

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
        if not selected_candidates:
            return {
                "quality": "weak",
                "reason": "no_selected_sources",
                "selected_source_count": 0,
                "topic_match": False,
                "required_terms_covered": False,
                "source_hint_match": False,
                "is_menu_or_boilerplate": False,
                "supports_answer_type": False,
            }
        severe = {
            "generic_gnuradio_page",
            "menu_index_page",
            "navigation_boilerplate",
            "toc_dominated",
        }
        top = selected_candidates[0]
        is_menu_or_boilerplate = any(reason in severe for reason in top.low_value_reasons)
        required_terms = self._required_terms_for_answer_type(
            question=question,
            answer_type=answer_type,
        )
        selected_text = " ".join(
            " ".join([candidate.snippet.title, candidate.section, candidate.snippet.excerpt]).lower()
            for candidate in selected_candidates
        )
        required_hits = sum(
            1
            for term in required_terms
            if term and self._text_matches_term_or_synonym(selected_text, term)
        )
        required_min = self._minimum_required_term_hits(required_terms)
        required_terms_covered = required_hits >= required_min
        primary_terms = self._docs_primary_terms(question) or self._docs_topic_terms(question)
        top_text = " ".join([top.snippet.title, top.section, top.snippet.excerpt]).lower()
        topic_match = (
            True
            if not primary_terms
            else any(self._text_matches_term_or_synonym(top_text, term) for term in primary_terms)
        )

        source_hint_match = required_terms_covered
        supports_answer_type = required_terms_covered and topic_match
        if answer_type == "comparison":
            sides = self._extract_comparison_sides(question)
            if sides is None:
                supports_answer_type = False
                source_hint_match = False
            else:
                left_text_match = any(
                    any(
                        self._text_matches_term_or_synonym(
                            " ".join(
                                [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
                            ).lower(),
                            term,
                        )
                        for term in sides.left_terms
                    )
                    for candidate in selected_candidates
                )
                right_text_match = any(
                    any(
                        self._text_matches_term_or_synonym(
                            " ".join(
                                [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
                            ).lower(),
                            term,
                        )
                        for term in sides.right_terms
                    )
                    for candidate in selected_candidates
                )
                supports_answer_type = left_text_match and right_text_match
                source_hint_match = supports_answer_type
        elif answer_type == "tool_command_concept":
            text = " ".join(
                " ".join([candidate.snippet.title, candidate.section, candidate.snippet.source, candidate.snippet.excerpt]).lower()
                for candidate in selected_candidates
            )
            supports_answer_type = "grcc" in text and ("compile" in text or "validation" in text)
            source_hint_match = "grcc" in text
        elif answer_type == "block_definition":
            catalog = [candidate for candidate in selected_candidates if candidate.source_type == "catalog"]
            if catalog:
                cleaned_summary = self._clean_catalog_summary_for_answer(
                    catalog[0].snippet.title,
                    catalog[0].snippet.excerpt,
                )
                summary = self._catalog_block_purpose_sentence(
                    catalog[0].snippet.title,
                    cleaned_summary,
                ).lower()
                supports_answer_type = bool(
                    summary
                    and "input port" not in summary
                    and "output port" not in summary
                    and "parameter" not in summary
                )
                source_hint_match = supports_answer_type

        quality = "medium"
        reason = "usable_evidence"
        if is_menu_or_boilerplate:
            quality = "weak"
            reason = "menu_or_boilerplate"
        elif not topic_match:
            quality = "weak"
            reason = "topic_mismatch"
        elif not required_terms_covered:
            quality = "weak"
            reason = "required_terms_missing"
        elif not supports_answer_type:
            quality = "weak"
            reason = "answer_type_unsupported_by_sources"
        elif top.quality_score >= 7.0:
            quality = "strong"
            reason = "high_confidence_ranked_sources"
        elif answer_type == "block_definition" and any(
            candidate.source_type == "catalog" for candidate in selected_candidates
        ):
            quality = "strong"
            reason = "catalog_block_evidence"

        return {
            "quality": quality,
            "reason": reason,
            "selected_source_count": len(selected_candidates),
            "topic_match": bool(topic_match),
            "required_terms_covered": bool(required_terms_covered),
            "source_hint_match": bool(source_hint_match),
            "is_menu_or_boilerplate": bool(is_menu_or_boilerplate),
            "supports_answer_type": bool(supports_answer_type),
        }

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
        if answer_type == "insufficient":
            return ("Local docs did not contain enough direct evidence for this question.", True)
        if not ranked_candidates:
            return ("Local docs did not contain enough direct evidence for this question.", True)
        subject = self._extract_docs_subject(question) or question
        subject_terms = tuple(self._docs_primary_terms(subject) or self._docs_topic_terms(subject))
        allow_procedural = answer_type == "procedural_how_to"

        if answer_type == "tool_command_concept":
            command_terms = ("grcc", "compile", "compiler", "validation")
            support = [
                candidate
                for candidate in ranked_candidates
                if "grcc"
                in " ".join(
                    [
                        candidate.snippet.title,
                        candidate.snippet.source,
                        candidate.section,
                        candidate.snippet.excerpt,
                    ]
                ).lower()
            ]
            if not support:
                return ("Local docs did not contain enough direct evidence for this question.", True)
            sentence = self._pick_typed_sentence(
                candidate=support[0],
                required_terms=command_terms,
                allow_procedural=False,
                min_term_hits=1,
            )
            if not sentence:
                return ("Local docs did not contain enough direct evidence for this question.", True)
            sentence_l = sentence.lower()
            if "?" in sentence or "how, where, when" in sentence_l:
                return ("Local docs did not contain enough direct evidence for this question.", True)
            return (f"According to local docs, {sentence}", False)

        if answer_type == "comparison":
            sides = self._extract_comparison_sides(question)
            if sides is None:
                return ("Local docs did not contain enough direct evidence for this question.", True)
            shared_terms = set(sides.left_terms).intersection(sides.right_terms)
            left_terms = tuple(term for term in sides.left_terms if term not in shared_terms) or sides.left_terms
            right_terms = tuple(term for term in sides.right_terms if term not in shared_terms) or sides.right_terms
            left_anchor_terms = tuple(
                self._docs_primary_terms(sides.left_label) or self._docs_topic_terms(sides.left_label)
            )
            right_anchor_terms = tuple(
                self._docs_primary_terms(sides.right_label) or self._docs_topic_terms(sides.right_label)
            )
            if (
                ("tags" in left_terms and "metadata" in right_terms)
                or ("metadata" in left_terms and "tags" in right_terms)
            ):
                return (
                    "Local docs did not contain enough direct evidence to compare tags and metadata clearly.",
                    True,
                )
            comparison_candidates = self._select_docs_candidates_for_answer_type(
                question=question,
                answer_type=answer_type,
                ranked_candidates=ranked_candidates,
                limit=min(8, max(2, len(ranked_candidates))),
            )
            combined = " ".join(
                " ".join(
                    [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
                ).lower()
                for candidate in comparison_candidates
            )
            left_exact = any(term in combined for term in left_terms if term)
            right_exact = any(term in combined for term in right_terms if term)
            if not (left_exact and right_exact):
                return (
                    "Local docs only provided one-sided evidence; not enough direct evidence to compare both sides.",
                    True,
                )
            left_sentence = ""
            right_sentence = ""
            for candidate in comparison_candidates:
                if not left_sentence:
                    left_sentence = self._pick_typed_sentence(
                        candidate=candidate,
                        required_terms=left_terms,
                        allow_procedural=allow_procedural,
                        min_term_hits=self._minimum_required_term_hits(left_terms),
                    )
                if not right_sentence:
                    right_sentence = self._pick_typed_sentence(
                        candidate=candidate,
                        required_terms=right_terms,
                        allow_procedural=allow_procedural,
                        min_term_hits=self._minimum_required_term_hits(right_terms),
                    )
                if left_sentence and right_sentence:
                    break
            if left_sentence and left_terms and not any(
                self._text_matches_term_or_synonym(left_sentence.lower(), term)
                for term in left_terms
            ):
                left_sentence = ""
            if right_sentence and right_terms and not any(
                self._text_matches_term_or_synonym(right_sentence.lower(), term)
                for term in right_terms
            ):
                right_sentence = ""
            if left_sentence and left_anchor_terms and not any(
                term in left_sentence.lower() for term in left_anchor_terms if len(term) > 2
            ):
                left_sentence = ""
            if right_sentence and right_anchor_terms and not any(
                term in right_sentence.lower() for term in right_anchor_terms if len(term) > 2
            ):
                right_sentence = ""
            if not left_sentence or not right_sentence:
                for candidate in comparison_candidates:
                    text = " ".join(
                        [candidate.snippet.title, candidate.section, candidate.snippet.excerpt]
                    ).lower()
                    candidate_sentences = self._sentence_list(candidate.snippet.excerpt)
                    if (not left_sentence) and any(
                        self._text_matches_term_or_synonym(text, term) for term in left_terms
                    ):
                        picked = ""
                        for sentence in candidate_sentences:
                            lower_sentence = sentence.lower()
                            if any(
                                self._text_matches_term_or_synonym(lower_sentence, term)
                                for term in left_terms
                            ):
                                picked = sentence
                                break
                        left_sentence = picked or (
                            candidate_sentences[0] if candidate_sentences else candidate.snippet.excerpt
                        )
                    if (not right_sentence) and any(
                        self._text_matches_term_or_synonym(text, term) for term in right_terms
                    ):
                        picked = ""
                        for sentence in candidate_sentences:
                            lower_sentence = sentence.lower()
                            if any(
                                self._text_matches_term_or_synonym(lower_sentence, term)
                                for term in right_terms
                            ):
                                picked = sentence
                                break
                        right_sentence = picked or (
                            candidate_sentences[0] if candidate_sentences else candidate.snippet.excerpt
                        )
                    if left_sentence and right_sentence:
                        break
            if left_sentence and left_anchor_terms and not any(
                term in left_sentence.lower() for term in left_anchor_terms if len(term) > 2
            ):
                left_sentence = ""
            if right_sentence and right_anchor_terms and not any(
                term in right_sentence.lower() for term in right_anchor_terms if len(term) > 2
            ):
                right_sentence = ""
            if not left_sentence or not right_sentence:
                missing = sides.left_label if not left_sentence else sides.right_label
                return (
                    f"Local docs only provided evidence for one side; not enough direct evidence to compare both ({missing} missing).",
                    True,
                )
            if any(
                marker in (left_sentence.lower() + " " + right_sentence.lower())
                for marker in ("gr::", "pmt::", "get_tags_in_range", "get_tags_in_window")
            ):
                return (
                    "Local docs only provided API-fragment evidence; not enough direct evidence to compare both sides clearly.",
                    True,
                )
            if "::" in left_sentence or "::" in right_sentence:
                return (
                    "Local docs only provided API-fragment evidence; not enough direct evidence to compare both sides clearly.",
                    True,
                )
            if left_sentence.lower() == right_sentence.lower():
                shared = left_sentence.lower()
                has_left = any(
                    self._text_matches_term_or_synonym(shared, term)
                    for term in left_terms
                )
                has_right = any(
                    self._text_matches_term_or_synonym(shared, term)
                    for term in right_terms
                )
                if not (has_left and has_right):
                    return (
                        "Local docs only provided overlapping evidence; not enough direct evidence to compare both sides distinctly.",
                        True,
                    )
            answer = (
                f"{sides.left_label}: {left_sentence} "
                f"{sides.right_label}: {right_sentence} "
                f"Difference: {sides.left_label} and {sides.right_label} are used for different roles in GNU Radio."
            )
            return (answer, False)

        if answer_type == "block_definition":
            if "hierarchical block" in question.lower():
                return ("Local docs did not contain enough direct evidence for this question.", True)
            catalog_candidates = [
                candidate
                for candidate in ranked_candidates
                if candidate.source_type == "catalog"
            ]
            if catalog_candidates:
                catalog = catalog_candidates[0]
                cleaned_summary = self._clean_catalog_summary_for_answer(
                    catalog.snippet.title,
                    catalog.snippet.excerpt,
                )
                summary = self._catalog_block_purpose_sentence(
                    catalog.snippet.title,
                    cleaned_summary,
                )
                summary_l = summary.lower()
                summary_terms = re.findall(r"[a-z0-9]+", summary_l)
                title_terms = set(re.findall(r"[a-z0-9]+", catalog.snippet.title.lower()))
                informative_terms = [term for term in summary_terms if term not in title_terms]
                if (
                    summary
                    and "input port" not in summary_l
                    and "output port" not in summary_l
                    and "parameter" not in summary_l
                    and len(summary) >= 20
                    and len(informative_terms) >= 2
                ):
                    if (
                        "relate to" in question.lower()
                        and catalog.snippet.excerpt.rstrip().endswith("…")
                        and summary == cleaned_summary
                    ):
                        return ("Local docs did not contain enough direct evidence for this question.", True)
                    return (
                        f"According to the local block catalog, {catalog.snippet.title} {summary}.",
                        False,
                    )
            required_terms = subject_terms or tuple(self._docs_primary_terms(question))
            subject_phrase = (
                (self._extract_block_definition_subject(question) or "").strip().lower()
            )
            for candidate in ranked_candidates[:6]:
                sentence = self._pick_typed_sentence(
                    candidate=candidate,
                    required_terms=required_terms,
                    allow_procedural=False,
                    min_term_hits=self._minimum_required_term_hits(required_terms),
                )
                if sentence and subject_phrase and " " in subject_phrase:
                    title_source = " ".join(
                        [candidate.snippet.title, candidate.snippet.source]
                    ).lower()
                    if subject_phrase not in title_source:
                        continue
                if sentence:
                    return (f"According to local docs, {sentence}", False)
            return ("Local docs did not contain enough direct evidence for this question.", True)

        required_terms = subject_terms or tuple(
            self._docs_primary_terms(question) or self._docs_topic_terms(question)
        )
        lower_question = question.lower()
        if "hierarchical block" in lower_question:
            return ("Local docs did not contain enough direct evidence for this question.", True)
        require_hier_source = "hierarchical block" in lower_question
        if "message ports" in lower_question:
            for candidate in ranked_candidates[:6]:
                sentence = self._pick_typed_sentence(
                    candidate=candidate,
                    required_terms=("message",),
                    allow_procedural=False,
                    min_term_hits=1,
                )
                if sentence and any(
                    marker in sentence.lower()
                    for marker in ("asynchronous", "control data", "between blocks")
                ):
                    return (f"According to local docs, {sentence}", False)
        min_hits = self._minimum_required_term_hits(required_terms)
        for candidate in ranked_candidates[:6]:
            if require_hier_source:
                title_source = " ".join([candidate.snippet.title, candidate.snippet.source]).lower()
                if "hier" not in title_source:
                    continue
            sentence = self._pick_typed_sentence(
                candidate=candidate,
                required_terms=required_terms,
                allow_procedural=allow_procedural,
                min_term_hits=min_hits,
            )
            if sentence:
                return (f"According to local docs, {sentence}", False)
        return ("Local docs did not contain enough direct evidence for this question.", True)

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
        if operation_kind is None:
            return None
        if operation_kind in {"clarify", "unsupported"}:
            return None

        if detach_connections is not None and not isinstance(detach_connections, bool):
            return self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "detach_connections must be boolean when provided.",
                },
            )
        if detach_connection_ids is not None:
            if not isinstance(detach_connection_ids, list):
                return self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": "detach_connection_ids must be an array of connection ids.",
                    },
                )
            for connection_id in detach_connection_ids:
                if not isinstance(connection_id, str) or not connection_id.strip():
                    return self._payload_result(
                        "change_graph",
                        {
                            "ok": False,
                            "dry_run": bool(dry_run),
                            "operation_kind": operation_kind,
                            "error_type": ErrorCode.INVALID_REQUEST,
                            "message": (
                                "detach_connection_ids entries must be non-empty connection id strings."
                            ),
                        },
                    )

        normalized_target_ref: dict[str, Any] | None = None
        if target_ref is not None:
            if not isinstance(target_ref, dict):
                return self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": "target_ref must be an object when provided.",
                    },
                )
            normalized_target_ref = {
                str(key): value for key, value in target_ref.items() if isinstance(key, str)
            }
            # Accept both wrapper-era (`uid`, `instance_name`) and guarded
            # transaction-era (`block_uid`, `expected_instance_name`) references.
            target_uid = normalized_target_ref.get("uid")
            if not (isinstance(target_uid, str) and target_uid.strip()):
                target_uid = normalized_target_ref.get("block_uid")
            target_instance = normalized_target_ref.get("instance_name")
            if not (isinstance(target_instance, str) and target_instance.strip()):
                target_instance = normalized_target_ref.get("expected_instance_name")
            if not (
                isinstance(target_uid, str)
                and target_uid.strip()
                or isinstance(target_instance, str)
                and target_instance.strip()
            ):
                return self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": (
                            "target_ref must include at least one non-empty identifier: "
                            "`uid` or `instance_name`."
                        ),
                    },
                )

        def _require(condition: bool, message: str) -> ToolResult | None:
            if condition:
                return None
            return self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": message,
                },
            )

        has_target = bool(
            normalized_target_ref
            or (isinstance(instance_name, str) and instance_name.strip())
        )

        if operation_kind == "set_param":
            missing = _require(
                has_target
                and isinstance(param_key, str)
                and param_key.strip()
                and param_value is not None,
                "set_param requires target_ref or instance_name plus param_key and param_value.",
            )
            return missing
        if operation_kind == "set_state":
            missing = _require(
                has_target and isinstance(state, str) and state in {"enabled", "disabled"},
                "set_state requires target_ref or instance_name plus state=enabled|disabled.",
            )
            return missing
        if operation_kind == "add_variable":
            missing = _require(
                isinstance(variable_name, str)
                and variable_name.strip()
                and variable_value is not None,
                "add_variable requires variable_name and variable_value.",
            )
            return missing
        if operation_kind == "disconnect":
            has_new_endpoints = any(
                value is not None and (not isinstance(value, str) or value.strip())
                for value in (
                    new_src_block,
                    new_src_port,
                    new_dst_block,
                    new_dst_port,
                )
            )
            if has_new_endpoints:
                return self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": (
                            "disconnect does not accept rewire fields. "
                            "Use operation_kind='rewire' for new endpoint arguments."
                        ),
                    },
                )
            has_endpoint_hint = any(
                value is not None and (not isinstance(value, str) or value.strip())
                for value in (src_block, src_port, dst_block, dst_port)
            )
            return _require(
                (isinstance(connection_id, str) and connection_id.strip()) or has_endpoint_hint,
                (
                    "disconnect requires connection_id or endpoint hints "
                    "(src_block/src_port/dst_block/dst_port)."
                ),
            )
        if operation_kind == "rewire":
            has_old_endpoint_hint = any(
                value is not None and (not isinstance(value, str) or value.strip())
                for value in (src_block, src_port, dst_block, dst_port)
            )
            has_new_source_hint = any(
                value is not None and (not isinstance(value, str) or value.strip())
                for value in (new_src_block, new_src_port)
            )
            has_new_destination_hint = any(
                value is not None and (not isinstance(value, str) or value.strip())
                for value in (new_dst_block, new_dst_port)
            )
            return _require(
                (
                    (isinstance(connection_id, str) and connection_id.strip())
                    or has_old_endpoint_hint
                )
                and has_new_source_hint
                and has_new_destination_hint,
                (
                    "rewire requires connection_id or old endpoint hints plus "
                    "exact new endpoints or bounded hints for both new source and new destination."
                ),
            )
        if operation_kind == "insert_block":
            normalized_block_id = (
                block_id.strip()
                if isinstance(block_id, str) and block_id.strip()
                else None
            )
            normalized_candidate_id = (
                candidate_id.strip()
                if isinstance(candidate_id, str) and candidate_id.strip()
                else None
            )
            if (
                normalized_block_id is not None
                and normalized_candidate_id is not None
                and normalized_block_id != normalized_candidate_id
            ):
                return self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": (
                            "insert_block received conflicting block_id and candidate_id. "
                            "Provide one catalog id or matching values for both."
                        ),
                    },
                )
            if insert_params is not None and not isinstance(insert_params, dict):
                return self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": "insert_params must be an object when provided.",
                    },
                )
            return _require(
                isinstance(connection_id, str)
                and connection_id.strip()
                and (
                    normalized_block_id is not None
                    or normalized_candidate_id is not None
                ),
                "insert_block requires connection_id and block_id (or candidate_id).",
            )
        if operation_kind == "remove_block":
            if detach_connections is not None and not isinstance(detach_connections, bool):
                return self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": "detach_connections must be boolean when provided.",
                    },
                )
            return _require(
                has_target,
                "remove_block requires instance_name or guarded target_ref.",
            )
        if detach_connections is not None or detach_connection_ids is not None:
            return self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": (
                        "detach_connections and detach_connection_ids are only supported for remove_block."
                    ),
                },
            )
        if operation_kind == "auto_insert":
            return _require(
                isinstance(connection_id, str) and connection_id.strip(),
                "auto_insert requires connection_id.",
            )
        if operation_kind in {"new_grc", "load_grc", "save_graph"}:
            return self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.UNSUPPORTED_OP,
                    "message": "change_graph is mutation-only. Use explicit lifecycle wrappers for save/load.",
                },
            )
        # Keep unused operation fields referenced so static checks do not regress silently.
        _ = (src_block, src_port, dst_block, dst_port)
        return None

    def _canonicalize_change_graph_target_ref(
        self,
        *,
        dry_run: bool,
        operation_kind: str | None,
        target_ref: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, ToolResult | None]:
        if target_ref is None:
            return None, None
        if not isinstance(target_ref, dict):
            return None, self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "target_ref must be an object when provided.",
                },
            )

        normalized = {str(key): value for key, value in target_ref.items() if isinstance(key, str)}
        alias_map = {
            "uid": "block_uid",
            "instance_name": "expected_instance_name",
            "block_type": "expected_block_type",
            "state_revision": "base_state_revision",
        }
        canonical_fields = (
            "block_uid",
            "expected_instance_name",
            "expected_block_type",
            "base_state_revision",
        )
        allowed_fields = set(canonical_fields) | set(alias_map.keys())
        unknown_fields = sorted(key for key in normalized if key not in allowed_fields)
        if unknown_fields:
            return None, self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": (
                        "target_ref contains unsupported keys: "
                        + ", ".join(unknown_fields)
                        + ". Allowed keys are guarded "
                        "(block_uid, expected_instance_name, expected_block_type, base_state_revision) "
                        "or wrapper-era aliases (uid, instance_name, block_type, state_revision)."
                    ),
                },
            )

        canonical: dict[str, Any] = {}
        for alias_key, canonical_key in alias_map.items():
            canonical_value = normalized.get(canonical_key)
            alias_value = normalized.get(alias_key)
            if canonical_value is not None and alias_value is not None and canonical_value != alias_value:
                return None, self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": (
                            f"target_ref has conflicting values for {canonical_key!r} and its "
                            f"alias {alias_key!r}."
                        ),
                    },
                )
            if canonical_value is not None:
                canonical[canonical_key] = canonical_value
            elif alias_value is not None:
                canonical[canonical_key] = alias_value

        missing_fields = [field for field in canonical_fields if field not in canonical]
        if missing_fields:
            return None, self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": (
                        "target_ref must include guarded fields "
                        "(block_uid, expected_instance_name, expected_block_type, base_state_revision). "
                        "Missing: " + ", ".join(missing_fields)
                    ),
                },
            )

        if not isinstance(canonical["block_uid"], str) or not canonical["block_uid"].strip():
            return None, self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "target_ref.block_uid must be a non-empty string.",
                },
            )
        if (
            not isinstance(canonical["expected_instance_name"], str)
            or not canonical["expected_instance_name"].strip()
        ):
            return None, self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "target_ref.expected_instance_name must be a non-empty string.",
                },
            )
        if (
            not isinstance(canonical["expected_block_type"], str)
            or not canonical["expected_block_type"].strip()
        ):
            return None, self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "target_ref.expected_block_type must be a non-empty string.",
                },
            )
        base_state_revision = canonical["base_state_revision"]
        if not isinstance(base_state_revision, int) or isinstance(base_state_revision, bool):
            return None, self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": "target_ref.base_state_revision must be an integer.",
                },
            )

        canonical["block_uid"] = canonical["block_uid"].strip()
        canonical["expected_instance_name"] = canonical["expected_instance_name"].strip()
        canonical["expected_block_type"] = canonical["expected_block_type"].strip()
        return canonical, None

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
        started = time.monotonic()
        before_revision = self.session.state_revision
        before_dirty = self.session.is_dirty
        def _block_names_snapshot() -> set[str]:
            flowgraph = self.session.flowgraph
            if flowgraph is None:
                return set()
            return {
                block.instance_name
                for block in flowgraph.blocks
                if isinstance(block.instance_name, str)
            }

        def _connection_ids_snapshot() -> set[str]:
            flowgraph = self.session.flowgraph
            if flowgraph is None:
                return set()
            return {
                render_connection_id(
                    conn.src_block,
                    conn.src_port,
                    conn.dst_block,
                    conn.dst_port,
                )
                for conn in flowgraph.connections
            }

        before_block_names = _block_names_snapshot()
        before_connection_ids = _connection_ids_snapshot()
        handlers: list[str] = []
        missing_session = self._missing_session_result("change_graph")
        if missing_session is not None:
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="missing_session",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=missing_session,
                validation_run=False,
                output_truncated=False,
            )
        if not isinstance(user_goal, str) or not user_goal.strip():
            result = self._tool_result(
                "change_graph",
                ok=False,
                message="user_goal must be non-empty.",
                error_type=ErrorCode.INVALID_REQUEST,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="invalid_request",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
        allowed_operation_kinds = {
            "set_param",
            "set_state",
            "add_variable",
            "disconnect",
            "rewire",
            "insert_block",
            "remove_block",
            "auto_insert",
            "clarify",
            "unsupported",
        }
        resolved_operation_kind = (
            operation_kind.strip() if isinstance(operation_kind, str) else None
        )
        if resolved_operation_kind == "":
            resolved_operation_kind = None
        if (
            resolved_operation_kind is not None
            and resolved_operation_kind not in allowed_operation_kinds
        ):
            result = self._tool_result(
                "change_graph",
                ok=False,
                message=f"Unsupported change_graph operation_kind: {resolved_operation_kind!r}",
                error_type=ErrorCode.INVALID_REQUEST,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="invalid_operation_kind",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
        if resolved_operation_kind == "insert_block":
            if isinstance(insert_params, dict):
                normalized_insert_params: dict[str, Any] = {}
                for key, value in insert_params.items():
                    key_text = str(key).strip()
                    if not key_text:
                        continue
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        normalized_insert_params[key_text] = str(value)
                    else:
                        normalized_insert_params[key_text] = value
                insert_params = normalized_insert_params
            if (
                not (isinstance(block_id, str) and block_id.strip())
                and not (isinstance(candidate_id, str) and candidate_id.strip())
                and isinstance(insert_block, str)
                and insert_block.strip()
            ):
                candidate_id = insert_block.strip()
            if (
                not (isinstance(block_id, str) and block_id.strip())
                and not (isinstance(candidate_id, str) and candidate_id.strip())
                and isinstance(new_dst_block, str)
                and new_dst_block.strip()
            ):
                candidate_id = new_dst_block.strip()
                new_dst_block = None

            if isinstance(connection_id, str) and connection_id.strip():
                normalized_connection_id = connection_id.strip()
                if parse_connection_id(normalized_connection_id) is None and "->" in normalized_connection_id:
                    left, right = normalized_connection_id.split("->", 1)
                    src_block = ""
                    src_port_value: int | str | None = None
                    if ":" in left:
                        src_block, src_port_token = left.split(":", 1)
                        src_block = src_block.strip()
                        src_port_token = src_port_token.strip()
                        if src_port_token:
                            if src_port_token.isdigit():
                                src_port_value = int(src_port_token)
                            else:
                                src_port_value = src_port_token
                    dst_block_hint = right.strip()
                    if src_block and src_port_value is not None and dst_block_hint and ":" not in dst_block_hint:
                        candidates = self.session.find_connection_candidates(
                            src_block=src_block,
                            src_port=src_port_value,
                            dst_block=dst_block_hint,
                            dst_port=None,
                        )
                        if len(candidates) == 1:
                            candidate = candidates[0]
                            connection_id = render_connection_id(
                                candidate.src_block,
                                candidate.src_port,
                                candidate.dst_block,
                                candidate.dst_port,
                            )
        canonical_target_ref, target_ref_error = self._canonicalize_change_graph_target_ref(
            dry_run=bool(dry_run),
            operation_kind=resolved_operation_kind,
            target_ref=target_ref,
        )
        if target_ref_error is not None:
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="invalid_operation_args",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=target_ref_error,
                validation_run=False,
                output_truncated=False,
            )
        target_ref = canonical_target_ref
        operation_args_error = self._validate_change_graph_operation_args(
            dry_run=bool(dry_run),
            operation_kind=resolved_operation_kind,
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
        if operation_args_error is not None:
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="invalid_operation_args",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=operation_args_error,
                validation_run=False,
                output_truncated=False,
            )
        if state_revision is not None:
            if not isinstance(state_revision, int) or isinstance(state_revision, bool):
                stale_result = self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": resolved_operation_kind,
                        "error_type": ErrorCode.INVALID_REQUEST,
                        "message": "state_revision must be an integer when provided.",
                    },
                )
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="invalid_operation_args",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=stale_result,
                    validation_run=False,
                    output_truncated=False,
                )
            if state_revision != self.session.state_revision:
                stale_result = self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": resolved_operation_kind,
                        "error_type": ErrorCode.STALE_REVISION,
                        "message": (
                            "state_revision is stale for the current graph. "
                            f"Provided {state_revision}, current is {self.session.state_revision}."
                        ),
                        "state_revision": self.session.state_revision,
                    },
                )
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="stale_revision",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=stale_result,
                    validation_run=False,
                    output_truncated=False,
                )
        if isinstance(target_ref, dict):
            target_ref_revision = target_ref.get("base_state_revision")
            if isinstance(target_ref_revision, int) and target_ref_revision != self.session.state_revision:
                stale_result = self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "operation_kind": resolved_operation_kind,
                        "error_type": ErrorCode.STALE_REVISION,
                        "message": (
                            "target_ref.base_state_revision is stale for the current graph. "
                            f"Provided {target_ref_revision}, current is {self.session.state_revision}."
                        ),
                        "state_revision": self.session.state_revision,
                    },
                )
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="stale_revision",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=stale_result,
                    validation_run=False,
                    output_truncated=False,
                )
        if resolved_operation_kind == "rewire" and state_revision is None:
            stale_result = self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": resolved_operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": (
                        "rewire requires state_revision to guard against stale-edge execution. "
                        "Provide the current active state_revision from inspect_graph."
                    ),
                },
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="invalid_operation_args",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=stale_result,
                validation_run=False,
                output_truncated=False,
            )
        lower_goal = user_goal.lower()
        if not dry_run and resolved_operation_kind is None:
            has_structured_mutation_args = any(
                (
                    isinstance(target_ref, dict),
                    isinstance(block_id, str) and bool(block_id.strip()),
                    isinstance(instance_name, str) and bool(instance_name.strip()),
                    isinstance(connection_id, str) and bool(connection_id.strip()),
                    isinstance(src_block, str) and bool(src_block.strip()),
                    src_port is not None,
                    isinstance(dst_block, str) and bool(dst_block.strip()),
                    dst_port is not None,
                    isinstance(new_src_block, str) and bool(new_src_block.strip()),
                    new_src_port is not None,
                    isinstance(new_dst_block, str) and bool(new_dst_block.strip()),
                    new_dst_port is not None,
                    isinstance(param_key, str) and bool(param_key.strip()),
                    param_value is not None,
                    isinstance(state, str) and bool(state.strip()),
                    isinstance(variable_name, str) and bool(variable_name.strip()),
                    variable_value is not None,
                )
            )
            if has_structured_mutation_args:
                result = self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "error_type": "clarification_required",
                        "message": (
                            "Committed mutation requires operation_kind. "
                            "Provide one of: set_param, set_state, add_variable, "
                            "disconnect, rewire, insert_block, remove_block, auto_insert."
                        ),
                        "clarification_options": [
                            "Retry with operation_kind set to the intended mutation class.",
                            "Or set dry_run=true for a preview-only request.",
                        ],
                    },
                )
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="missing_operation_kind",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=result,
                    validation_run=False,
                    output_truncated=False,
                )
        if any(token in lower_goal for token in ("yaml", "undo", "redo", "export python", "source text")):
            resolved_operation_kind = resolved_operation_kind or "unsupported"
        if resolved_operation_kind == "unsupported":
            result = self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "error_type": ErrorCode.UNSUPPORTED_OP,
                    "message": "Unsupported workflow for change_graph.",
                },
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="unsupported",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
        if resolved_operation_kind == "clarify":
            result = self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": resolved_operation_kind,
                    "error_type": "clarification_required",
                    "message": "Clarification required before changing the graph.",
                    "clarification_options": [
                        "Provide exact instance_name + param_key + param_value for param edits.",
                        "Provide exact connection_id (preferred) or endpoint hints for disconnect.",
                        "Provide exact block_id and placement details for inserts.",
                    ],
                },
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="clarify",
                internal_handlers=["clarification"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
        if "save" in lower_goal or "write out" in lower_goal:
            result = self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "error_type": ErrorCode.UNSUPPORTED_OP,
                    "message": (
                        "change_graph is mutation-only. Use save_graph_explicit for "
                        "explicit lifecycle save requests."
                    ),
                },
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action="unsupported",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )

        tx_tool = self._propose_edit if dry_run else self._apply_edit
        operation_summary = "clarification_required"
        result: dict[str, Any]

        def _kind_allows(*allowed: str) -> bool:
            return resolved_operation_kind is None or resolved_operation_kind in allowed

        def _kind_mismatch_result(*allowed: str) -> ToolResult | None:
            if _kind_allows(*allowed):
                return None
            return self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "operation_kind": resolved_operation_kind,
                    "error_type": ErrorCode.INVALID_REQUEST,
                    "message": (
                        f"operation_kind={resolved_operation_kind!r} does not match "
                        f"the supplied arguments; expected one of {sorted(allowed)!r}."
                    ),
                },
            )

        def _build_remove_block_operation(*, resolved_instance_name: str) -> dict[str, Any]:
            operation: dict[str, Any] = {
                "op_type": "remove_block",
                "instance_name": resolved_instance_name,
            }
            if isinstance(target_ref, dict):
                operation["target_ref"] = target_ref
            return operation

        def _resolve_remove_block_target_name() -> str | None:
            if isinstance(target_ref, dict):
                resolved = self.session.resolve_block_reference(
                    instance_name=target_ref.get("expected_instance_name"),
                    block_uid=target_ref.get("block_uid"),
                    block_type=target_ref.get("expected_block_type"),
                )
                candidates = resolved.get("candidates") if isinstance(resolved, dict) else None
                if isinstance(candidates, list) and len(candidates) == 1:
                    candidate = candidates[0]
                    if isinstance(candidate, dict):
                        name = candidate.get("name")
                        if isinstance(name, str) and name.strip():
                            return name.strip()
                return None
            if isinstance(instance_name, str) and instance_name.strip():
                resolved = self.session.resolve_block_reference(instance_name=instance_name.strip())
                candidates = resolved.get("candidates") if isinstance(resolved, dict) else None
                if isinstance(candidates, list) and len(candidates) == 1:
                    candidate = candidates[0]
                    if isinstance(candidate, dict):
                        name = candidate.get("name")
                        if isinstance(name, str) and name.strip():
                            return name.strip()
            return None

        def _attached_connection_ids_for_block(block_name: str) -> list[str]:
            flowgraph = self.session.flowgraph
            if flowgraph is None:
                return []
            attached: list[str] = []
            for connection in flowgraph.connections:
                if connection.src_block != block_name and connection.dst_block != block_name:
                    continue
                attached.append(
                    render_connection_id(
                        connection.src_block,
                        connection.src_port,
                        connection.dst_block,
                        connection.dst_port,
                    )
                )
            return sorted(attached)

        def _normalize_connection_id_list(values: list[str] | None) -> list[str]:
            if not isinstance(values, list):
                return []
            normalized: list[str] = []
            for value in values:
                if not isinstance(value, str):
                    continue
                token = value.strip()
                if token:
                    normalized.append(token)
            return sorted(dict.fromkeys(normalized))

        resolved_insert_block_id: str | None = None
        if isinstance(block_id, str) and block_id.strip():
            resolved_insert_block_id = block_id.strip()
        elif isinstance(candidate_id, str) and candidate_id.strip():
            resolved_insert_block_id = candidate_id.strip()

        rewire_old_hint = any(
            value is not None and (not isinstance(value, str) or value.strip())
            for value in (src_block, src_port, dst_block, dst_port)
        )
        if resolved_operation_kind == "rewire" and (
            (isinstance(connection_id, str) and connection_id.strip()) or rewire_old_hint
        ):
            operation_summary = "rewire_connection"
            mismatch = _kind_mismatch_result("rewire")
            if mismatch is not None:
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="operation_kind_mismatch",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=mismatch,
                    validation_run=False,
                    output_truncated=False,
                )
            handlers.append("rewire_connection")
            result = self._rewire_connection(
                old_connection_id=connection_id.strip()
                if isinstance(connection_id, str) and connection_id.strip()
                else None,
                old_src_block=src_block,
                old_src_port=src_port,
                old_dst_block=dst_block,
                old_dst_port=dst_port,
                new_src_block=new_src_block,
                new_src_port=new_src_port,
                new_dst_block=new_dst_block,
                new_dst_port=new_dst_port,
                dry_run=bool(dry_run),
            )
        elif (
            resolved_insert_block_id is not None
            and isinstance(connection_id, str)
            and connection_id.strip()
        ):
            operation_summary = "insert_block_on_connection"
            mismatch = _kind_mismatch_result("insert_block")
            if mismatch is not None:
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="operation_kind_mismatch",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=mismatch,
                    validation_run=False,
                    output_truncated=False,
                )
            if dry_run:
                handlers.append("propose_edit(insert_block_on_connection)")
                result = tx_tool(
                    {
                        "op_type": "insert_block_on_connection",
                        "connection_id": connection_id.strip(),
                        "block_type": resolved_insert_block_id,
                        "instance_name": instance_name or f"{resolved_insert_block_id}_0",
                        "params": insert_params or {},
                    }
                )
            else:
                handlers.append("insert_block_on_connection")
                result = self._insert_block_on_connection(
                    connection_id=connection_id.strip(),
                    block_type=resolved_insert_block_id,
                    instance_name=instance_name or f"{resolved_insert_block_id}_0",
                    params=insert_params or {},
                )
        elif isinstance(connection_id, str) and connection_id.strip():
            insertion_words = ("insert", "add", "compatible")
            if resolved_operation_kind == "auto_insert" or (
                resolved_operation_kind is None
                and any(word in lower_goal for word in insertion_words)
            ):
                operation_summary = "auto_insert_block"
                if dry_run:
                    handlers.append("suggest_compatible_insertions")
                    result = self._suggest_compatible_insertions(connection_id=connection_id.strip())
                else:
                    handlers.append("auto_insert_block")
                    result = self._auto_insert_block(
                        goal=user_goal,
                        preferred_block_type=block_id,
                        target_hint=connection_id.strip(),
                    )
            else:
                operation_summary = "remove_connection"
                mismatch = _kind_mismatch_result("disconnect")
                if mismatch is not None:
                    return self._attach_wrapper_dispatch_telemetry(
                        debug=debug,
                        wrapper_name="change_graph",
                        wrapper_action="operation_kind_mismatch",
                        internal_handlers=["none"],
                        started=started,
                        before_revision=before_revision,
                        before_dirty=before_dirty,
                        result=mismatch,
                        validation_run=False,
                        output_truncated=False,
                    )
                if dry_run:
                    handlers.append("propose_edit(remove_connection)")
                    remove_operation: dict[str, Any] = {
                        "op_type": "remove_connection",
                        "connection_id": connection_id.strip(),
                    }
                    result = tx_tool(remove_operation)
                else:
                    handlers.append("remove_connection")
                    result = self._remove_connection(connection_id=connection_id.strip())
        elif (
            resolved_operation_kind == "disconnect"
            and any(
                value is not None and (not isinstance(value, str) or value.strip())
                for value in (src_block, src_port, dst_block, dst_port)
            )
        ):
            operation_summary = "remove_connection"
            if dry_run:
                handlers.append("propose_edit(remove_connection)")
                result = tx_tool(
                    {
                        "op_type": "remove_connection",
                        "src_block": src_block,
                        "src_port": src_port,
                        "dst_block": dst_block,
                        "dst_port": dst_port,
                    }
                )
            else:
                handlers.append("remove_connection")
                result = self._remove_connection(
                    src_block=src_block,
                    src_port=src_port,
                    dst_block=dst_block,
                    dst_port=dst_port,
                )
        elif isinstance(variable_name, str) and variable_name.strip() and variable_value is not None:
            operation_summary = "add_variable"
            mismatch = _kind_mismatch_result("add_variable")
            if mismatch is not None:
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="operation_kind_mismatch",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=mismatch,
                    validation_run=False,
                    output_truncated=False,
                )
            handlers.append("propose_edit" if dry_run else "apply_edit")
            result = tx_tool(
                {
                    "op_type": "add_block",
                    "block_type": "variable",
                    "instance_name": variable_name.strip(),
                    "parameters": {"value": variable_value},
                }
            )
        elif isinstance(param_key, str) and param_key.strip() and param_value is not None:
            operation_summary = "update_params"
            mismatch = _kind_mismatch_result("set_param")
            if mismatch is not None:
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="operation_kind_mismatch",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=mismatch,
                    validation_run=False,
                    output_truncated=False,
                )
            handlers.append("propose_edit" if dry_run else "apply_edit")
            if isinstance(target_ref, dict):
                result = tx_tool(
                    {
                        "op_type": "update_params",
                        "target_ref": target_ref,
                        "params": {param_key.strip(): param_value},
                    }
                )
            elif isinstance(instance_name, str) and instance_name.strip():
                result = tx_tool(
                    {
                        "op_type": "update_params",
                        "instance_name": instance_name.strip(),
                        "params": {param_key.strip(): param_value},
                    }
                )
            else:
                result = self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "error_type": "clarification_required",
                        "message": "Missing target block for parameter update.",
                        "clarification_options": [
                            "Provide exact instance_name.",
                            "Or provide guarded target_ref from a prior clarification.",
                        ],
                    },
                )
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action=operation_summary,
                    internal_handlers=handlers,
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=result,
                    validation_run=False,
                    output_truncated=False,
                )
        elif isinstance(state, str) and state in {"enabled", "disabled"}:
            operation_summary = "update_states"
            mismatch = _kind_mismatch_result("set_state")
            if mismatch is not None:
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="operation_kind_mismatch",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=mismatch,
                    validation_run=False,
                    output_truncated=False,
                )
            handlers.append("propose_edit" if dry_run else "apply_edit")
            if isinstance(target_ref, dict):
                result = tx_tool({"op_type": "update_states", "target_ref": target_ref, "state": state})
            elif isinstance(instance_name, str) and instance_name.strip():
                result = tx_tool(
                    {
                        "op_type": "update_states",
                        "instance_name": instance_name.strip(),
                        "state": state,
                    }
                )
            else:
                result = self._payload_result(
                    "change_graph",
                    {
                        "ok": False,
                        "dry_run": bool(dry_run),
                        "error_type": "clarification_required",
                        "message": "Missing target block for state update.",
                        "clarification_options": ["Provide exact instance_name."],
                    },
                )
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action=operation_summary,
                    internal_handlers=handlers,
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=result,
                    validation_run=False,
                    output_truncated=False,
                )
        elif (
            resolved_operation_kind == "remove_block"
            and (
                isinstance(instance_name, str) and instance_name.strip()
                or isinstance(target_ref, dict)
            )
        ):
            operation_summary = "remove_block"
            mismatch = _kind_mismatch_result("remove_block")
            if mismatch is not None:
                return self._attach_wrapper_dispatch_telemetry(
                    debug=debug,
                    wrapper_name="change_graph",
                    wrapper_action="operation_kind_mismatch",
                    internal_handlers=["none"],
                    started=started,
                    before_revision=before_revision,
                    before_dirty=before_dirty,
                    result=mismatch,
                    validation_run=False,
                    output_truncated=False,
                )
            resolved_target_name = _resolve_remove_block_target_name()
            fallback_target_name: str = ""
            if isinstance(instance_name, str) and instance_name.strip():
                fallback_target_name = instance_name.strip()
            elif isinstance(target_ref, dict):
                expected_name = target_ref.get("expected_instance_name")
                if isinstance(expected_name, str) and expected_name.strip():
                    fallback_target_name = expected_name.strip()
            explicit_remove_operation = _build_remove_block_operation(
                resolved_instance_name=resolved_target_name or fallback_target_name
            )
            if resolved_target_name is not None:
                attached_connection_ids = _attached_connection_ids_for_block(resolved_target_name)
                if attached_connection_ids:
                    provided_detach_ids = _normalize_connection_id_list(detach_connection_ids)
                    explicit_detach_requested = bool(detach_connections) or (
                        bool(provided_detach_ids)
                        and provided_detach_ids == attached_connection_ids
                    )
                    if not explicit_detach_requested:
                        clarification_options = [
                            (
                                "Preview exact detach+remove plan by retrying change_graph "
                                "with operation_kind='remove_block', dry_run=true, "
                                "detach_connections=true, and the same target."
                            ),
                            (
                                "Commit exact detach+remove by retrying with "
                                "dry_run=false and detach_connections=true."
                            ),
                        ]
                        if attached_connection_ids:
                            clarification_options.append(
                                "Attached connections: " + ", ".join(attached_connection_ids)
                            )
                        result = self._payload_result(
                            "change_graph",
                            {
                                "ok": False,
                                "dry_run": bool(dry_run),
                                "operation_kind": resolved_operation_kind,
                                "error_type": "clarification_required",
                                "message": (
                                    f"Block '{resolved_target_name}' is connected. "
                                    "Explicit detach confirmation is required before remove_block commit."
                                ),
                                "clarification_options": clarification_options,
                                "attached_connection_ids": attached_connection_ids,
                                "planned_operations": [
                                    {"op_type": "remove_connection", "connection_id": connection_id}
                                    for connection_id in attached_connection_ids
                                ]
                                + [explicit_remove_operation],
                                "state_revision": self.session.state_revision,
                            },
                        )
                        return self._attach_wrapper_dispatch_telemetry(
                            debug=debug,
                            wrapper_name="change_graph",
                            wrapper_action=operation_summary,
                            internal_handlers=["clarification"],
                            started=started,
                            before_revision=before_revision,
                            before_dirty=before_dirty,
                            result=result,
                            validation_run=False,
                            output_truncated=False,
                        )

                    if provided_detach_ids and provided_detach_ids != attached_connection_ids:
                        result = self._payload_result(
                            "change_graph",
                            {
                                "ok": False,
                                "dry_run": bool(dry_run),
                                "operation_kind": resolved_operation_kind,
                                "error_type": ErrorCode.INVALID_REQUEST,
                                "message": (
                                    "detach_connection_ids do not match the current attached "
                                    "connections for this block."
                                ),
                                "attached_connection_ids": attached_connection_ids,
                            },
                        )
                        return self._attach_wrapper_dispatch_telemetry(
                            debug=debug,
                            wrapper_name="change_graph",
                            wrapper_action="invalid_operation_args",
                            internal_handlers=["none"],
                            started=started,
                            before_revision=before_revision,
                            before_dirty=before_dirty,
                            result=result,
                            validation_run=False,
                            output_truncated=False,
                        )

                    ordered_operations = [
                        {"op_type": "remove_connection", "connection_id": connection_id}
                        for connection_id in attached_connection_ids
                    ]
                    ordered_operations.append(explicit_remove_operation)
                    handlers.append("propose_edit" if dry_run else "apply_edit")
                    result = tx_tool(ordered_operations)
                else:
                    handlers.append("propose_edit" if dry_run else "apply_edit")
                    result = tx_tool(explicit_remove_operation)
            else:
                handlers.append("propose_edit" if dry_run else "apply_edit")
                result = tx_tool(explicit_remove_operation)
        else:
            wrapper_result = self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "error_type": "clarification_required",
                    "message": "Not enough exact change details to execute safely.",
                    "clarification_options": [
                        "Provide exact instance_name + param_key + param_value for param edits.",
                        "Provide exact connection_id (preferred) or endpoint hints for disconnect.",
                        "Provide exact rewire endpoints for rewiring.",
                    ],
                },
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="change_graph",
                wrapper_action=operation_summary,
                internal_handlers=handlers or ["clarification"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=wrapper_result,
                validation_run=False,
                output_truncated=False,
            )

        validation_result = None
        if isinstance(result, dict):
            validation_result = result.get("validation")
        graph_delta = result.get("graph_delta") if isinstance(result, dict) else None
        if (
            graph_delta is None
            and isinstance(result, dict)
            and bool(result.get("ok"))
            and not bool(dry_run)
        ):
            after_block_names = _block_names_snapshot()
            after_connection_ids = _connection_ids_snapshot()
            synthesized_delta: dict[str, Any] = {}
            added_blocks = sorted(after_block_names - before_block_names)
            removed_blocks = sorted(before_block_names - after_block_names)
            added_connections = sorted(after_connection_ids - before_connection_ids)
            removed_connections = sorted(before_connection_ids - after_connection_ids)
            if added_blocks:
                synthesized_delta["added_blocks"] = added_blocks
            if removed_blocks:
                synthesized_delta["removed_blocks"] = removed_blocks
            if added_connections:
                synthesized_delta["added_connections"] = added_connections
            if removed_connections:
                synthesized_delta["removed_connections"] = removed_connections
            synthesized_delta["dirty"] = bool(self.session.is_dirty)
            if isinstance(validation_result, dict):
                status = validation_result.get("status")
                returncode = validation_result.get("returncode")
                if status is not None:
                    synthesized_delta["validation_status"] = status
                if returncode is not None:
                    synthesized_delta["validation_returncode"] = returncode
            graph_delta = synthesized_delta
        payload: dict[str, Any] = {
            "ok": bool(result.get("ok")) if isinstance(result, dict) else False,
            "dry_run": bool(dry_run),
            "operation_kind": resolved_operation_kind,
            "operation_summary": operation_summary,
            "graph_delta": graph_delta,
            "validation_result": validation_result,
            "checkpoint_id": result.get("checkpoint_id") if isinstance(result, dict) else None,
            "message": result.get("message") if isinstance(result, dict) else "change_graph failed",
        }
        if isinstance(result, dict) and result.get("error_type"):
            payload["error_type"] = result.get("error_type")
        if isinstance(result, dict) and result.get("clarification_required"):
            payload["clarification_options"] = result.get("options")
        if isinstance(result, dict):
            normalized_operations = result.get("normalized_operations")
            if isinstance(normalized_operations, list):
                payload["planned_operations"] = copy.deepcopy(normalized_operations)
            errors = result.get("errors")
            if isinstance(errors, list):
                payload["errors"] = copy.deepcopy(errors)
            hint = result.get("hint")
            if isinstance(hint, str) and hint:
                payload["hint"] = hint
        wrapper_result = self._payload_result("change_graph", payload)
        validation_run = bool(validation_result) or operation_summary in {"update_params", "update_states", "rewire_connection", "insert_block_on_connection", "add_variable"}
        return self._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="change_graph",
            wrapper_action=operation_summary,
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=wrapper_result,
            validation_run=validation_run,
            output_truncated=False,
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
        started = time.monotonic()
        before_revision = self.session.state_revision
        before_dirty = self.session.is_dirty
        handlers: list[str] = []
        missing_session = self._missing_session_result("save_graph_explicit")
        if missing_session is not None:
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="save_graph_explicit",
                wrapper_action="missing_session",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=missing_session,
                validation_run=False,
                output_truncated=False,
            )
        if not self._has_explicit_save_intent():
            result = self._tool_result(
                "save_graph_explicit",
                ok=False,
                message=(
                    "Explicit save intent is required. Use clear save wording like "
                    "'save', 'persist', or 'write a copy'."
                ),
                error_type=ErrorCode.INVALID_REQUEST,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="save_graph_explicit",
                wrapper_action="intent_required",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )

        explicit_path = isinstance(path, str) and bool(path.strip())
        target_path = (
            Path(path).expanduser()
            if explicit_path
            else self.session.path
        )
        if target_path is None:
            result = self._tool_result(
                "save_graph_explicit",
                ok=False,
                message="This graph has no file path yet. Provide `path` for explicit save/copy.",
                error_type="SAVE_PATH_REQUIRED",
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="save_graph_explicit",
                wrapper_action="missing_path",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )

        resolved_target = target_path.resolve(strict=False)
        unsafe_root = self._unsafe_graph_root_for_path(resolved_target)
        if unsafe_root is not None:
            result = self._tool_result(
                "save_graph_explicit",
                ok=False,
                message=(
                    "Refusing to write to protected canonical/example graph paths. "
                    f"Choose a copied working path outside {unsafe_root}."
                ),
                error_type=ErrorCode.SAVE_REFUSED,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="save_graph_explicit",
                wrapper_action="unsafe_path",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )

        current_path = (
            self.session.path.resolve(strict=False)
            if self.session.path is not None
            else None
        )
        explicit_target_path = explicit_path and path is not None
        target_exists = resolved_target.exists()
        if (
            explicit_target_path
            and target_exists
            and current_path is not None
            and resolved_target != current_path
            and not overwrite
        ):
            result = self._tool_result(
                "save_graph_explicit",
                ok=False,
                message=(
                    "Refusing to overwrite existing destination without explicit overwrite permission. "
                    "Set overwrite=true for that destination."
                ),
                error_type=ErrorCode.SAVE_REFUSED,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="save_graph_explicit",
                wrapper_action="overwrite_refused",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )

        handlers.append("validate_graph")
        validation = self._validate_graph()
        validation_result = {
            "valid": bool(validation.get("valid")),
            "returncode": validation.get("returncode"),
            "stderr": validation.get("stderr"),
        }
        if validation.get("ok") is not True:
            result = self._payload_result(
                "save_graph_explicit",
                {
                    "ok": False,
                    "message": validation.get(
                        "message", "Graph validation failed before save."
                    ),
                    "error_type": validation.get("error_type", ErrorCode.VALIDATION_ERROR),
                    "path": str(resolved_target),
                    "dirty_before": bool(before_dirty),
                    "dirty_after": bool(self.session.is_dirty),
                    "validation_result": validation_result,
                },
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="save_graph_explicit",
                wrapper_action="validation_failed",
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=True,
                output_truncated=False,
            )
        if not bool(validation.get("valid")):
            result = self._payload_result(
                "save_graph_explicit",
                {
                    "ok": False,
                    "message": "Refusing to save invalid graph state.",
                    "error_type": ErrorCode.SAVE_REFUSED,
                    "path": str(resolved_target),
                    "dirty_before": bool(before_dirty),
                    "dirty_after": bool(self.session.is_dirty),
                    "validation_result": validation_result,
                },
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="save_graph_explicit",
                wrapper_action="invalid_graph",
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=True,
                output_truncated=False,
            )

        handlers.append("save_graph")
        save_result = self._save_graph(str(resolved_target) if explicit_target_path else None)
        payload = {
            "ok": bool(save_result.get("ok")),
            "message": save_result.get("message", "Save failed."),
            "error_type": save_result.get("error_type"),
            "path": save_result.get("path", str(resolved_target)),
            "dirty_before": bool(before_dirty),
            "dirty_after": bool(self.session.is_dirty),
            "validation_result": validation_result,
        }
        wrapper_result = self._payload_result("save_graph_explicit", payload)
        return self._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="save_graph_explicit",
            wrapper_action="save",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=wrapper_result,
            validation_run=True,
            output_truncated=False,
        )

    def _load_graph_explicit(
        self,
        path: str,
        debug: bool = False,
    ) -> ToolResult:
        started = time.monotonic()
        before_revision = self.session.state_revision
        before_dirty = self.session.is_dirty
        handlers: list[str] = []
        if not self._has_explicit_load_intent():
            result = self._tool_result(
                "load_graph_explicit",
                ok=False,
                message=(
                    "Explicit load intent is required. Use clear load wording like "
                    "'load', 'open', or 'switch to'."
                ),
                error_type=ErrorCode.INVALID_REQUEST,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="load_graph_explicit",
                wrapper_action="intent_required",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
        if not isinstance(path, str) or not path.strip():
            result = self._tool_result(
                "load_graph_explicit",
                ok=False,
                message="load_graph_explicit requires non-empty `path`.",
                error_type=ErrorCode.INVALID_REQUEST,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="load_graph_explicit",
                wrapper_action="missing_path",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )

        resolved_path = Path(path).expanduser().resolve(strict=False)
        unsafe_root = self._unsafe_graph_root_for_path(resolved_path)
        if unsafe_root is not None:
            result = self._tool_result(
                "load_graph_explicit",
                ok=False,
                message=(
                    "Refusing to load protected canonical/example graph directly for mutation. "
                    f"Copy it to a working path outside {unsafe_root} and load the copy."
                ),
                error_type=ErrorCode.FILE_LOAD_ERROR,
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="load_graph_explicit",
                wrapper_action="unsafe_path",
                internal_handlers=["none"],
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )

        handlers.append("load_grc")
        loaded = load_grc(str(resolved_path))
        if not isinstance(loaded, FlowgraphSession):
            result = self._tool_result(
                "load_graph_explicit",
                ok=False,
                message=loaded.get("message", "Failed to load .grc file."),
                error_type=loaded.get("error_type", ErrorCode.FILE_LOAD_ERROR),
            )
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="load_graph_explicit",
                wrapper_action="load_failed",
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )

        self._replace_session(loaded, reason="load_graph_explicit")
        handlers.append("validate_graph")
        validation = self._validate_graph()
        summary_payload = summarize_graph(self.session)
        validation_result = {
            "valid": bool(validation.get("valid")),
            "returncode": validation.get("returncode"),
            "stderr": validation.get("stderr"),
        }
        valid_graph = bool(validation.get("ok")) and bool(validation.get("valid"))
        payload: dict[str, Any] = {
            "ok": valid_graph,
            "path": str(self.session.path) if self.session.path is not None else str(resolved_path),
            "state_revision": self.session.state_revision,
            "message": (
                "Graph loaded and validated."
                if valid_graph
                else "Graph loaded, but validation failed for the loaded state."
            ),
            "valid": bool(validation.get("valid")),
            "validation_result": validation_result,
            "graph_summary": summary_payload.get("summary"),
            "block_count": summary_payload.get("block_count"),
            "connection_count": summary_payload.get("connection_count"),
            "dirty": self.session.is_dirty,
        }
        if not valid_graph:
            payload["error_type"] = (
                validation.get("error_type")
                if validation.get("ok") is False
                else ErrorCode.GNU_VALIDATION_FAILED
            )
        result = self._payload_result("load_graph_explicit", payload)
        return self._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="load_graph_explicit",
            wrapper_action="load",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=True,
            output_truncated=False,
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
