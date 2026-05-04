"""Thin runtime wrapper for routed package-level `.grc` tools."""

import copy
from collections import OrderedDict
from functools import lru_cache
import json
import logging
import re
from pathlib import Path
import time
from typing import Any, Callable

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
from grc_agent.retrieval.search import _search_grc_with_context
from grc_agent.retrieval.vector import semantic_search_grc, vector_index_version_token
from grc_agent.runtime.clarification import ClarificationOption, ClarificationRequest
from grc_agent.runtime.prompt import build_system_prompt
from grc_agent.runtime.tool_schemas import MODEL_TOOL_NAMES_ORDERED, build_tool_schemas
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
_ALIAS_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
_SEARCH_BLOCK_SUMMARY_MAX_CHARS = 120
_SEARCH_BLOCKS_CACHE_MAX_SIZE = 64
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
        history_journal_path: str | Path | None = None,
    ) -> None:
        self.session = FlowgraphSession() if session is None else session
        self.catalog_root = str(catalog_root) if catalog_root is not None else None
        self.config = config or default_app_config().agent
        self._history_journal = GraphHistoryJournal(history_journal_path)
        self._history_lineage_key: str | None = None
        self.history: list[HistoryEntry] = []
        self._last_validated_state_revision: int | None = None
        self._last_validation_ok: bool | None = None
        self._reset_validation_tracking()
        self._tools = self._build_tool_registry()
        self._mvp_tools = self._build_mvp_tool_registry()
        self._tool_schemas = build_tool_schemas()
        self._tool_schema_map = build_tool_schema_map(self._tool_schemas)
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
        self._search_blocks_cache: OrderedDict[
            tuple[str, int, str], dict[str, Any]
        ] = OrderedDict()
        self._maybe_record_baseline_checkpoint(reason="initial_session")

    def get_system_prompt(self) -> str:
        return build_system_prompt()

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return the fixed tool schemas exposed to a chat-completions client."""
        return self._tool_schemas

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
        schemas_by_name = {
            schema["function"]["name"]: schema
            for schema in self._tool_schemas
        }
        return [
            self._schema_narrowed_for_turn(schemas_by_name[name])
            for name in allowed_order
            if name in schemas_by_name
        ]

    def execute_tool(self, tool_name: str, kwargs: dict[str, Any]) -> ToolResult:
        """Execute one runtime tool and return a structured result."""
        kwargs = self.normalize_tool_call_arguments(tool_name, kwargs)
        before_snapshot = self._checkpoint_before(tool_name)
        validation_result = self.validate_tool_call(tool_name, kwargs)
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
        return tool_name in {
            "new_grc",
            "load_grc",
            "apply_edit",
            "remove_connection",
            "validate_graph",
            "save_graph",
        }

    def resolve_pending_clarification(
        self, user_message: str
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
                    result = self.execute_tool(opt.tool_name, opt.tool_args)
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
            "search_help": self._search_help,
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
            if result.get("ok") and not bool(result.get("dry_run")):
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
                save_path=result.get("path") if tool_name == "save_graph" else None,
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
            payload = summarize_graph(self.session, max_blocks=max_items) if max_items else summarize_graph(self.session)
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
            payload = self._get_grc_context(target.strip(), max_nodes=max_items or 20)
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
        while len(self._search_blocks_cache) > _SEARCH_BLOCKS_CACHE_MAX_SIZE:
            self._search_blocks_cache.popitem(last=False)

    def _search_blocks(
        self,
        query: str,
        k: int = 5,
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
        limit = max(1, min(int(k), 5))
        cacheable = not debug and not enrich
        lexical: dict[str, Any] = {"ok": True, "results": []}
        retrieval_mode = "hybrid"
        semantic: dict[str, Any] = {"ok": True, "results": []}

        query_raw = " ".join(q.split()).strip().lower()
        query_alias = _normalize_alias_key(q)
        exact_block_id: str | None = None
        exact_alias_hit = False
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
                    retrieval_mode = "lexical_fallback"

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

    def _search_help(self, query: str, k: int = 3, debug: bool = False) -> ToolResult:
        started = time.monotonic()
        before_revision = self.session.state_revision
        before_dirty = self.session.is_dirty
        handlers = ["search_manual"]
        payload = search_manual(query, k=k)
        if payload.get("ok") is not True:
            result = self._payload_result("search_help", payload)
            return self._attach_wrapper_dispatch_telemetry(
                debug=debug,
                wrapper_name="search_help",
                wrapper_action="query",
                internal_handlers=handlers,
                started=started,
                before_revision=before_revision,
                before_dirty=before_dirty,
                result=result,
                validation_run=False,
                output_truncated=False,
            )
        compact_results: list[dict[str, Any]] = []
        for row in payload.get("results", []):
            if not isinstance(row, dict):
                continue
            citation = row.get("citation")
            source = None
            if isinstance(citation, dict):
                source = citation.get("url") or citation.get("path")
            compact_results.append(
                {
                    "title": row.get("title"),
                    "excerpt": row.get("excerpt"),
                    "source": source,
                }
            )
        result = self._payload_result(
            "search_help",
            {
                "ok": True,
                "query": payload.get("query"),
                "results": compact_results,
                "message": "Explanation-only help results returned.",
            },
        )
        output_truncated = isinstance(k, int) and len(compact_results) >= max(1, min(int(k), 8))
        return self._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="search_help",
            wrapper_action="query",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=output_truncated,
        )

    def _change_graph(
        self,
        dry_run: bool,
        user_goal: str,
        target_ref: dict[str, Any] | None = None,
        block_id: str | None = None,
        instance_name: str | None = None,
        connection_id: str | None = None,
        src_block: str | None = None,
        src_port: int | str | None = None,
        dst_block: str | None = None,
        dst_port: int | str | None = None,
        new_src_block: str | None = None,
        new_src_port: int | str | None = None,
        new_dst_block: str | None = None,
        new_dst_port: int | str | None = None,
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
        lower_goal = user_goal.lower()
        if any(token in lower_goal for token in ("yaml", "undo", "redo", "export python", "source text")):
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
        if "save" in lower_goal or "write out" in lower_goal:
            result = self._payload_result(
                "change_graph",
                {
                    "ok": False,
                    "dry_run": bool(dry_run),
                    "error_type": ErrorCode.UNSUPPORTED_OP,
                    "message": "Saving is CLI/user-controlled and not model-facing in MVP.",
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

        if (
            isinstance(connection_id, str)
            and connection_id.strip()
            and all(
                isinstance(value, str) and value
                for value in (new_src_block, new_dst_block)
            )
            and new_src_port is not None
            and new_dst_port is not None
        ):
            operation_summary = "rewire_connection"
            if dry_run:
                handlers.append("propose_edit(rewire_transaction)")
                result = tx_tool(
                    [
                        {"op_type": "remove_connection", "connection_id": connection_id.strip()},
                        {
                            "op_type": "add_connection",
                            "src_block": new_src_block,
                            "src_port": new_src_port,
                            "dst_block": new_dst_block,
                            "dst_port": new_dst_port,
                        },
                    ]
                )
            else:
                handlers.append("rewire_connection")
                result = self._rewire_connection(
                    old_connection_id=connection_id.strip(),
                    new_src_block=str(new_src_block),
                    new_src_port=new_src_port,
                    new_dst_block=str(new_dst_block),
                    new_dst_port=new_dst_port,
                )
        elif (
            isinstance(block_id, str)
            and block_id.strip()
            and isinstance(connection_id, str)
            and connection_id.strip()
        ):
            operation_summary = "insert_block_on_connection"
            if dry_run:
                handlers.append("propose_edit(insert_block_on_connection)")
                result = tx_tool(
                    {
                        "op_type": "insert_block_on_connection",
                        "connection_id": connection_id.strip(),
                        "block_type": block_id.strip(),
                        "instance_name": instance_name or f"{block_id.strip()}_0",
                    }
                )
            else:
                handlers.append("insert_block_on_connection")
                result = self._insert_block_on_connection(
                    connection_id=connection_id.strip(),
                    block_type=block_id.strip(),
                    instance_name=instance_name or f"{block_id.strip()}_0",
                    params={},
                )
        elif isinstance(connection_id, str) and connection_id.strip():
            insertion_words = ("insert", "add", "compatible")
            if any(word in lower_goal for word in insertion_words):
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
                if dry_run:
                    handlers.append("propose_edit(remove_connection)")
                    result = tx_tool(
                        {
                            "op_type": "remove_connection",
                            "connection_id": connection_id.strip(),
                        }
                    )
                else:
                    handlers.append("remove_connection")
                    result = self._remove_connection(connection_id=connection_id.strip())
        elif isinstance(variable_name, str) and variable_name.strip() and variable_value is not None:
            operation_summary = "add_variable"
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
                        "Provide exact connection_id for disconnect/insert/rewire.",
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
        payload: dict[str, Any] = {
            "ok": bool(result.get("ok")) if isinstance(result, dict) else False,
            "dry_run": bool(dry_run),
            "operation_summary": operation_summary,
            "graph_delta": result.get("graph_delta") if isinstance(result, dict) else None,
            "validation_result": validation_result,
            "checkpoint_id": result.get("checkpoint_id") if isinstance(result, dict) else None,
            "message": result.get("message") if isinstance(result, dict) else "change_graph failed",
        }
        if isinstance(result, dict) and result.get("error_type"):
            payload["error_type"] = result.get("error_type")
        if isinstance(result, dict) and result.get("clarification_required"):
            payload["clarification_options"] = result.get("options")
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

        result = self._apply_edit(
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
