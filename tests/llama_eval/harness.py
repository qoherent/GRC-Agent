"""Shared helpers for live llama eval runners."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_launcher import LlamaLauncherError, LlamaServerLauncher
from grc_agent.runtime import prompt as runtime_prompt
from grc_agent.runtime import tool_schemas as runtime_tool_schemas
from grc_agent.runtime.tool_surface import MVP_MODEL_TOOL_NAMES
from grc_agent.recovery import (
    NO_RECOVERY_NEEDED,
    RecoveryDecision,
    classify_tool_result_for_recovery,
)
from grc_agent.trace import build_live_eval_turn_trace
from grc_agent.session_ops import parse_connection_id
from grc_agent.toolagents_runtime import (
    ToolAgentsLlamaProviderConfig,
    run_bounded_toolagents_turn,
)

DEFAULT_FIXTURE_NAME = "random_bit_generator.grc"
DEFAULT_LIVE_EVAL_MAX_TOKENS = 2048
RESULTS_SCHEMA_VERSION = "2026-04-28.intent-hardening"
RUN_STATUS_PASS = "PASS"
RUN_STATUS_FAIL = "FAIL"
RUN_STATUS_INFRA_FAIL = "INFRA_FAIL"
RUN_STATUS_INFRA_BANNER = "INFRA_UNAVAILABLE"
REPORT_DIMENSIONS = (
    "routing_pass",
    "argument_pass",
    "tool_success_pass",
    "semantic_pass",
    "safety_pass",
    "runtime_safety_pass",
    "model_contract_pass",
    "end_state_pass",
    "recovery_pass",
)
MVP_RELEASE_MODEL_TOOLS = frozenset(MVP_MODEL_TOOL_NAMES)
MUTATION_TOOL_NAMES = frozenset(
    {
        "new_grc",
        "load_grc",
        "apply_edit",
        "insert_block_on_connection",
        "auto_insert_block",
        "remove_connection",
        "rewire_connection",
        "save_graph",
    }
)
RECOVERY_MUTATION_RETRY_TOOL_NAMES = frozenset(
    {
        "new_grc",
        "load_grc",
        "apply_edit",
        "insert_block_on_connection",
        "auto_insert_block",
        "remove_connection",
        "rewire_connection",
        "save_graph",
    }
)
PRE_TURN_SETUP_TOOL_NAMES = frozenset(
    {
        "apply_edit",
        "insert_block_on_connection",
        "auto_insert_block",
        "remove_connection",
        "rewire_connection",
    }
)

CaseRunner = Callable[[ToolAgentsLlamaProviderConfig, str, Any], dict[str, Any]]
CaseReportBuilder = Callable[[Any, list[dict[str, Any]], int, float], dict[str, Any]]
StatusRenderer = Callable[[Any, dict[str, Any]], str]
SummaryBuilder = Callable[[list[dict[str, Any]], int], dict[str, Any]]


@dataclass(frozen=True)
class ToolExpectation:
    """Declarative expectation for one model-requested tool call."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    transaction_operations: tuple[dict[str, Any], ...] = ()
    ordered_transaction: bool = True
    require_result_ok: bool = True


@dataclass(frozen=True)
class LiveTurnSpec:
    """One user turn in a live eval scenario."""

    prompt: str
    expected_tool_calls: tuple[ToolExpectation, ...] = ()
    semantic_checks: tuple[dict[str, Any], ...] = ()
    pre_turn_tool_name: str = ""
    pre_turn_tool_args: dict[str, Any] = field(default_factory=dict)
    pre_turn_allow_clarification: bool = False
    pre_turn_require_valid_graph: bool = True
    accept_any_tool: bool = False
    allow_safe_text_only: bool = False
    clarification_response: bool = False
    recovery_enabled: bool = False
    expected_recovery_class: str | None = None

    @property
    def expected_tools(self) -> list[str]:
        return [expectation.name for expectation in self.expected_tool_calls]


@dataclass(frozen=True)
class LiveScenario:
    """Declarative live eval scenario shared by current dashboard runners."""

    category: str
    name: str
    turns: tuple[LiveTurnSpec, ...]
    fixture_name: str = DEFAULT_FIXTURE_NAME
    target_fixture_name: str | None = None
    description: str = ""
    release_profile: str = "BETA_COMPLEX_MUTATION"

    @property
    def prompt(self) -> str:
        return self.turns[0].prompt if self.turns else ""



def scenario_expected_tools_only(
    scenario: LiveScenario,
    *,
    allowed_tool_names: set[str] | frozenset[str],
) -> bool:
    """Return whether all expected tool calls stay within an allow-list."""
    for turn in scenario.turns:
        for expectation in turn.expected_tool_calls:
            if expectation.name not in allowed_tool_names:
                return False
    return True


def fixture_path(name: str = DEFAULT_FIXTURE_NAME) -> Path:
    return Path(__file__).resolve().parents[1] / "data" / name


def _session_from_agent_or_session(agent_or_session: Any) -> FlowgraphSession:
    session = getattr(agent_or_session, "session", agent_or_session)
    if not isinstance(session, FlowgraphSession):
        raise TypeError("Expected GrcAgent or FlowgraphSession.")
    if session.flowgraph is None:
        raise ValueError("No graph loaded.")
    return session


def graph_snapshot(agent_or_session: Any) -> dict[str, Any]:
    """Return a stable test/eval snapshot of the current graph state."""
    session = _session_from_agent_or_session(agent_or_session)
    flowgraph = session.flowgraph
    if flowgraph is None:
        raise ValueError("No graph loaded.")

    serialized = session._serialize_raw_data(flowgraph.raw_data)
    blocks = [
        {
            "uid": block.block_uid,
            "name": block.instance_name,
            "type": block.block_type,
            "parameters": dict(block.params.get("parameters") or {}),
            "state": (block.params.get("states") or {}).get("state"),
            "coordinate": (block.params.get("states") or {}).get("coordinate"),
            "rotation": (block.params.get("states") or {}).get("rotation"),
        }
        for block in flowgraph.blocks
    ]
    blocks_by_name = {
        block["name"]: {
            "type": block["type"],
            "parameters": block["parameters"],
            "state": block["state"],
        }
        for block in sorted(blocks, key=lambda item: str(item["name"]))
    }
    blocks_by_uid = {
        str(block["uid"]): {
            "name": block["name"],
            "type": block["type"],
            "parameters": block["parameters"],
            "state": block["state"],
            "coordinate": block["coordinate"],
            "rotation": block["rotation"],
        }
        for block in sorted(blocks, key=lambda item: str(item["uid"]))
    }
    duplicate_block_groups: dict[str, list[str]] = {}
    grouped_uids: dict[tuple[str, str], list[str]] = {}
    for block in blocks:
        grouped_uids.setdefault((str(block["name"]), str(block["type"])), []).append(str(block["uid"]))
    for (name, block_type), uids in sorted(grouped_uids.items()):
        if len(uids) > 1:
            duplicate_block_groups[f"{name}|{block_type}"] = list(uids)
    variable_values = {
        block.instance_name: block.params.get("parameters", {}).get("value")
        for block in flowgraph.blocks
        if block.block_type == "variable"
    }
    connection_ids = sorted(
        f"{connection.src_block}:{connection.src_port}->{connection.dst_block}:{connection.dst_port}"
        for connection in flowgraph.connections
    )

    validation = session.validation_state()
    return {
        "path": str(session.path) if session.path is not None else None,
        "state_revision": session.state_revision,
        "dirty": session.is_dirty,
        "validation_status": validation.get("status"),
        "validation_returncode": validation.get("returncode"),
        "block_count": len(flowgraph.blocks),
        "connection_count": len(flowgraph.connections),
        "block_names": sorted(block["name"] for block in blocks),
        "blocks_by_name": blocks_by_name,
        "block_uids": sorted(str(block["uid"]) for block in blocks),
        "blocks_by_uid": blocks_by_uid,
        "duplicate_block_groups": duplicate_block_groups,
        "variable_values": dict(sorted(variable_values.items())),
        "connection_ids": connection_ids,
        "raw_hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def snapshot_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Return whether graph content changed between two snapshots."""
    return before.get("raw_hash") != after.get("raw_hash")


def graph_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return the exact semantic graph delta tracked by live evals."""
    delta: dict[str, Any] = {}

    before_blocks = set(_string_list(before.get("block_names")))
    after_blocks = set(_string_list(after.get("block_names")))
    _add_non_empty(delta, "added_blocks", sorted(after_blocks - before_blocks))
    _add_non_empty(delta, "removed_blocks", sorted(before_blocks - after_blocks))

    before_connections = set(_string_list(before.get("connection_ids")))
    after_connections = set(_string_list(after.get("connection_ids")))
    _add_non_empty(delta, "added_connections", sorted(after_connections - before_connections))
    _add_non_empty(delta, "removed_connections", sorted(before_connections - after_connections))

    variable_delta = _value_delta(
        _dict_value(before.get("variable_values")),
        _dict_value(after.get("variable_values")),
    )
    _add_non_empty(delta, "variables", variable_delta)

    before_by_name = _dict_value(before.get("blocks_by_name"))
    after_by_name = _dict_value(after.get("blocks_by_name"))
    block_params_delta: dict[str, Any] = {}
    block_states_delta: dict[str, Any] = {}
    for block_name in sorted(before_blocks & after_blocks):
        before_block = _dict_value(before_by_name.get(block_name))
        after_block = _dict_value(after_by_name.get(block_name))
        params = _value_delta(
            _dict_value(before_block.get("parameters")),
            _dict_value(after_block.get("parameters")),
        )
        if params:
            block_params_delta[block_name] = params
        if before_block.get("state") != after_block.get("state"):
            block_states_delta[block_name] = after_block.get("state")
    _add_non_empty(delta, "block_params", block_params_delta)
    _add_non_empty(delta, "block_states", block_states_delta)

    if before.get("dirty") != after.get("dirty"):
        delta["dirty"] = after.get("dirty")
    if before.get("validation_status") != after.get("validation_status"):
        delta["validation_status"] = after.get("validation_status")
    if before.get("validation_returncode") != after.get("validation_returncode"):
        delta["validation_returncode"] = after.get("validation_returncode")

    return delta


def uid_graph_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return an exact UID-keyed delta for duplicate-safe eval assertions."""
    delta: dict[str, Any] = {}

    before_uids = set(_string_list(before.get("block_uids")))
    after_uids = set(_string_list(after.get("block_uids")))
    _add_non_empty(delta, "added_block_uids", sorted(after_uids - before_uids))
    _add_non_empty(delta, "removed_block_uids", sorted(before_uids - after_uids))

    before_connections = set(_string_list(before.get("connection_ids")))
    after_connections = set(_string_list(after.get("connection_ids")))
    _add_non_empty(delta, "added_connections", sorted(after_connections - before_connections))
    _add_non_empty(delta, "removed_connections", sorted(before_connections - after_connections))

    before_by_uid = _dict_value(before.get("blocks_by_uid"))
    after_by_uid = _dict_value(after.get("blocks_by_uid"))
    params_delta: dict[str, Any] = {}
    states_delta: dict[str, Any] = {}
    layout_delta: dict[str, Any] = {}
    for uid in sorted(before_uids & after_uids):
        before_block = _dict_value(before_by_uid.get(uid))
        after_block = _dict_value(after_by_uid.get(uid))
        params = _value_delta(
            _dict_value(before_block.get("parameters")),
            _dict_value(after_block.get("parameters")),
        )
        if params:
            params_delta[uid] = params
        if before_block.get("state") != after_block.get("state"):
            states_delta[uid] = after_block.get("state")
        layout = _value_delta(
            {
                "coordinate": before_block.get("coordinate"),
                "rotation": before_block.get("rotation"),
            },
            {
                "coordinate": after_block.get("coordinate"),
                "rotation": after_block.get("rotation"),
            },
        )
        if layout:
            layout_delta[uid] = layout
    _add_non_empty(delta, "block_params_by_uid", params_delta)
    _add_non_empty(delta, "block_states_by_uid", states_delta)
    _add_non_empty(delta, "block_layout_by_uid", layout_delta)

    if before.get("dirty") != after.get("dirty"):
        delta["dirty"] = after.get("dirty")
    if before.get("validation_status") != after.get("validation_status"):
        delta["validation_status"] = after.get("validation_status")
    if before.get("validation_returncode") != after.get("validation_returncode"):
        delta["validation_returncode"] = after.get("validation_returncode")

    return delta


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _value_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            changes[str(key)] = after.get(key)
    return changes


def _add_non_empty(target: dict[str, Any], key: str, value: Any) -> None:
    if value:
        target[key] = value


def _normalize_graph_delta(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in {
            "added_blocks",
            "removed_blocks",
            "added_block_uids",
            "removed_block_uids",
            "added_connections",
            "removed_connections",
        }:
            list_item = item if isinstance(item, list) else []
            _add_non_empty(normalized, str(key), sorted(str(entry) for entry in list_item))
        elif key in {
            "variables",
            "block_params",
            "block_states",
            "block_params_by_uid",
            "block_states_by_uid",
            "block_layout_by_uid",
        }:
            dict_item = item if isinstance(item, dict) else {}
            _add_non_empty(normalized, str(key), _normalize_nested_mapping(dict_item))
        elif item is not None:
            normalized[str(key)] = item
    return normalized


def _normalize_nested_mapping(value: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in sorted(value):
        item = value[key]
        if isinstance(item, dict):
            nested = _normalize_nested_mapping(item)
            if nested:
                normalized[str(key)] = nested
        elif item is not None:
            normalized[str(key)] = _normalize_delta_scalar(item)
    return normalized


def _normalize_delta_scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return str(value)
    return value


def graph_variable_value(snapshot: dict[str, Any], variable_name: str) -> Any:
    values = snapshot.get("variable_values")
    if not isinstance(values, dict):
        return None
    return values.get(variable_name)


def graph_block_param_value(snapshot: dict[str, Any], instance_name: str, param: str) -> Any:
    blocks = snapshot.get("blocks_by_name")
    if not isinstance(blocks, dict):
        return None
    block = blocks.get(instance_name)
    if not isinstance(block, dict):
        return None
    parameters = block.get("parameters")
    if not isinstance(parameters, dict):
        return None
    return parameters.get(param)


def graph_block_uid(
    snapshot: dict[str, Any],
    *,
    instance_name: str,
    block_type: str,
    index: int = 0,
) -> str | None:
    groups = snapshot.get("duplicate_block_groups")
    if not isinstance(groups, dict):
        return None
    uids = groups.get(f"{instance_name}|{block_type}")
    if not isinstance(uids, list) or index < 0 or index >= len(uids):
        return None
    return str(uids[index])


def graph_block_param_value_by_uid(snapshot: dict[str, Any], block_uid: str, param: str) -> Any:
    blocks = snapshot.get("blocks_by_uid")
    if not isinstance(blocks, dict):
        return None
    block = blocks.get(block_uid)
    if not isinstance(block, dict):
        return None
    parameters = block.get("parameters")
    if not isinstance(parameters, dict):
        return None
    return parameters.get(param)


def graph_block_state_by_uid(snapshot: dict[str, Any], block_uid: str) -> Any:
    blocks = snapshot.get("blocks_by_uid")
    if not isinstance(blocks, dict):
        return None
    block = blocks.get(block_uid)
    if not isinstance(block, dict):
        return None
    return block.get("state")


def graph_block_state(snapshot: dict[str, Any], instance_name: str) -> Any:
    blocks = snapshot.get("blocks_by_name")
    if not isinstance(blocks, dict):
        return None
    block = blocks.get(instance_name)
    if not isinstance(block, dict):
        return None
    return block.get("state")


def saved_graph_reloads_and_validates(path: str | Path) -> dict[str, Any]:
    """Reload a saved `.grc` file and validate it with the session's normal grcc path."""
    target = Path(path)
    result: dict[str, Any] = {
        "path": str(target),
        "exists": target.exists(),
        "loaded": False,
        "valid": False,
        "error": None,
    }
    if not target.exists():
        result["error"] = "file does not exist"
        return result
    session = FlowgraphSession()
    try:
        session.load(target)
        result["loaded"] = True
        result["snapshot"] = graph_snapshot(session)
        result["valid"] = session.validate()
        result["validation_returncode"] = session.last_validation_returncode
        result["validation_stdout"] = session.last_validation_stdout
        result["validation_stderr"] = session.last_validation_stderr
    except Exception as exc:
        result["error"] = str(exc)
    return result


def collect_backend_metadata(
    client: ToolAgentsLlamaProviderConfig | None,
    *,
    server_url: str,
    model: str,
    temperature: float,
) -> dict[str, Any]:
    """Collect best-effort llama.cpp metadata without failing eval execution."""
    metadata: dict[str, Any] = {
        "server_url": server_url,
        "model": model,
        "temperature": temperature,
        "props_available": False,
    }
    if client is None:
        return metadata

    try:
        props = client.get_server_properties()
    except Exception as exc:
        metadata["props_error"] = str(exc)
        return metadata

    metadata["props_available"] = True
    for key in (
        "chat_template",
        "chat_template_tool_use",
        "tool_call_format",
        "tool_call_parser",
    ):
        if key in props:
            metadata.update(_compact_backend_prop(key, props[key]))
    metadata["backend_tool_call_risk"] = (
        "low"
        if props.get("chat_template_tool_use") or props.get("tool_call_parser")
        else "generic_or_unknown"
    )
    return metadata


def _compact_backend_prop(key: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {key: value}
    if len(value) <= 240:
        return {key: value}
    return {
        f"{key}_present": True,
        f"{key}_chars": len(value),
        f"{key}_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        f"{key}_preview": value[:120],
    }


def _compact_pre_turn_setup_result(
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Persist evidence that eval setup used public tools and left a valid graph."""
    return {
        "tool": tool_name,
        "arguments": arguments,
        "ok": result.get("ok"),
        "valid": result.get("valid"),
        "clarification_required": result.get("clarification_required"),
        "error_type": result.get("error_type"),
        "state_revision": result.get("state_revision"),
        "state_revision_before": result.get("state_revision_before"),
        "state_revision_after": result.get("state_revision_after"),
        "validation_status": _nested_value(result, ("validation", "status")),
        "validation_returncode": _nested_value(result, ("validation", "returncode")),
    }


def _nested_value(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def collect_release_metadata(
    *,
    case: Any | None = None,
    model_alias: str | None = None,
    backend_metadata: dict[str, Any] | None = None,
    mvp_tool_profile: bool = True,
) -> dict[str, Any]:
    """Return reproducibility metadata persisted with live eval rows."""
    prompt_text = runtime_prompt.build_system_prompt()
    schema_names = MVP_MODEL_TOOL_NAMES
    tool_schema_text = json.dumps(
        runtime_tool_schemas.build_tool_schemas(schema_names),
        sort_keys=True,
        separators=(",", ":"),
    )
    backend = backend_metadata if isinstance(backend_metadata, dict) else {}
    return {
        "results_schema_version": RESULTS_SCHEMA_VERSION,
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "prompt_version": getattr(runtime_prompt, "__version__", "unknown"),
        "prompt_sha256": _sha256_text(prompt_text),
        "tool_schema_sha256": _sha256_text(tool_schema_text),
        "model_alias": model_alias or backend.get("model") or "",
        "backend_metadata": backend,
        "chat_template_hash": backend.get("chat_template_sha256")
        or backend.get("chat_template_tool_use_sha256")
        or "",
        "mvp_tool_profile": True,
        "tool_surface": "mvp",
        "model_tool_names": list(schema_names),
        "fixture": getattr(case, "fixture_name", DEFAULT_FIXTURE_NAME) if case is not None else "",
        "target_fixture": getattr(case, "target_fixture_name", None) if case is not None else None,
        "release_profile": getattr(case, "release_profile", "BETA_COMPLEX_MUTATION") if case is not None else "",
    }


def _source_text_for(module: Any) -> str:
    path = getattr(module, "__file__", "")
    if not path:
        return repr(module)
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return repr(module)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool | None:
    try:
        output = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return bool(output.strip())


def run_live_scenario_once(
    *,
    client: ToolAgentsLlamaProviderConfig,
    model: str,
    scenario: LiveScenario,
    mvp_tool_profile: bool = True,
) -> dict[str, Any]:
    """Run one declarative live scenario in an isolated fixture workspace."""
    from grc_agent.agent import GrcAgent

    fixture_names = [scenario.fixture_name]
    if scenario.target_fixture_name:
        fixture_names.append(scenario.target_fixture_name)

    with isolated_fixture_workspace(*fixture_names) as (workspace, paths):
        fixture_path = paths[scenario.fixture_name]
        target_path = (
            str(paths[scenario.target_fixture_name])
            if scenario.target_fixture_name
            else ""
        )
        save_path = str(workspace / "output.grc")

        mvp_tool_profile = True
        agent = GrcAgent()
        agent.execute_tool("load_grc", {"file_path": str(fixture_path)})

        turn_results: list[dict[str, Any]] = []
        error_message = ""
        started_at = datetime.now(timezone.utc)

        for turn_index, turn in enumerate(scenario.turns):
            pre_turn_setup: list[dict[str, Any]] = []
            if turn.pre_turn_tool_name:
                if turn.pre_turn_tool_name not in PRE_TURN_SETUP_TOOL_NAMES:
                    raise RuntimeError(
                        "unsupported pre-turn setup tool: "
                        f"{turn.pre_turn_tool_name}"
                    )
                setup_args = render_value_templates(
                    turn.pre_turn_tool_args,
                    target_path=target_path,
                    save_path=save_path,
                )
                setup_result = agent.execute_tool(turn.pre_turn_tool_name, setup_args)
                pre_turn_setup.append(
                    _compact_pre_turn_setup_result(
                        turn.pre_turn_tool_name,
                        setup_args,
                        setup_result,
                    )
                )
                setup_ok = bool(setup_result.get("ok"))
                setup_clarifies = (
                    turn.pre_turn_allow_clarification
                    and setup_result.get("clarification_required") is True
                )
                if not setup_ok and not setup_clarifies:
                    raise RuntimeError(
                        "pre-turn setup tool failed: "
                        f"{turn.pre_turn_tool_name} {setup_result}"
                    )
                if turn.pre_turn_require_valid_graph:
                    validation_result = agent.execute_tool("validate_graph", {})
                    pre_turn_setup.append(
                        _compact_pre_turn_setup_result(
                            "validate_graph",
                            {},
                            validation_result,
                        )
                    )
                    if not validation_result.get("ok") or not validation_result.get("valid"):
                        raise RuntimeError(
                            "pre-turn setup graph validation failed: "
                            f"{validation_result}"
                        )

            before_snapshot = graph_snapshot(agent)
            history_start = len(agent.history)
            prompt = render_prompt(
                turn.prompt,
                target_path=target_path,
                save_path=save_path,
            )
            result: dict[str, Any] = {}
            turn_error = ""
            turn_started = datetime.now(timezone.utc)
            try:
                if turn.clarification_response:
                    clarification = agent.resolve_pending_clarification(
                        prompt,
                        model_tool_call=True,
                    )
                    result = {
                        "ok": clarification.get("mode")
                        in {"executed", "reminder", "custom", "expired", "none"},
                        "assistant_text": clarification.get("text", ""),
                        "clarification_result": clarification,
                    }
                else:
                    result = run_bounded_toolagents_turn(
                        client=client,
                        model=model,
                        agent=agent,
                        user_message=prompt,
                        mvp_tool_profile=mvp_tool_profile,
                    )
            except Exception as exc:
                turn_error = str(exc)
                error_message = turn_error

            after_snapshot = graph_snapshot(agent)
            requested_tool_calls_raw = requested_tool_calls_since(agent.history, history_start)
            executed_tool_calls_raw = executed_tool_calls_since(agent.history, history_start)
            requested_tool_calls = requested_tool_calls_raw
            executed_tool_calls = executed_tool_calls_raw
            tool_dimensions = evaluate_tool_expectations(
                requested_tool_calls=requested_tool_calls,
                executed_tool_calls=executed_tool_calls,
                expected_tool_calls=render_tool_expectations(
                    turn.expected_tool_calls,
                    target_path=target_path,
                    save_path=save_path,
                ),
                accept_any_tool=turn.accept_any_tool,
                allow_safe_text_only=turn.allow_safe_text_only,
            )
            semantic_dimensions = evaluate_semantic_checks(
                checks=render_value_templates(
                    turn.semantic_checks,
                    target_path=target_path,
                    save_path=save_path,
                ),
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                run_result={
                    "requested_tool_calls": requested_tool_calls,
                    "executed_tool_calls": executed_tool_calls,
                    "assistant_text": result.get("assistant_text", "") if result else "",
                    "clarification_result": result.get("clarification_result")
                    if result
                    else None,
                },
                save_path=save_path,
            )
            recovery_dimensions = evaluate_turn_recovery(
                client=client,
                model=model,
                agent=agent,
                history_start=len(agent.history),
                executed_tool_calls=executed_tool_calls,
                recovery_enabled=turn.recovery_enabled,
                expected_recovery_class=turn.expected_recovery_class,
                mvp_tool_profile=mvp_tool_profile,
            )
            turn_result = {
                "turn_index": turn_index,
                "prompt": prompt,
                "tools_called": _tool_names(requested_tool_calls),
                "requested_tool_calls": requested_tool_calls,
                "executed_tool_calls": executed_tool_calls,
                "requested_tool_calls_raw": requested_tool_calls_raw,
                "executed_tool_calls_raw": executed_tool_calls_raw,
                "before_snapshot": before_snapshot,
                "after_snapshot": after_snapshot,
                "assistant_text": result.get("assistant_text", "") if result else "",
                "clarification_result": result.get("clarification_result")
                if result
                else None,
                "pre_turn_setup": pre_turn_setup,
                "ok": result.get("ok", False) if result else False,
                "error": turn_error,
                "elapsed_seconds": round(
                    (datetime.now(timezone.utc) - turn_started).total_seconds(),
                    3,
                ),
                **tool_dimensions,
                **semantic_dimensions,
                **recovery_dimensions,
            }
            if mvp_tool_profile:
                requested_names_raw = set(_tool_names(requested_tool_calls_raw))
                executed_names_raw = set(_tool_names(executed_tool_calls_raw))
                model_contract_pass = requested_names_raw.issubset(MVP_RELEASE_MODEL_TOOLS) and executed_names_raw.issubset(
                    MVP_RELEASE_MODEL_TOOLS
                )
                turn_result["model_contract_pass"] = model_contract_pass

                safe_surface_block = _is_safe_surface_blocked_legacy_attempt(
                    requested_tool_calls=requested_tool_calls_raw,
                    executed_tool_calls=executed_tool_calls_raw,
                    before_snapshot=before_snapshot,
                    after_snapshot=after_snapshot,
                )
                runtime_safety_pass = bool(turn_result["safety_pass"])
                if not model_contract_pass:
                    runtime_safety_pass = safe_surface_block
                turn_result["runtime_safety_pass"] = runtime_safety_pass
            else:
                turn_result["model_contract_pass"] = True
                turn_result["runtime_safety_pass"] = True
            turn_result["passed"] = (
                turn_result["routing_pass"]
                and turn_result["argument_pass"]
                and turn_result["tool_success_pass"]
                and turn_result["semantic_pass"]
                and turn_result["safety_pass"]
                and turn_result["end_state_pass"]
                and turn_result["recovery_pass"]
                and (turn_result.get("model_contract_pass") is not False)
                and (turn_result.get("runtime_safety_pass") is not False)
            )
            turn_result["trace"] = build_live_eval_turn_trace(
                prompt=prompt,
                active_tool_surface="mvp",
                raw_requested_tool_calls=requested_tool_calls_raw,
                requested_tool_calls=requested_tool_calls,
                executed_tool_calls=executed_tool_calls,
                state_revision_before=before_snapshot.get("state_revision"),
                state_revision_after=after_snapshot.get("state_revision"),
                graph_delta=graph_delta(before_snapshot, after_snapshot),
                model_contract_pass=turn_result.get("model_contract_pass"),
                runtime_safety_pass=turn_result.get("runtime_safety_pass"),
                semantic_pass=turn_result.get("semantic_pass"),
                passed=turn_result.get("passed"),
            )
            turn_results.append(turn_result)

            if turn_error:
                break

        overall = {
            dimension: all(turn.get(dimension) is True for turn in turn_results)
            if turn_results
            else False
            for dimension in REPORT_DIMENSIONS
        }
        return {
            "mvp_tool_profile": True,
            "expected_model_tools": sorted(MVP_RELEASE_MODEL_TOOLS),
            "tools_called": [
                name
                for turn_result in turn_results
                for name in turn_result.get("tools_called", [])
            ],
            "requested_tool_calls": [
                call
                for turn_result in turn_results
                for call in turn_result.get("requested_tool_calls", [])
            ],
            "executed_tool_calls": [
                call
                for turn_result in turn_results
                for call in turn_result.get("executed_tool_calls", [])
            ],
            "turn_results": turn_results,
            "matched": all(overall.values()) if overall else False,
            "passed": all(overall.values()) if overall else False,
            "ok": all(turn.get("ok") is True for turn in turn_results)
            if turn_results
            else False,
            "error": error_message,
            "elapsed_seconds": round(
                (datetime.now(timezone.utc) - started_at).total_seconds(),
                3,
            ),
            **overall,
        }


def dimension_pass_counts(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Count pass totals for optional report dimensions across all non-infra runs."""
    counts = {
        dimension: {"passed": 0, "total": 0}
        for dimension in REPORT_DIMENSIONS
    }
    for result in results:
        for run in result.get("runs", []):
            if run.get("status") == RUN_STATUS_INFRA_FAIL:
                continue
            for dimension in REPORT_DIMENSIONS:
                value = run.get(dimension)
                if not isinstance(value, bool):
                    continue
                counts[dimension]["total"] += 1
                if value:
                    counts[dimension]["passed"] += 1
    return counts




def evaluate_tool_expectations(
    *,
    requested_tool_calls: list[dict[str, Any]],
    executed_tool_calls: list[dict[str, Any]],
    expected_tool_calls: tuple[ToolExpectation, ...],
    accept_any_tool: bool = False,
    allow_safe_text_only: bool = False,
) -> dict[str, bool]:
    """Evaluate routing, argument, and execution success independently."""
    requested_names = _tool_names(requested_tool_calls)
    executed_names = _tool_names(executed_tool_calls)
    expected_names = [expectation.name for expectation in expected_tool_calls]

    if allow_safe_text_only and not requested_tool_calls:
        return {
            "routing_pass": True,
            "argument_pass": True,
            "tool_success_pass": True,
        }

    if accept_any_tool:
        return {
            "routing_pass": bool(requested_tool_calls),
            "argument_pass": bool(requested_tool_calls),
            "tool_success_pass": any(_tool_result_ok(call) for call in executed_tool_calls),
        }

    routing_pass = tools_appear_in_expected_order(requested_names, expected_names)
    argument_pass = routing_pass and _requested_calls_match_expectations(
        requested_tool_calls,
        expected_tool_calls,
    )
    tool_success_pass = tools_appear_in_expected_order(executed_names, expected_names)
    if tool_success_pass:
        tool_success_pass = _executed_calls_match_expectations(
            executed_tool_calls,
            expected_tool_calls,
        )

    return {
        "routing_pass": routing_pass,
        "argument_pass": argument_pass,
        "tool_success_pass": tool_success_pass,
    }


def evaluate_semantic_checks(
    *,
    checks: tuple[dict[str, Any], ...],
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    run_result: dict[str, Any],
    save_path: str,
) -> dict[str, Any]:
    """Evaluate reusable graph/end-state checks for live evals."""
    if not checks:
        return {"semantic_pass": True, "safety_pass": True, "end_state_pass": True}

    details: list[dict[str, Any]] = []
    semantic_pass = True
    safety_pass = True
    end_state_pass = True
    extra: dict[str, Any] = {}

    for check in checks:
        kind = check.get("kind")
        passed = False
        detail: dict[str, Any] = {"kind": kind}
        if kind == "no_mutation":
            passed = (
                not snapshot_changed(before_snapshot, after_snapshot)
                and before_snapshot.get("state_revision") == after_snapshot.get("state_revision")
            )
            safety_pass = safety_pass and passed
            end_state_pass = end_state_pass and passed
        elif kind == "mutation":
            passed = snapshot_changed(before_snapshot, after_snapshot)
            end_state_pass = end_state_pass and passed
        elif kind == "variable_equals":
            passed = str(graph_variable_value(after_snapshot, str(check.get("name")))) == str(
                check.get("value")
            )
            end_state_pass = end_state_pass and passed
        elif kind == "block_param_equals":
            passed = str(graph_block_param_value(
                after_snapshot,
                str(check.get("instance_name")),
                str(check.get("param")),
            )) == str(check.get("value"))
            end_state_pass = end_state_pass and passed
        elif kind == "block_state_equals":
            passed = str(graph_block_state(
                after_snapshot,
                str(check.get("instance_name")),
            )) == str(check.get("state"))
            end_state_pass = end_state_pass and passed
        elif kind == "uid_block_param_equals":
            passed = str(graph_block_param_value_by_uid(
                after_snapshot,
                str(check.get("block_uid")),
                str(check.get("param")),
            )) == str(check.get("value"))
            end_state_pass = end_state_pass and passed
        elif kind == "uid_block_state_equals":
            passed = str(graph_block_state_by_uid(
                after_snapshot,
                str(check.get("block_uid")),
            )) == str(check.get("state"))
            end_state_pass = end_state_pass and passed
        elif kind == "dirty":
            passed = after_snapshot.get("dirty") is bool(check.get("value", True))
            end_state_pass = end_state_pass and passed
        elif kind == "exact_graph_delta":
            actual_delta = _normalize_graph_delta(graph_delta(before_snapshot, after_snapshot))
            expected_delta = _normalize_graph_delta(check.get("delta"))
            passed = actual_delta == expected_delta
            detail["expected_delta"] = expected_delta
            detail["actual_delta"] = actual_delta
            if not passed and actual_delta:
                safety_pass = False
            end_state_pass = end_state_pass and passed
        elif kind == "uid_exact_graph_delta":
            actual_delta = _normalize_graph_delta(uid_graph_delta(before_snapshot, after_snapshot))
            expected_delta = _normalize_graph_delta(check.get("delta"))
            passed = actual_delta == expected_delta
            detail["expected_delta"] = expected_delta
            detail["actual_delta"] = actual_delta
            if not passed and actual_delta:
                safety_pass = False
            end_state_pass = end_state_pass and passed
        elif kind == "saved_path_valid":
            raw_path = check.get("path") or save_path
            expected_path = (
                str(after_snapshot.get("path"))
                if raw_path == "{after_path}"
                else str(raw_path)
            )
            validation = saved_graph_reloads_and_validates(expected_path)
            extra.setdefault("saved_graph_validations", []).append(validation)
            passed = (
                after_snapshot.get("path") == expected_path
                and after_snapshot.get("dirty") is False
                and validation.get("exists") is True
                and validation.get("loaded") is True
                and validation.get("valid") is True
            )
            if passed and check.get("copy") is True:
                passed = expected_path != str(before_snapshot.get("path"))
            end_state_pass = end_state_pass and passed
        elif kind == "saved_variable_equals":
            raw_path = check.get("path") or save_path
            expected_path = (
                str(after_snapshot.get("path"))
                if raw_path == "{after_path}"
                else str(raw_path)
            )
            validation = saved_graph_reloads_and_validates(expected_path)
            extra.setdefault("saved_graph_validations", []).append(validation)
            snapshot = validation.get("snapshot")
            passed = (
                validation.get("exists") is True
                and validation.get("loaded") is True
                and isinstance(snapshot, dict)
                and str(graph_variable_value(snapshot, str(check.get("name"))))
                == str(check.get("value"))
            )
            end_state_pass = end_state_pass and passed
        elif kind == "saved_block_param_equals":
            raw_path = check.get("path") or save_path
            expected_path = (
                str(after_snapshot.get("path"))
                if raw_path == "{after_path}"
                else str(raw_path)
            )
            validation = saved_graph_reloads_and_validates(expected_path)
            extra.setdefault("saved_graph_validations", []).append(validation)
            snapshot = validation.get("snapshot")
            passed = (
                validation.get("exists") is True
                and validation.get("loaded") is True
                and isinstance(snapshot, dict)
                and str(graph_block_param_value(
                    snapshot,
                    str(check.get("instance_name")),
                    str(check.get("param")),
                ))
                == str(check.get("value"))
            )
            end_state_pass = end_state_pass and passed
        elif kind == "saved_block_state_equals":
            raw_path = check.get("path") or save_path
            expected_path = (
                str(after_snapshot.get("path"))
                if raw_path == "{after_path}"
                else str(raw_path)
            )
            validation = saved_graph_reloads_and_validates(expected_path)
            extra.setdefault("saved_graph_validations", []).append(validation)
            snapshot = validation.get("snapshot")
            passed = (
                validation.get("exists") is True
                and validation.get("loaded") is True
                and isinstance(snapshot, dict)
                and str(graph_block_state(snapshot, str(check.get("instance_name"))))
                == str(check.get("state"))
            )
            end_state_pass = end_state_pass and passed
        elif kind == "saved_block_present":
            raw_path = check.get("path") or save_path
            expected_path = (
                str(after_snapshot.get("path"))
                if raw_path == "{after_path}"
                else str(raw_path)
            )
            validation = saved_graph_reloads_and_validates(expected_path)
            extra.setdefault("saved_graph_validations", []).append(validation)
            snapshot = validation.get("snapshot")
            block_name = str(check.get("instance_name"))
            passed = (
                validation.get("exists") is True
                and validation.get("loaded") is True
                and isinstance(snapshot, dict)
                and block_name in snapshot.get("block_names", [])
            )
            end_state_pass = end_state_pass and passed
        elif kind == "saved_block_absent":
            raw_path = check.get("path") or save_path
            expected_path = (
                str(after_snapshot.get("path"))
                if raw_path == "{after_path}"
                else str(raw_path)
            )
            validation = saved_graph_reloads_and_validates(expected_path)
            extra.setdefault("saved_graph_validations", []).append(validation)
            snapshot = validation.get("snapshot")
            block_name = str(check.get("instance_name"))
            passed = (
                validation.get("exists") is True
                and validation.get("loaded") is True
                and isinstance(snapshot, dict)
                and block_name not in snapshot.get("block_names", [])
            )
            end_state_pass = end_state_pass and passed
        elif kind == "saved_connection_present":
            raw_path = check.get("path") or save_path
            expected_path = (
                str(after_snapshot.get("path"))
                if raw_path == "{after_path}"
                else str(raw_path)
            )
            validation = saved_graph_reloads_and_validates(expected_path)
            extra.setdefault("saved_graph_validations", []).append(validation)
            snapshot = validation.get("snapshot")
            connection_id = str(check.get("connection_id"))
            passed = (
                validation.get("exists") is True
                and validation.get("loaded") is True
                and isinstance(snapshot, dict)
                and connection_id in snapshot.get("connection_ids", [])
            )
            end_state_pass = end_state_pass and passed
        elif kind == "saved_connection_absent":
            raw_path = check.get("path") or save_path
            expected_path = (
                str(after_snapshot.get("path"))
                if raw_path == "{after_path}"
                else str(raw_path)
            )
            validation = saved_graph_reloads_and_validates(expected_path)
            extra.setdefault("saved_graph_validations", []).append(validation)
            snapshot = validation.get("snapshot")
            connection_id = str(check.get("connection_id"))
            passed = (
                validation.get("exists") is True
                and validation.get("loaded") is True
                and isinstance(snapshot, dict)
                and connection_id not in snapshot.get("connection_ids", [])
            )
            end_state_pass = end_state_pass and passed
        elif kind == "tool_result":
            tool_name = str(check.get("tool"))
            payloads: list[dict[str, Any]] = []
            for call in run_result.get("executed_tool_calls", []):
                if call.get("name") != tool_name:
                    continue
                payload = call.get("arguments")
                if isinstance(payload, dict):
                    payloads.append(payload)
            expected_arguments = check.get("arguments")
            if isinstance(expected_arguments, dict):
                passed = any(_partial_match(payload, expected_arguments) for payload in payloads)
            else:
                passed = bool(payloads)
            end_state_pass = end_state_pass and passed
        elif kind == "assistant_text_contains":
            needles = check.get("needles")
            passed = isinstance(needles, list) and text_contains_any(
                str(run_result.get("assistant_text", "")),
                [str(needle) for needle in needles],
            )
        elif kind == "no_mutation_tools":
            passed = not any_mutation_tool_requested_or_executed(run_result)
            safety_pass = safety_pass and passed
        elif kind == "clarification_mode":
            result = run_result.get("clarification_result")
            if not isinstance(result, dict):
                result = {}
            passed = result.get("mode") == check.get("mode")
            if check.get("mode") in {"reminder", "custom", "expired"}:
                safety_pass = safety_pass and passed
        elif kind == "path_equals":
            expected_path = str(check.get("path"))
            if expected_path == "{save_path}":
                expected_path = save_path
            passed = after_snapshot.get("path") == expected_path
            end_state_pass = end_state_pass and passed
        elif kind == "connection_absent":
            connection_id = str(check.get("connection_id"))
            passed = connection_id not in after_snapshot.get("connection_ids", [])
            end_state_pass = end_state_pass and passed
        elif kind == "connection_present":
            connection_id = str(check.get("connection_id"))
            passed = connection_id in after_snapshot.get("connection_ids", [])
            end_state_pass = end_state_pass and passed
        else:
            passed = False

        detail["passed"] = passed
        details.append(detail)
        semantic_pass = semantic_pass and passed

    return {
        "semantic_pass": semantic_pass,
        "safety_pass": safety_pass,
        "end_state_pass": end_state_pass,
        "semantic_details": details,
        **extra,
    }


def evaluate_turn_recovery(
    *,
    client: ToolAgentsLlamaProviderConfig,
    model: str,
    agent: Any,
    history_start: int,
    executed_tool_calls: list[dict[str, Any]],
    recovery_enabled: bool = False,
    expected_recovery_class: str | None = None,
    mvp_tool_profile: bool = False,
) -> dict[str, Any]:
    """Classify and optionally probe one bounded recovery follow-up.

    Recovery is reported separately from the original turn result. The helper
    never converts the initial failure into a pass; it only measures whether a
    typed recovery policy exists and whether a model follow-up stays within it.
    """
    failed_call = latest_failed_executed_tool_call(executed_tool_calls)
    failed_tool_name = str(failed_call.get("name")) if isinstance(failed_call, dict) else ""
    failed_payload = failed_call.get("arguments", {}) if isinstance(failed_call, dict) else {}
    recovery_tool_name, recovery_payload = _normalize_failed_call_for_recovery(
        failed_tool_name,
        failed_payload,
    )
    decision = (
        classify_tool_result_for_recovery(
            recovery_tool_name,
            recovery_payload,
        )
        if failed_call is not None
        else RecoveryDecision(
            recovery_class=NO_RECOVERY_NEEDED,
            recoverable=False,
            reason="no failed tool result",
        )
    )
    decision_data = asdict(decision)
    expected_match = (
        expected_recovery_class is None
        or decision.recovery_class == expected_recovery_class
    )

    result: dict[str, Any] = {
        "recovery_pass": expected_match,
        "recovery_attempted": False,
        "recovery_decision": decision_data,
        "recovery_requested_tool_calls": [],
        "recovery_executed_tool_calls": [],
        "recovery_allowed_tools_pass": True,
        "recovery_retry_budget_pass": True,
        "recovery_tool_success_pass": True,
        "recovery_end_state_pass": True,
        "recovery_changed_state": False,
        "recovery_mutation_retry_count": 0,
        "pre_recovery_snapshot": None,
        "post_recovery_snapshot": None,
        "recovery_error": "",
    }

    if (
        not recovery_enabled
        or not decision.recoverable
        or not decision.prompt
        or not expected_match
    ):
        return result

    recovery_history_start = history_start
    pre_recovery_snapshot = _maybe_graph_snapshot(agent)
    try:
        run_bounded_toolagents_turn(
            client=client,
            model=model,
            agent=agent,
            user_message=decision.prompt,
            mvp_tool_profile=mvp_tool_profile,
        )
    except Exception as exc:
        result["recovery_error"] = str(exc)

    requested = requested_tool_calls_since(agent.history, recovery_history_start)
    executed = executed_tool_calls_since(agent.history, recovery_history_start)
    post_recovery_snapshot = _maybe_graph_snapshot(agent)
    requested_names = _tool_names(requested)
    executed_names = _tool_names(executed)
    allowed = set(decision.allowed_tools)
    all_names = [*requested_names, *executed_names]
    mutation_retry_count = sum(
        1 for name in requested_names if name in RECOVERY_MUTATION_RETRY_TOOL_NAMES
    )
    allowed_tools_pass = all(name in allowed for name in all_names)
    retry_budget_pass = mutation_retry_count <= decision.max_mutation_retries
    tool_success_pass = all(_tool_result_ok(call) for call in executed)
    changed_state = (
        pre_recovery_snapshot is not None
        and post_recovery_snapshot is not None
        and snapshot_changed(pre_recovery_snapshot, post_recovery_snapshot)
    )
    end_state_pass = bool(tool_success_pass and not result["recovery_error"])

    result.update(
        {
            "recovery_attempted": True,
            "recovery_requested_tool_calls": requested,
            "recovery_executed_tool_calls": executed,
            "recovery_allowed_tools_pass": allowed_tools_pass,
            "recovery_retry_budget_pass": retry_budget_pass,
            "recovery_tool_success_pass": tool_success_pass,
            "recovery_end_state_pass": end_state_pass,
            "recovery_changed_state": changed_state,
            "recovery_mutation_retry_count": mutation_retry_count,
            "pre_recovery_snapshot": pre_recovery_snapshot,
            "post_recovery_snapshot": post_recovery_snapshot,
            "recovery_pass": expected_match
            and allowed_tools_pass
            and retry_budget_pass
            and tool_success_pass
            and end_state_pass
            and not result["recovery_error"],
        }
    )
    return result


def _maybe_graph_snapshot(agent_or_session: Any) -> dict[str, Any] | None:
    try:
        return graph_snapshot(agent_or_session)
    except (TypeError, ValueError):
        return None


def latest_failed_executed_tool_call(
    executed_tool_calls: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the latest tool payload only when that latest payload failed."""
    for call in reversed(executed_tool_calls):
        payload = call.get("arguments")
        if not isinstance(payload, dict):
            continue
        if payload.get("ok") is False or payload.get("clarification_required") is True:
            return call
        return None
    return None


def _normalize_failed_call_for_recovery(
    tool_name: str,
    payload: Any,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return tool_name, {}
    normalized_payload = dict(payload)
    if (
        "validation" not in normalized_payload
        and isinstance(normalized_payload.get("validation_result"), dict)
    ):
        normalized_payload["validation"] = normalized_payload["validation_result"]
    if tool_name != "change_graph":
        return tool_name, normalized_payload
    operation_kind = str(normalized_payload.get("operation_kind") or "").strip()
    if operation_kind == "disconnect":
        return "remove_connection", normalized_payload
    if operation_kind in {
        "set_param",
        "set_state",
        "add_variable",
        "insert_in_connection",
        "add_signal_source_to_sum",
        "remove_block",
        "rewire",
    }:
        return "apply_edit", normalized_payload
    return tool_name, normalized_payload


def _is_safe_surface_blocked_legacy_attempt(
    *,
    requested_tool_calls: list[dict[str, Any]],
    executed_tool_calls: list[dict[str, Any]],
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
) -> bool:
    """Return true when MVP blocked a non-wrapper/internal tool and state stayed unchanged."""
    requested_names = _tool_names(requested_tool_calls)
    if not requested_names:
        return False
    legacy_requested = [name for name in requested_names if name not in MVP_RELEASE_MODEL_TOOLS]
    if not legacy_requested:
        return False
    executed_by_name = {
        str(call.get("name")): call.get("arguments")
        for call in executed_tool_calls
        if isinstance(call, dict)
    }
    for name in legacy_requested:
        payload = executed_by_name.get(name)
        if not isinstance(payload, dict):
            return False
        error_type = str(payload.get("error_type", ""))
        if error_type not in {"tool_not_allowed_for_surface", "route_mismatch"}:
            return False
    return (
        not snapshot_changed(before_snapshot, after_snapshot)
        and before_snapshot.get("state_revision") == after_snapshot.get("state_revision")
    )


def any_mutation_tool_requested_or_executed(run_result: dict[str, Any]) -> bool:
    requested = _tool_names(run_result.get("requested_tool_calls", []))
    executed = _tool_names(run_result.get("executed_tool_calls", []))
    return any(name in MUTATION_TOOL_NAMES for name in [*requested, *executed])


def first_executed_tool_result(
    run_result: dict[str, Any],
    tool_name: str,
) -> dict[str, Any] | None:
    for call in run_result.get("executed_tool_calls", []):
        if call.get("name") != tool_name:
            continue
        payload = call.get("arguments")
        return payload if isinstance(payload, dict) else None
    return None


@contextmanager
def isolated_fixture_workspace(
    *fixture_names: str | None,
) -> Iterator[tuple[Path, dict[str, Path]]]:
    """Copy one or more fixtures into a temporary workspace and clean it up."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        copied: dict[str, Path] = {}
        for fixture_name in fixture_names:
            if not fixture_name or fixture_name in copied:
                continue
            src = fixture_path(fixture_name)
            dst = workspace / src.name
            shutil.copy2(src, dst)
            copied[fixture_name] = dst
        yield workspace, copied


def ensure_llama_server(
    server_url: str | None = None,
    model: str | None = None,
) -> tuple[str, str, ToolAgentsLlamaProviderConfig]:
    """Ensure the llama.cpp server is reachable, starting it if necessary.

    Returns (server_url, model_alias, client).
    """
    config = load_app_config()
    resolved_url = (server_url or config.llama.server_url).rstrip("/")
    resolved_model = model or config.llama.model

    launcher = LlamaServerLauncher(
        config.llama,
        server_url=resolved_url,
        model_alias=resolved_model,
    )
    try:
        result = launcher.ensure_server_ready()
        print(
            f"{result.status.capitalize()} llama.cpp server at {result.server_url} (pid={result.pid})"
        )
        return result.server_url, result.model_alias, result.provider_config
    except LlamaLauncherError as exc:
        print(f"Failed to start llama.cpp server: {exc}")
        raise


def apply_live_generation_bounds(
    client: ToolAgentsLlamaProviderConfig,
    *,
    max_tokens: int | None,
) -> None:
    """Keep live eval completions bounded so backend stalls stay diagnosable."""
    if max_tokens is None:
        return
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("max_tokens must be a positive integer.")
    if hasattr(client, "max_tokens"):
        client.max_tokens = min(int(client.max_tokens), max_tokens)


def restart_llama_server(
    server_url: str | None = None,
    model: str | None = None,
) -> tuple[str, str, ToolAgentsLlamaProviderConfig]:
    """Force a fresh llama.cpp server instance and return a new client."""
    config = load_app_config()
    resolved_url = (server_url or config.llama.server_url).rstrip("/")
    resolved_model = model or config.llama.model

    launcher = LlamaServerLauncher(
        config.llama,
        server_url=resolved_url,
        model_alias=resolved_model,
    )

    result = launcher.restart_server_ready()
    print(f"Restarted llama.cpp server at {result.server_url} (pid={result.pid})")
    return (result.server_url, result.model_alias, result.provider_config)


def build_phase_parser(
    description: str,
    *,
    default_n_runs: int,
    server_help: str,
    model_help: str,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--server-url",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_URL"),
        help=server_help,
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL"),
        help=model_help,
    )
    parser.add_argument(
        "--n-runs",
        type=int,
        default=default_n_runs,
        help=f"Number of runs per case. Default: {default_n_runs}.",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Run only cases in this category.",
    )
    parser.add_argument(
        "--case",
        type=str,
        default=None,
        help="Run only the case with this name.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick check: force n_runs=1.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.environ.get("GRC_AGENT_LIVE_MAX_TOKENS", DEFAULT_LIVE_EVAL_MAX_TOKENS)),
        help=(
            "Maximum completion tokens for each live eval model call. "
            f"Default: {DEFAULT_LIVE_EVAL_MAX_TOKENS}."
        ),
    )
    parser.add_argument(
        "--stability-threshold",
        type=float,
        default=float(os.environ.get("GRC_AGENT_LIVE_STABILITY_THRESHOLD", "1.0")),
        help=(
            "Per-case model pass-rate required for release-stable reporting. "
            "This does not change majority pass/fail gating. Default: 1.0."
        ),
    )
    parser.add_argument(
        "--results-path",
        default=None,
        help="Optional JSONL-like run store path for resumable live eval results.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse cached runs from --results-path.",
    )
    parser.add_argument(
        "--rerun-failed",
        action="store_true",
        help="With --resume, reuse passing runs and rerun failures.",
    )
    return parser


def select_cases(
    all_cases: list[Any],
    *,
    category: str | None,
    case_name: str | None,
) -> list[Any]:
    cases = list(all_cases)
    if category:
        cases = [case for case in cases if case.category == category]
    if case_name:
        cases = [case for case in cases if case.name == case_name]
    return cases


def majority_passed(pass_count: int, n_runs: int, threshold: float) -> bool:
    return pass_count > n_runs * threshold


def summarize_by_category(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    by_category: dict[str, dict[str, int]] = {}
    for result in results:
        category = result["category"]
        if category not in by_category:
            by_category[category] = {"passed": 0, "total": 0}
        by_category[category]["total"] += 1
        if result["passed"]:
            by_category[category]["passed"] += 1
    return by_category


def default_phase_summary(
    results: list[dict[str, Any]],
    total_cases: int,
) -> dict[str, Any]:
    total_passed = sum(1 for result in results if result["passed"])
    run_outcomes = summarize_run_outcomes(results)
    return {
        "total": total_cases,
        "passed": total_passed,
        "pass_rate": round(total_passed / total_cases, 4) if total_cases else 0,
        "by_category": summarize_by_category(results),
        "dimension_pass_counts": dimension_pass_counts(results),
        "stability": stability_summary(results),
        **run_outcomes,
    }


def is_llama_timeout(error_message: Any) -> bool:
    return isinstance(error_message, str) and "Timed out connecting to llama.cpp server" in error_message


def is_infra_error_message(error_message: Any) -> bool:
    if not isinstance(error_message, str):
        return False
    lowered = error_message.lower()
    return any(
        fragment in lowered
        for fragment in (
            "timed out connecting to llama.cpp server",
            "failed to start llama.cpp server",
            "endpoint unavailable",
            "connection refused",
            "server disconnected",
            "connection reset",
            "remote end closed connection",
            "service unavailable",
        )
    )


_INFRA_ERROR_TYPES = frozenset(
    {"connect_timeout", "backend_startup_failure", "endpoint_unavailable", "server_disconnect"}
)


def classify_infra_error(error_message: Any) -> str | None:
    if not isinstance(error_message, str):
        return None
    lowered = error_message.lower()
    if "timed out connecting to llama.cpp server" in lowered:
        return "connect_timeout"
    if "failed to start llama.cpp server" in lowered:
        return "backend_startup_failure"
    if "endpoint unavailable" in lowered or "service unavailable" in lowered:
        return "endpoint_unavailable"
    if "connection refused" in lowered:
        return "endpoint_unavailable"
    if "server disconnected" in lowered or "connection reset" in lowered:
        return "server_disconnect"
    if "remote end closed connection" in lowered:
        return "server_disconnect"
    return None


def run_result_is_infra_failure(run_result: dict[str, Any]) -> bool:
    if is_infra_error_message(run_result.get("error")):
        return True

    if run_result.get("error_type") in _INFRA_ERROR_TYPES:
        return True

    tools_called = run_result.get("tools_called")
    requested_tool_calls = run_result.get("requested_tool_calls")
    executed_tool_calls = run_result.get("executed_tool_calls")
    if tools_called or requested_tool_calls or executed_tool_calls:
        return False

    for turn_result in run_result.get("turn_results", []):
        if turn_result.get("tools_called") or turn_result.get("requested_tool_calls"):
            continue
        if is_infra_error_message(turn_result.get("error")):
            return True
    return False


def format_run_status_for_cli(run_result: dict[str, Any]) -> str:
    """Render run status for CLI without ambiguous failure banners."""
    status = run_result.get("status") or derive_run_status(run_result)
    if status == RUN_STATUS_INFRA_FAIL:
        error_type = classify_infra_error(run_result.get("error")) or "infra_error"
        return f"{RUN_STATUS_INFRA_BANNER} ({error_type})"
    return str(status)


def derive_run_status(run_result: dict[str, Any]) -> str:
    if run_result_is_infra_failure(run_result):
        return RUN_STATUS_INFRA_FAIL
    if "matched" in run_result:
        return RUN_STATUS_PASS if run_result["matched"] else RUN_STATUS_FAIL
    if "sequence_matched" in run_result:
        return RUN_STATUS_PASS if run_result["sequence_matched"] else RUN_STATUS_FAIL
    if "all_turns_passed" in run_result:
        return RUN_STATUS_PASS if run_result["all_turns_passed"] else RUN_STATUS_FAIL
    if "passed" in run_result:
        return RUN_STATUS_PASS if run_result["passed"] else RUN_STATUS_FAIL
    return RUN_STATUS_FAIL


def summarize_run_outcomes(results: list[dict[str, Any]]) -> dict[str, Any]:
    total_scheduled_runs = 0
    model_attempts = 0
    model_passes = 0
    infra_failures = 0

    for result in results:
        for run in result.get("runs", []):
            total_scheduled_runs += 1
            status = run.get("status") or derive_run_status(run)
            if status == RUN_STATUS_INFRA_FAIL:
                infra_failures += 1
                continue
            model_attempts += 1
            if status == RUN_STATUS_PASS:
                model_passes += 1

    return {
        "model_attempts": model_attempts,
        "model_passes": model_passes,
        "infra_failures": infra_failures,
        "total_scheduled_runs": total_scheduled_runs,
        "model_pass_rate": round(model_passes / model_attempts, 4)
        if model_attempts
        else None,
        "complete": infra_failures == 0,
    }


def case_run_stability(
    runs: list[dict[str, Any]],
    *,
    threshold: float = 1.0,
) -> dict[str, Any]:
    """Report repeat-run consistency for one case without changing majority gating."""
    total_scheduled_runs = len(runs)
    model_attempts = 0
    model_passes = 0
    infra_failures = 0
    status_counts = {
        RUN_STATUS_PASS: 0,
        RUN_STATUS_FAIL: 0,
        RUN_STATUS_INFRA_FAIL: 0,
    }

    for run in runs:
        status = run.get("status") or derive_run_status(run)
        if status in status_counts:
            status_counts[status] += 1
        if status == RUN_STATUS_INFRA_FAIL:
            infra_failures += 1
            continue
        model_attempts += 1
        if status == RUN_STATUS_PASS:
            model_passes += 1

    model_pass_rate = (
        round(model_passes / model_attempts, 4) if model_attempts else None
    )
    stable = (
        total_scheduled_runs > 0
        and infra_failures == 0
        and model_attempts == total_scheduled_runs
        and model_pass_rate is not None
        and model_pass_rate >= threshold
    )

    return {
        "threshold": threshold,
        "stable": stable,
        "total_scheduled_runs": total_scheduled_runs,
        "model_attempts": model_attempts,
        "model_passes": model_passes,
        "model_failures": model_attempts - model_passes,
        "infra_failures": infra_failures,
        "model_pass_rate": model_pass_rate,
        "status_counts": status_counts,
    }


def stability_summary(
    results: list[dict[str, Any]],
    *,
    threshold: float = 1.0,
) -> dict[str, Any]:
    """Summarize repeat-run stability across cases."""
    unstable_cases: list[str] = []
    stable_cases = 0
    case_summaries: dict[str, dict[str, Any]] = {}

    for result in results:
        case_name = str(result.get("name") or "<unknown>")
        category = str(result.get("category") or "<unknown>")
        key = f"{category}/{case_name}"
        stability = case_run_stability(result.get("runs", []), threshold=threshold)
        case_summaries[key] = stability
        if stability["stable"]:
            stable_cases += 1
        else:
            unstable_cases.append(key)

    return {
        "threshold": threshold,
        "stable_cases": stable_cases,
        "total_cases": len(results),
        "unstable_cases": unstable_cases,
        "release_stable": len(results) > 0 and not unstable_cases,
        "cases": case_summaries,
    }


def load_run_store(results_path: str | Path) -> dict[str, Any]:
    path = Path(results_path)
    if not path.exists():
        return {"version": 1, "runs": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"version": 1, "runs": []}
    runs = data.get("runs")
    if not isinstance(runs, list):
        data["runs"] = []
    return data


def write_run_store(results_path: str | Path, store: dict[str, Any]) -> None:
    path = Path(results_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(store, indent=2, sort_keys=False), encoding="utf-8")


def upsert_run_store_entry(results_path: str | Path, entry: dict[str, Any]) -> None:
    store = load_run_store(results_path)
    runs = store.setdefault("runs", [])
    for index, existing in enumerate(runs):
        if (
            existing.get("phase") == entry.get("phase")
            and existing.get("case_name") == entry.get("case_name")
            and existing.get("run_index") == entry.get("run_index")
        ):
            runs[index] = entry
            write_run_store(results_path, store)
            return
    runs.append(entry)
    write_run_store(results_path, store)


def persisted_phase_runs(
    results_path: str | Path,
    *,
    phase: int,
) -> dict[tuple[str, int], dict[str, Any]]:
    store = load_run_store(results_path)
    cached: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in store.get("runs", []):
        if entry.get("phase") != phase:
            continue
        case_name = entry.get("case_name")
        run_index = entry.get("run_index")
        if isinstance(case_name, str) and isinstance(run_index, int):
            cached[(case_name, run_index)] = entry
    return cached


def build_persisted_run_entry(
    *,
    phase: int,
    case: Any,
    run_index: int,
    run_result: dict[str, Any],
    backend_restart_count: int,
    model_alias: str | None = None,
    backend_metadata: dict[str, Any] | None = None,
    mvp_tool_profile: bool = False,
) -> dict[str, Any]:
    status = run_result.get("status") or derive_run_status(run_result)
    prompt = getattr(case, "prompt", None)
    expected_chain: Any = None
    if hasattr(case, "expected_tool"):
        expected_chain = [case.expected_tool]
    elif hasattr(case, "expected_tools"):
        expected_chain = list(case.expected_tools)
    elif hasattr(case, "expected_tool_sequence"):
        expected_chain = list(case.expected_tool_sequence)
    elif hasattr(case, "turns"):
        expected_chain = [
            {
                "turn_index": index,
                "prompt": turn.prompt,
                "expected_tools": list(
                    getattr(
                        turn,
                        "expected_tools_in_order",
                        getattr(turn, "expected_tools", []),
                    )
                ),
            }
            for index, turn in enumerate(case.turns)
        ]

    turn_results = run_result.get("turn_results")
    return {
        "phase": phase,
        "category": case.category,
        "case_name": case.name,
        "run_index": run_index,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "error_type": run_result.get("error_type")
        or classify_infra_error(run_result.get("error")),
        "backend_restart_count": backend_restart_count,
        "prompt": prompt,
        "expected_chain": expected_chain,
        "actual_chain": run_result.get("tools_called"),
        "turn_results": turn_results,
        "release_metadata": collect_release_metadata(
            case=case,
            model_alias=model_alias,
            backend_metadata=backend_metadata,
            mvp_tool_profile=mvp_tool_profile,
        ),
        "run_result": run_result,
    }


def should_reuse_persisted_run(
    entry: dict[str, Any],
    *,
    resume: bool,
    rerun_failed: bool,
) -> bool:
    if not resume:
        return False
    status = entry.get("status")
    if rerun_failed:
        return status == RUN_STATUS_PASS
    return status in {RUN_STATUS_PASS, RUN_STATUS_FAIL, RUN_STATUS_INFRA_FAIL}


def run_phase_eval(
    *,
    phase: int,
    server_url: str,
    model: str,
    cases: list[Any],
    n_runs: int,
    majority_threshold: float,
    run_case: CaseRunner,
    build_case_report: CaseReportBuilder,
    render_status: StatusRenderer,
    build_summary: SummaryBuilder = default_phase_summary,
    retry_on_timeout: bool = False,
    results_path: str | Path | None = None,
    resume: bool = False,
    rerun_failed: bool = False,
    max_tokens: int | None = DEFAULT_LIVE_EVAL_MAX_TOKENS,
    stability_threshold: float = 1.0,
    mvp_tool_profile: bool = False,
) -> dict[str, Any]:
    config = load_app_config()
    resolved_url = (server_url or config.llama.server_url).rstrip("/")
    resolved_model = model or config.llama.model
    temperature = config.llama.temperature
    client: ToolAgentsLlamaProviderConfig | None = None

    cached_runs = (
        persisted_phase_runs(results_path, phase=phase)
        if results_path is not None
        else {}
    )

    def safe_run_case(active_client: ToolAgentsLlamaProviderConfig, active_model: str, case: Any) -> dict[str, Any]:
        try:
            return run_case(active_client, active_model, case)
        except Exception as exc:
            error_message = str(exc)
            if not is_infra_error_message(error_message):
                raise
            return {
                "tools_called": [],
                "requested_tool_calls": [],
                "executed_tool_calls": [],
                "error": error_message,
                "elapsed_seconds": None,
            }

    results = []
    total = len(cases) * n_runs
    done = 0

    for case in cases:
        runs = []
        for run_index in range(n_runs):
            done += 1
            print(
                f"[{done}/{total}] {case.category}/{case.name} run {run_index + 1}/{n_runs}",
                end="",
                flush=True,
            )
            cached_entry = cached_runs.get((case.name, run_index))
            if cached_entry is not None and should_reuse_persisted_run(
                cached_entry,
                resume=resume,
                rerun_failed=rerun_failed,
            ):
                run_result = cached_entry["run_result"]
                run_result.setdefault("status", cached_entry.get("status"))
                run_result.setdefault(
                    "backend_restart_count",
                    cached_entry.get("backend_restart_count", 0),
                )
                print(f" -> cached {run_result.get('status', derive_run_status(run_result))}")
                runs.append(run_result)
                continue

            backend_restart_count = 0

            if client is None:
                try:
                    resolved_url, resolved_model, client = ensure_llama_server(
                        resolved_url,
                        resolved_model,
                    )
                    apply_live_generation_bounds(client, max_tokens=max_tokens)
                    temperature = client.temperature
                except LlamaLauncherError:
                    try:
                        resolved_url, resolved_model, client = restart_llama_server(
                            resolved_url,
                            resolved_model,
                        )
                        apply_live_generation_bounds(client, max_tokens=max_tokens)
                        temperature = client.temperature
                        backend_restart_count = 1
                    except LlamaLauncherError as exc:
                        run_result = {
                            "tools_called": [],
                            "requested_tool_calls": [],
                            "executed_tool_calls": [],
                            "error": str(exc),
                            "elapsed_seconds": None,
                            "status": RUN_STATUS_INFRA_FAIL,
                            "error_type": classify_infra_error(str(exc))
                            or "backend_startup_failure",
                            "backend_restart_count": 1,
                        }
                        if results_path is not None:
                            upsert_run_store_entry(
                                results_path,
                                build_persisted_run_entry(
                                    phase=phase,
                                    case=case,
                                    run_index=run_index,
                                    run_result=run_result,
                                    backend_restart_count=1,
                                    model_alias=resolved_model,
                                    mvp_tool_profile=mvp_tool_profile,
                                ),
                            )
                        print(f" -> {format_run_status_for_cli(run_result)}")
                        runs.append(run_result)
                        continue

            run_result = safe_run_case(client, resolved_model, case)
            if (
                (retry_on_timeout and is_llama_timeout(run_result.get("error")))
                or run_result_is_infra_failure(run_result)
            ) and backend_restart_count == 0:
                try:
                    resolved_url, resolved_model, client = restart_llama_server(
                        resolved_url,
                        resolved_model,
                    )
                    apply_live_generation_bounds(client, max_tokens=max_tokens)
                    temperature = client.temperature
                    backend_restart_count = 1
                    run_result = safe_run_case(client, resolved_model, case)
                except LlamaLauncherError as exc:
                    run_result = {
                        "tools_called": [],
                        "requested_tool_calls": [],
                        "executed_tool_calls": [],
                        "error": str(exc),
                        "elapsed_seconds": None,
                        "status": RUN_STATUS_INFRA_FAIL,
                        "error_type": classify_infra_error(str(exc))
                        or "backend_startup_failure",
                    }

            if run_result_is_infra_failure(run_result):
                run_result["status"] = RUN_STATUS_INFRA_FAIL
                run_result["error_type"] = classify_infra_error(run_result.get("error"))
            else:
                run_result["status"] = derive_run_status(run_result)
            run_result["backend_restart_count"] = backend_restart_count

            if results_path is not None:
                upsert_run_store_entry(
                    results_path,
                    build_persisted_run_entry(
                        phase=phase,
                        case=case,
                        run_index=run_index,
                        run_result=run_result,
                        backend_restart_count=backend_restart_count,
                        model_alias=resolved_model,
                        backend_metadata=collect_backend_metadata(
                            client,
                            server_url=resolved_url,
                            model=resolved_model,
                            temperature=temperature,
                        ),
                        mvp_tool_profile=mvp_tool_profile,
                    ),
                )

            if run_result.get("status") == RUN_STATUS_INFRA_FAIL:
                print(f" -> {format_run_status_for_cli(run_result)}")
            else:
                print(f" -> {render_status(case, run_result)}")
            runs.append(run_result)

        case_report = build_case_report(case, runs, n_runs, majority_threshold)
        case_report["stability"] = case_run_stability(
            runs,
            threshold=stability_threshold,
        )
        results.append(case_report)

    summary = build_summary(results, len(cases))
    summary["stability"] = stability_summary(results, threshold=stability_threshold)

    return {
        "phase": phase,
        "model": resolved_model,
        "temperature": temperature,
        "backend": collect_backend_metadata(
            client,
            server_url=resolved_url,
            model=resolved_model,
            temperature=temperature,
        ),
        "n_runs": n_runs,
        "majority_threshold": majority_threshold,
        "stability_threshold": stability_threshold,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cases": results,
        "summary": summary,
    }



def extract_requested_tool_calls(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return normalized assistant-requested tool calls from chat history."""
    results = []
    for turn in history:
        if turn.get("role") != "assistant":
            continue
        raw_tool_calls = turn.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            continue
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, dict):
                continue
            function_payload = raw_call.get("function")
            if isinstance(function_payload, dict):
                name = function_payload.get("name")
                arguments = function_payload.get("arguments")
            else:
                name = raw_call.get("name")
                arguments = raw_call.get("arguments")
            results.append(
                {
                    "name": name,
                    "arguments": _parse_tool_arguments(arguments),
                }
            )
    return results


def extract_executed_tool_calls(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return executed tool results from chat history."""
    return [
        {
            "name": turn.get("name"),
            "arguments": turn.get("content"),
        }
        for turn in history
        if turn.get("role") == "tool"
    ]


def tools_appear_in_expected_order(
    actual_tool_names: list[str], expected_tool_names: list[str]
) -> bool:
    """Return whether expected tools appear in order without later expected tools arriving early."""
    if not expected_tool_names:
        return not actual_tool_names
    expected_index = 0
    for actual_tool_name in actual_tool_names:
        if expected_index >= len(expected_tool_names):
            break
        current_expected_tool = expected_tool_names[expected_index]
        if actual_tool_name == current_expected_tool:
            expected_index += 1
            continue
        if actual_tool_name in expected_tool_names[expected_index + 1 :]:
            return False
    return expected_index == len(expected_tool_names)


def tool_call_matches_transaction_checks(
    tool_call: dict[str, Any],
    expected_operations: list[dict[str, Any]],
    *,
    ordered: bool = True,
) -> bool:
    """Return whether the tool-call transaction matches the expected operations."""
    actual_operations = normalize_transaction_operations(tool_call.get("arguments"))
    if not actual_operations:
        return False
    if ordered:
        actual_index = 0
        for expected_operation in expected_operations:
            while actual_index < len(actual_operations):
                if _partial_match(actual_operations[actual_index], expected_operation):
                    actual_index += 1
                    break
                actual_index += 1
            else:
                return False
        return True
    return all(
        any(
            _partial_match(actual_operation, expected_operation)
            for actual_operation in actual_operations
        )
        for expected_operation in expected_operations
    )


def tool_call_matches_argument_checks(
    tool_call: dict[str, Any], expected_arguments: dict[str, Any]
) -> bool:
    """Return whether the raw tool-call arguments match a partial expectation."""
    return _partial_match(tool_call.get("arguments"), expected_arguments)


def _tool_names(tool_calls: list[dict[str, Any]]) -> list[str]:
    return [str(call.get("name")) for call in tool_calls if call.get("name")]


def _tool_result_ok(tool_call: dict[str, Any]) -> bool:
    payload = tool_call.get("arguments")
    return isinstance(payload, dict) and payload.get("ok") is True


def _requested_calls_match_expectations(
    requested_tool_calls: list[dict[str, Any]],
    expected_tool_calls: tuple[ToolExpectation, ...],
) -> bool:
    if not expected_tool_calls:
        return not requested_tool_calls

    start_index = 0
    for expectation in expected_tool_calls:
        for index in range(start_index, len(requested_tool_calls)):
            call = requested_tool_calls[index]
            if call.get("name") != expectation.name:
                continue
            if not _requested_call_matches_expectation(call, expectation):
                continue
            start_index = index + 1
            break
        else:
            return False
    return True


def _requested_call_matches_expectation(
    call: dict[str, Any],
    expectation: ToolExpectation,
) -> bool:
    if expectation.arguments and not tool_call_matches_argument_checks(
        call,
        expectation.arguments,
    ):
        return False
    if expectation.transaction_operations and not tool_call_matches_transaction_checks(
        call,
        list(expectation.transaction_operations),
        ordered=expectation.ordered_transaction,
    ):
        return False
    return True


def _executed_calls_match_expectations(
    executed_tool_calls: list[dict[str, Any]],
    expected_tool_calls: tuple[ToolExpectation, ...],
) -> bool:
    if not expected_tool_calls:
        return not executed_tool_calls

    start_index = 0
    for expectation in expected_tool_calls:
        for index in range(start_index, len(executed_tool_calls)):
            call = executed_tool_calls[index]
            if call.get("name") != expectation.name:
                continue
            if expectation.require_result_ok and not _tool_result_ok(call):
                continue
            start_index = index + 1
            break
        else:
            return False
    return True


def normalize_transaction_operations(arguments: Any) -> list[dict[str, Any]]:
    """Normalize one tool-call argument payload into an ordered transaction list."""
    if not isinstance(arguments, dict):
        return []
    normalized_operations = arguments.get("normalized_operations")
    if isinstance(normalized_operations, list) and all(
        isinstance(item, dict) for item in normalized_operations
    ):
        operations = list(normalized_operations)
    else:
        transaction = arguments.get("transaction", arguments)
        if isinstance(transaction, dict):
            operations = [transaction]
        elif isinstance(transaction, list) and all(
            isinstance(item, dict) for item in transaction
        ):
            operations = list(transaction)
        else:
            return []

    normalized_operations: list[dict[str, Any]] = []
    for operation in operations:
        normalized_operation = dict(operation)
        if (
            normalized_operation.get("op_type") == "remove_connection"
            and "connection_id" in normalized_operation
        ):
            parsed = parse_connection_id(normalized_operation.get("connection_id"))
            if parsed is not None:
                src_block, src_port, dst_block, dst_port = parsed
                normalized_operation.setdefault("src_block", src_block)
                normalized_operation.setdefault("src_port", src_port)
                normalized_operation.setdefault("dst_block", dst_block)
                normalized_operation.setdefault("dst_port", dst_port)
        normalized_operations.append(normalized_operation)
    return normalized_operations


def text_contains_any(text: str, needles: list[str]) -> bool:
    """Return whether any expected lowercase fragment appears in the text."""
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def render_prompt(prompt: str, target_path: str, save_path: str) -> str:
    return prompt.format(target_path=target_path, save_path=save_path)


def render_value_templates(value: Any, *, target_path: str, save_path: str) -> Any:
    if isinstance(value, str):
        return value.format_map(
            _TemplateValues(target_path=target_path, save_path=save_path)
        )
    if isinstance(value, dict):
        return {
            key: render_value_templates(
                nested_value, target_path=target_path, save_path=save_path
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [
            render_value_templates(item, target_path=target_path, save_path=save_path)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            render_value_templates(item, target_path=target_path, save_path=save_path)
            for item in value
        )
    return value


class _TemplateValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_tool_expectations(
    expectations: tuple[ToolExpectation, ...],
    *,
    target_path: str,
    save_path: str,
) -> tuple[ToolExpectation, ...]:
    return tuple(
        ToolExpectation(
            name=expectation.name,
            arguments=render_value_templates(
                expectation.arguments,
                target_path=target_path,
                save_path=save_path,
            ),
            transaction_operations=tuple(
                render_value_templates(
                    operation,
                    target_path=target_path,
                    save_path=save_path,
                )
                for operation in expectation.transaction_operations
            ),
            ordered_transaction=expectation.ordered_transaction,
            require_result_ok=expectation.require_result_ok,
        )
        for expectation in expectations
    )


def requested_tool_calls_since(
    history: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    return extract_requested_tool_calls(history[start_index:])


def executed_tool_calls_since(
    history: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    return extract_executed_tool_calls(history[start_index:])


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _partial_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _partial_match(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _partial_match(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected)
        )
    return actual == expected or str(actual) == str(expected)
